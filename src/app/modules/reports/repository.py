"""Acesso a op.relatorio/agendamento e às materializações gold consumidas."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.alerts.models import CalendarioObrigacao
from app.modules.benchmark.models import DimCoorte, MartBenchmark
from app.modules.catalog.models import DimEnte
from app.modules.debt.models import FatoDivida
from app.modules.health_edu.models import FatoEducacao, FatoSaude
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.personnel.models import FatoPessoal
from app.modules.reports.models import Relatorio, RelatorioAgendamento
from app.modules.result.models import FatoResultado
from app.modules.tenancy.models import Organizacao, Usuario


def insert_relatorios(
    session: Session,
    *,
    org_id: uuid.UUID,
    lote_id: uuid.UUID,
    modelo: str,
    formato: str,
    escopo: str,
    entes: Iterable[str],
    periodo: str,
    as_of: datetime,
    parametros: dict[str, Any],
    criado_por: uuid.UUID | None,
) -> list[Relatorio]:
    rows = [
        Relatorio(
            org_id=org_id,
            lote_id=lote_id,
            modelo=modelo,
            modelo_versao="v1",
            formato=formato,
            escopo=escopo,
            cod_ibge=cod,
            periodo=periodo,
            as_of=as_of,
            status="enfileirado",
            progresso=0,
            parametros=parametros,
            cabecalho={},
            source_refs=[],
            memoria={},
            dados_incompletos=[],
            criado_por=criado_por,
        )
        for cod in entes
    ]
    session.add_all(rows)
    session.flush()
    for row in rows:
        session.refresh(row)
    return rows


def get_relatorio(session: Session, relatorio_id: uuid.UUID) -> Relatorio | None:
    return session.get(Relatorio, relatorio_id)


def get_relatorio_for_update(session: Session, relatorio_id: uuid.UUID) -> Relatorio | None:
    return session.scalar(select(Relatorio).where(Relatorio.id == relatorio_id).with_for_update())


def list_relatorios(session: Session, *, org_id: uuid.UUID, limit: int = 50) -> list[Relatorio]:
    return list(
        session.scalars(
            select(Relatorio)
            .where(Relatorio.org_id == org_id)
            .order_by(Relatorio.criado_em.desc(), Relatorio.cod_ibge)
            .limit(limit)
        )
    )


def count_relatorios(session: Session, *, org_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(Relatorio).where(Relatorio.org_id == org_id)
    return int(session.scalar(stmt) or 0)


def list_lote(session: Session, *, org_id: uuid.UUID, lote_id: uuid.UUID) -> list[Relatorio]:
    return list(
        session.scalars(
            select(Relatorio)
            .where(Relatorio.org_id == org_id, Relatorio.lote_id == lote_id)
            .order_by(Relatorio.cod_ibge)
        )
    )


def list_pending_ids(session: Session) -> list[uuid.UUID]:
    """Jobs interrompidos voltam à fila no startup; estado persistido evita perda silenciosa."""
    rows = list(
        session.scalars(
            select(Relatorio).where(Relatorio.status.in_(("enfileirado", "processando")))
        )
    )
    for row in rows:
        row.status = "enfileirado"
        row.progresso = 0
        row.erro = None
        row.atualizado_em = func.now()
    session.flush()
    return [row.id for row in rows]


def claim_relatorio(session: Session, relatorio_id: uuid.UUID) -> Relatorio | None:
    row = get_relatorio_for_update(session, relatorio_id)
    if row is None or row.status not in {"enfileirado", "falhou"}:
        return None
    row.status = "processando"
    row.progresso = 10
    row.erro = None
    row.atualizado_em = func.now()
    session.flush()
    return row


def mark_success(
    row: Relatorio,
    *,
    cabecalho: dict[str, Any],
    source_refs: list[dict[str, Any]],
    memoria: dict[str, Any],
    dados_incompletos: list[dict[str, Any]],
    arquivo_nome: str,
    arquivo_path: str,
    mime_type: str,
    tamanho_bytes: int,
    conteudo_hash: str,
    gerado_em: datetime,
) -> None:
    row.cabecalho = cabecalho
    row.source_refs = source_refs
    row.memoria = memoria
    row.dados_incompletos = dados_incompletos
    row.arquivo_nome = arquivo_nome
    row.arquivo_path = arquivo_path
    row.mime_type = mime_type
    row.tamanho_bytes = tamanho_bytes
    row.conteudo_hash = conteudo_hash
    row.gerado_em = gerado_em
    row.status = "parcial" if dados_incompletos else "gerado"
    row.progresso = 100
    row.erro = None
    row.atualizado_em = func.now()


def mark_failure(row: Relatorio, erro: str) -> None:
    row.status = "falhou"
    row.progresso = 100
    row.erro = erro[:4000]
    row.atualizado_em = func.now()


def get_org(session: Session, org_id: uuid.UUID) -> Organizacao | None:
    return session.get(Organizacao, org_id)


def get_usuario(session: Session, usuario_id: uuid.UUID | None) -> Usuario | None:
    return session.get(Usuario, usuario_id) if usuario_id else None


def get_ente(session: Session, cod_ibge: str) -> DimEnte | None:
    return session.get(DimEnte, cod_ibge)


def get_rcl(session: Session, *, cod_ibge: str, periodo: str, versao: str) -> FatoRcl | None:
    return session.scalar(
        select(FatoRcl).where(
            FatoRcl.cod_ibge == cod_ibge,
            FatoRcl.periodo_ref == periodo,
            FatoRcl.versao_entrega == versao,
        )
    )


def list_mart_indicadores(session: Session, *, cod_ibge: str, periodo: str) -> list[MartIndicador]:
    return list(
        session.scalars(
            select(MartIndicador)
            .where(MartIndicador.cod_ibge == cod_ibge, MartIndicador.periodo == periodo)
            .order_by(MartIndicador.indicador, MartIndicador.versao_entrega.desc())
        )
    )


def get_pessoal(
    session: Session, *, cod_ibge: str, periodo: str, versao: str
) -> FatoPessoal | None:
    return session.scalar(
        select(FatoPessoal).where(
            FatoPessoal.cod_ibge == cod_ibge,
            FatoPessoal.periodo == periodo,
            FatoPessoal.versao_entrega == versao,
            FatoPessoal.poder_codigo == "ENTE.EXEC",
        )
    )


def get_divida(session: Session, *, cod_ibge: str, periodo: str, versao: str) -> FatoDivida | None:
    return session.scalar(
        select(FatoDivida).where(
            FatoDivida.cod_ibge == cod_ibge,
            FatoDivida.periodo == periodo,
            FatoDivida.versao_entrega == versao,
        )
    )


def get_resultado(
    session: Session, *, cod_ibge: str, periodo: str, versao: str
) -> FatoResultado | None:
    return session.scalar(
        select(FatoResultado).where(
            FatoResultado.cod_ibge == cod_ibge,
            FatoResultado.periodo == periodo,
            FatoResultado.versao_entrega == versao,
        )
    )


def get_saude(
    session: Session, *, cod_ibge: str, periodo: str, versao_rreo: str
) -> FatoSaude | None:
    return session.scalar(
        select(FatoSaude)
        .where(
            FatoSaude.cod_ibge == cod_ibge,
            FatoSaude.periodo == periodo,
            FatoSaude.versao_rreo == versao_rreo,
        )
        .order_by(FatoSaude.versao_rgf.desc())
        .limit(1)
    )


def get_educacao(
    session: Session, *, cod_ibge: str, periodo: str, versao_rreo: str
) -> FatoEducacao | None:
    return session.scalar(
        select(FatoEducacao)
        .where(
            FatoEducacao.cod_ibge == cod_ibge,
            FatoEducacao.periodo == periodo,
            FatoEducacao.versao_rreo == versao_rreo,
        )
        .order_by(FatoEducacao.versao_rgf.desc())
        .limit(1)
    )


def list_benchmarks(
    session: Session, *, cod_ibge: str, periodo: str, as_of: datetime
) -> list[tuple[MartBenchmark, DimCoorte]]:
    rows = session.execute(
        select(MartBenchmark, DimCoorte)
        .join(DimCoorte, DimCoorte.id == MartBenchmark.coorte_id)
        .where(
            MartBenchmark.cod_ibge == cod_ibge,
            MartBenchmark.periodo == periodo,
            MartBenchmark.as_of <= as_of,
        )
        .order_by(MartBenchmark.coorte_id, MartBenchmark.as_of.desc())
    ).all()
    latest: dict[uuid.UUID, tuple[MartBenchmark, DimCoorte]] = {}
    for benchmark, coorte in rows:
        latest.setdefault(benchmark.coorte_id, (benchmark, coorte))
    return list(latest.values())


def list_calendario(session: Session, *, cod_ibge: str, ano: str) -> list[CalendarioObrigacao]:
    return list(
        session.scalars(
            select(CalendarioObrigacao)
            .where(
                CalendarioObrigacao.cod_ibge == cod_ibge,
                CalendarioObrigacao.periodo.like(f"{ano}%"),
            )
            .order_by(CalendarioObrigacao.prazo, CalendarioObrigacao.relatorio)
        )
    )


def insert_agendamento(
    session: Session,
    *,
    org_id: uuid.UUID,
    modelo: str,
    formato: str,
    escopo: str,
    entes: list[str],
    periodo: str,
    periodicidade: str,
    parametros: dict[str, Any],
    proxima_execucao: datetime,
    criado_por: uuid.UUID,
) -> RelatorioAgendamento:
    row = RelatorioAgendamento(
        org_id=org_id,
        modelo=modelo,
        formato=formato,
        escopo=escopo,
        entes=entes,
        periodo=periodo,
        periodicidade=periodicidade,
        parametros=parametros,
        proxima_execucao=proxima_execucao,
        ativo=True,
        criado_por=criado_por,
    )
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def list_due_agendamentos(session: Session, now: datetime) -> list[RelatorioAgendamento]:
    return list(
        session.scalars(
            select(RelatorioAgendamento)
            .where(
                RelatorioAgendamento.ativo.is_(True),
                RelatorioAgendamento.proxima_execucao <= now,
            )
            .order_by(RelatorioAgendamento.proxima_execucao)
            .with_for_update(skip_locked=True)
        )
    )
