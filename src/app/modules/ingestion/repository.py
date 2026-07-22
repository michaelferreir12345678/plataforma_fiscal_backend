"""Acesso a dados do medallion. Implementa o ``MedallionSink`` e as leituras bitemporais."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.ingestion.models import (
    DimEntrega,
    IngestionLog,
    RawPayload,
)
from app.shared.ingestion.base import EntregaInfo, IngestionJob


class MedallionRepository:
    """Implementação concreta do ``MedallionSink`` (bronze + gold.dim_entrega + log)."""

    def upsert_bronze(
        self, session: Session, job: IngestionJob, payload: Any, hash_: str
    ) -> bool:
        """Grava o payload cru (idempotente por chave). Retorna ``True`` se inseriu."""
        stmt = (
            pg_insert(RawPayload)
            .values(
                fonte=job.fonte,
                cod_ibge=job.cod_ibge,
                periodo=job.periodo,
                versao=job.versao,
                ano=job.ano,
                hash_payload=hash_,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=["fonte", "cod_ibge", "periodo", "versao"])
        )
        result = session.execute(stmt)
        return bool(result.rowcount == 1)

    def register_entrega(
        self, session: Session, job: IngestionJob, hash_: str
    ) -> EntregaInfo:
        """Registra a entrega em gold.dim_entrega. Retificação mais recente supera a vigente."""
        chave = (
            DimEntrega.cod_ibge == job.cod_ibge,
            DimEntrega.relatorio == job.relatorio,
            DimEntrega.periodo == job.periodo,
        )
        existing = session.scalar(
            select(DimEntrega).where(*chave, DimEntrega.versao_entrega == job.versao)
        )
        if existing is not None:
            if existing.hash_payload is None:
                existing.hash_payload = hash_
            return EntregaInfo(existing.versao_entrega, existing.vigente)

        homologada = job.homologada_em or datetime.now(UTC)
        max_homologada = session.scalar(select(func.max(DimEntrega.homologada_em)).where(*chave))
        # Timezone-safe: compara em UTC.
        becomes_vigente = max_homologada is None or homologada >= max_homologada
        if becomes_vigente:
            session.execute(update(DimEntrega).where(*chave).values(vigente=False))

        entrega = DimEntrega(
            cod_ibge=job.cod_ibge,
            relatorio=job.relatorio,
            periodo=job.periodo,
            versao_entrega=job.versao,
            homologada_em=homologada,
            vigente=becomes_vigente,
            hash_payload=hash_,
        )
        session.add(entrega)
        session.flush()
        return EntregaInfo(job.versao, becomes_vigente)

    def log_ingestion(
        self, session: Session, job: IngestionJob, status: str, mensagem: str | None = None
    ) -> None:
        session.add(
            IngestionLog(
                fonte=job.fonte,
                cod_ibge=job.cod_ibge,
                periodo=job.periodo,
                versao=job.versao,
                status=status,
                mensagem=mensagem,
            )
        )
        session.flush()


# --- Leituras bitemporais (as_of) e materialização silver ---
def resolve_versao(
    session: Session,
    *,
    cod_ibge: str,
    relatorio: str,
    periodo: str,
    as_of: datetime | None = None,
) -> str | None:
    """Resolve a ``versao_entrega`` efetiva (§6.5).

    Sem ``as_of``, retorna a versão **vigente**; com ``as_of``, a que estava vigente
    naquele instante (maior ``homologada_em`` ≤ ``as_of``).
    """
    chave = (
        DimEntrega.cod_ibge == cod_ibge,
        DimEntrega.relatorio == relatorio,
        DimEntrega.periodo == periodo,
    )
    if as_of is None:
        return session.scalar(
            select(DimEntrega.versao_entrega).where(*chave, DimEntrega.vigente.is_(True))
        )
    return session.scalar(
        select(DimEntrega.versao_entrega)
        .where(*chave, DimEntrega.homologada_em <= as_of)
        .order_by(DimEntrega.homologada_em.desc())
        .limit(1)
    )


def entrega_homologada_em(
    session: Session,
    *,
    cod_ibge: str,
    relatorio: str,
    periodo: str,
    versao_entrega: str,
) -> datetime | None:
    """Instante efetivo da entrega usada — ancora o ``as_of`` quando a query o omite."""
    return session.scalar(
        select(DimEntrega.homologada_em).where(
            DimEntrega.cod_ibge == cod_ibge,
            DimEntrega.relatorio == relatorio,
            DimEntrega.periodo == periodo,
            DimEntrega.versao_entrega == versao_entrega,
        )
    )


def resolve_latest_entrega(
    session: Session,
    *,
    cod_ibge: str,
    relatorio: str,
    as_of: datetime | None = None,
) -> DimEntrega | None:
    """Entrega mais recente de uma fonte de fotografia (ex.: SADIPEM)."""
    stmt = select(DimEntrega).where(
        DimEntrega.cod_ibge == cod_ibge,
        DimEntrega.relatorio == relatorio,
    )
    if as_of is None:
        stmt = stmt.where(DimEntrega.vigente.is_(True))
    else:
        stmt = stmt.where(DimEntrega.homologada_em <= as_of)
    return session.scalar(
        stmt.order_by(DimEntrega.periodo.desc(), DimEntrega.homologada_em.desc()).limit(1)
    )


def replace_silver_rows(
    session: Session,
    model: Any,
    *,
    keys: dict[str, Any],
    rows: list[dict[str, Any]],
) -> int:
    """Substitui as linhas silver que casam com ``keys`` (idempotente por reprocesso).

    ``keys`` são as colunas que identificam a versão a substituir (ex.:
    ``{"cod_ibge": ..., "periodo": ..., "versao_entrega": ...}`` para SICONFI, ou
    ``{"codigo_serie": ..., "versao_entrega": ...}`` para o BCB). Retorna a contagem.
    """
    session.execute(
        delete(model).where(
            *[getattr(model, col) == value for col, value in keys.items()]
        )
    )
    if rows:
        has_id = "id" in model.__table__.columns
        payload = [
            {"id": uuid.uuid4(), **row} if has_id and "id" not in row else row for row in rows
        ]
        session.execute(insert(model), payload)
    return len(rows)


def read_silver(
    session: Session,
    model: Any,
    *,
    cod_ibge: str,
    periodo: str,
    versao_entrega: str,
) -> list[Any]:
    return list(
        session.scalars(
            select(model).where(
                model.cod_ibge == cod_ibge,
                model.periodo == periodo,
                model.versao_entrega == versao_entrega,
            )
        )
    )


def list_bronze(
    session: Session, *, fonte: str, cod_ibge: str, periodo: str
) -> list[RawPayload]:
    """Payloads crus de (fonte, ente, período) — todas as versões (para replay)."""
    return list(
        session.scalars(
            select(RawPayload)
            .where(
                RawPayload.fonte == fonte,
                RawPayload.cod_ibge == cod_ibge,
                RawPayload.periodo == periodo,
            )
            .order_by(RawPayload.versao)
        )
    )


def list_entregas(
    session: Session, *, relatorio: str | None = None
) -> list[DimEntrega]:
    stmt = select(DimEntrega)
    if relatorio is not None:
        stmt = stmt.where(DimEntrega.relatorio == relatorio)
    stmt = stmt.order_by(
        DimEntrega.cod_ibge, DimEntrega.relatorio, DimEntrega.periodo, DimEntrega.homologada_em
    )
    return list(session.scalars(stmt))
