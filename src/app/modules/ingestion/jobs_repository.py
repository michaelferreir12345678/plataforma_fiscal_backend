"""Acesso a dados dos jobs de ingestão + varredura de retificações (Sprint 24)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.ingestion.jobs_models import (
    STATUS_CANCELADO,
    STATUS_EXECUTANDO,
    STATUS_FALHOU,
    STATUS_NA_FILA,
    IngestJob,
)
from app.modules.ingestion.jobs_schemas import RetificacaoItem
from app.modules.ingestion.models import DimEntrega, IngestionLog


def create_job(session: Session, valores: dict[str, Any]) -> IngestJob:
    job = IngestJob(**valores)
    session.add(job)
    session.flush()
    session.refresh(job)
    return job


def get_job(session: Session, job_id: uuid.UUID) -> IngestJob | None:
    return session.get(IngestJob, job_id)


def _refresh_updated_job(session: Session, job_id: uuid.UUID) -> IngestJob:
    job = session.get(IngestJob, job_id, populate_existing=True)
    assert job is not None
    return job


def claim_job(session: Session, job_id: uuid.UUID, *, iniciado_em: datetime) -> IngestJob | None:
    """Faz ``na_fila → executando`` em um único UPDATE condicional.

    O predicado no próprio UPDATE é a trava de concorrência: entre cancelamento, retry
    duplicado e múltiplas entregas do Redis, somente uma transação consegue assumir o job.
    """
    claimed = session.scalar(
        update(IngestJob)
        .where(IngestJob.id == job_id, IngestJob.status == STATUS_NA_FILA)
        .values(
            status=STATUS_EXECUTANDO,
            iniciado_em=iniciado_em,
            terminado_em=None,
            tentativas=IngestJob.tentativas + 1,
        )
        .returning(IngestJob.id)
    )
    if claimed is None:
        return None
    return _refresh_updated_job(session, claimed)


def cancel_job(session: Session, job_id: uuid.UUID, *, terminado_em: datetime) -> IngestJob | None:
    """Faz ``na_fila → cancelado`` atomicamente; ``None`` significa que perdeu a corrida."""
    cancelled = session.scalar(
        update(IngestJob)
        .where(IngestJob.id == job_id, IngestJob.status == STATUS_NA_FILA)
        .values(status=STATUS_CANCELADO, terminado_em=terminado_em)
        .returning(IngestJob.id)
    )
    if cancelled is None:
        return None
    return _refresh_updated_job(session, cancelled)


def retry_job(
    session: Session, job_id: uuid.UUID, *, itens_total: int
) -> IngestJob | None:
    """Faz ``falhou → na_fila`` atomicamente e zera o progresso da nova tentativa."""
    retried = session.scalar(
        update(IngestJob)
        .where(IngestJob.id == job_id, IngestJob.status == STATUS_FALHOU)
        .values(
            status=STATUS_NA_FILA,
            progresso_pct=0,
            itens_total=itens_total,
            itens_ok=0,
            itens_erro=0,
            erro_resumo=None,
            terminado_em=None,
        )
        .returning(IngestJob.id)
    )
    if retried is None:
        return None
    return _refresh_updated_job(session, retried)


def add_log(
    session: Session,
    *,
    job_id: uuid.UUID,
    fonte: str,
    status: str,
    cod_ibge: str | None = None,
    periodo: str | None = None,
    versao: str | None = None,
    mensagem: str | None = None,
) -> IngestionLog:
    row = IngestionLog(
        job_id=job_id,
        fonte=fonte,
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao=versao,
        status=status,
        mensagem=mensagem,
    )
    session.add(row)
    session.flush()
    return row


def list_logs(session: Session, job_id: uuid.UUID, *, limit: int = 1000) -> list[IngestionLog]:
    return list(
        session.scalars(
            select(IngestionLog)
            .where(IngestionLog.job_id == job_id)
            .order_by(IngestionLog.ts, IngestionLog.id)
            .limit(limit)
        )
    )


def list_jobs(
    session: Session, *, status: str | None = None, fonte: str | None = None, limit: int = 100
) -> list[IngestJob]:
    stmt = select(IngestJob)
    if status is not None:
        stmt = stmt.where(IngestJob.status == status)
    if fonte is not None:
        stmt = stmt.where(IngestJob.fonte == fonte)
    stmt = stmt.order_by(IngestJob.criado_em.desc()).limit(limit)
    return list(session.scalars(stmt))


def list_queued_jobs(session: Session, *, limit: int = 10_000) -> list[IngestJob]:
    """Jobs duráveis que precisam ser (re)entregues ao Redis após um restart."""
    return list(
        session.scalars(
            select(IngestJob)
            .where(IngestJob.status == STATUS_NA_FILA)
            .order_by(IngestJob.criado_em, IngestJob.id)
            .limit(limit)
        )
    )


def lock_active_job(session: Session, job_id: uuid.UUID) -> IngestJob | None:
    """Trava uma entrega ainda ativa para callback/reconciliação idempotentes."""
    return session.scalar(
        select(IngestJob)
        .where(
            IngestJob.id == job_id,
            IngestJob.status.in_((STATUS_NA_FILA, STATUS_EXECUTANDO)),
        )
        .with_for_update()
    )


def lock_queued_job(session: Session, job_id: uuid.UUID) -> IngestJob | None:
    """Trava somente ``na_fila`` para falha de transporte sem vencer o claim."""
    return session.scalar(
        select(IngestJob)
        .where(IngestJob.id == job_id, IngestJob.status == STATUS_NA_FILA)
        .with_for_update()
    )


def requeue_abandoned_jobs(
    session: Session,
    *,
    cutoff: datetime,
    limit: int = 1000,
) -> list[IngestJob]:
    """Trava e devolve a ``na_fila`` execuções cujo lease temporal expirou.

    ``SKIP LOCKED`` permite que vários workers façam manutenção ao mesmo tempo sem
    reentregar a mesma execução. Checkpoints, contadores e ``tentativas`` são
    deliberadamente preservados; o novo claim incrementará a tentativa.
    """
    jobs = list(
        session.scalars(
            select(IngestJob)
            .where(
                IngestJob.status == STATUS_EXECUTANDO,
                IngestJob.iniciado_em.is_not(None),
                IngestJob.iniciado_em < cutoff,
            )
            .order_by(IngestJob.iniciado_em, IngestJob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for job in jobs:
        job.status = STATUS_NA_FILA
        job.terminado_em = None
    session.flush()
    return jobs


def retificacoes(
    session: Session, *, desde: datetime | None = None, limit: int = 200
) -> list[RetificacaoItem]:
    """Entregas que superaram uma versão anterior (mais de uma entrega no mesmo período).

    A retificação **supera** a versão anterior sem apagá-la (§6.5). Aqui listamos, por
    (ente, relatório, período) com mais de uma versão, a entrega **vigente** e quantas
    versões a antecederam.
    """
    counts = (
        select(
            DimEntrega.cod_ibge.label("cod_ibge"),
            DimEntrega.relatorio.label("relatorio"),
            DimEntrega.periodo.label("periodo"),
            func.count(func.distinct(DimEntrega.versao_entrega)).label("n_versoes"),
        )
        .group_by(DimEntrega.cod_ibge, DimEntrega.relatorio, DimEntrega.periodo)
        .having(func.count(func.distinct(DimEntrega.versao_entrega)) > 1)
        .subquery()
    )
    stmt = (
        select(
            DimEntrega.cod_ibge,
            DimEntrega.relatorio,
            DimEntrega.periodo,
            DimEntrega.versao_entrega,
            DimEntrega.homologada_em,
            counts.c.n_versoes,
        )
        .join(
            counts,
            (DimEntrega.cod_ibge == counts.c.cod_ibge)
            & (DimEntrega.relatorio == counts.c.relatorio)
            & (DimEntrega.periodo == counts.c.periodo),
        )
        .where(DimEntrega.vigente.is_(True))
    )
    if desde is not None:
        stmt = stmt.where(DimEntrega.homologada_em >= desde)
    stmt = stmt.order_by(DimEntrega.homologada_em.desc()).limit(limit)

    return [
        RetificacaoItem(
            cod_ibge=r.cod_ibge,
            relatorio=r.relatorio,
            periodo=r.periodo,
            versao_entrega=r.versao_entrega,
            homologada_em=r.homologada_em,
            versoes_anteriores=int(r.n_versoes) - 1,
        )
        for r in session.execute(stmt).all()
    ]
