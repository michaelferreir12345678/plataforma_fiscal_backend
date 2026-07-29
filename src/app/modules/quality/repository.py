"""Acesso a gold.data_quality_check e gold.lineage_edge (Sprint 26)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.quality.models import DataQualityCheck, LineageEdge


def upsert_check(session: Session, valores: dict[str, Any]) -> None:
    """Grava o estado corrente do check — reexecutar atualiza, não empilha."""
    stmt = pg_insert(DataQualityCheck).values(id=uuid.uuid4(), **valores)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_data_quality_check_chave",
        set_={
            "job_id": stmt.excluded.job_id,
            "status": stmt.excluded.status,
            "esquerda": stmt.excluded.esquerda,
            "direita": stmt.excluded.direita,
            "diferenca": stmt.excluded.diferenca,
            "tolerancia": stmt.excluded.tolerancia,
            "detalhe": stmt.excluded.detalhe,
            "executado_em": datetime.now(UTC),
        },
    )
    session.execute(stmt)


def listar_checks(
    session: Session,
    *,
    fonte: str | None = None,
    status: str | None = None,
    cod_ibge: str | None = None,
    check_codigo: str | None = None,
    cods_escopo: Iterable[str] | None = None,
    limite: int = 100,
    offset: int = 0,
) -> tuple[list[DataQualityCheck], int]:
    stmt = select(DataQualityCheck)
    if fonte:
        stmt = stmt.where(DataQualityCheck.fonte == fonte)
    if status:
        stmt = stmt.where(DataQualityCheck.status == status)
    if cod_ibge:
        stmt = stmt.where(DataQualityCheck.cod_ibge == cod_ibge)
    if check_codigo:
        stmt = stmt.where(DataQualityCheck.check_codigo == check_codigo)
    if cods_escopo is not None:
        codigos = list(cods_escopo)
        # Check global (cod_ibge NULL) é visível a todos; o por ente respeita o escopo.
        stmt = stmt.where(
            DataQualityCheck.cod_ibge.is_(None) | DataQualityCheck.cod_ibge.in_(codigos)
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    linhas = list(
        session.scalars(
            stmt.order_by(
                # Falha primeiro: o painel existe para mostrar problema, não catálogo.
                DataQualityCheck.status.desc(),
                DataQualityCheck.executado_em.desc(),
            )
            .limit(limite)
            .offset(offset)
        )
    )
    return linhas, int(total)


def contar_por_status(
    session: Session, *, cods_escopo: Iterable[str] | None = None
) -> dict[str, int]:
    stmt = select(DataQualityCheck.status, func.count()).group_by(DataQualityCheck.status)
    if cods_escopo is not None:
        codigos = list(cods_escopo)
        stmt = stmt.where(
            DataQualityCheck.cod_ibge.is_(None) | DataQualityCheck.cod_ibge.in_(codigos)
        )
    return {str(s): int(n) for s, n in session.execute(stmt).all()}


def checks_abertos(
    session: Session, *, cod_ibge: str, periodo: str | None = None
) -> list[DataQualityCheck]:
    """Checks em falha/aviso do ente — o que a página precisa selar."""
    stmt = select(DataQualityCheck).where(
        DataQualityCheck.cod_ibge == cod_ibge,
        DataQualityCheck.status.in_(("falha", "aviso")),
    )
    if periodo:
        stmt = stmt.where(
            DataQualityCheck.periodo.is_(None) | (DataQualityCheck.periodo == periodo)
        )
    return list(session.scalars(stmt.order_by(DataQualityCheck.status.desc())))


def fontes_distintas(session: Session) -> list[str]:
    return [str(f) for f in session.scalars(select(DataQualityCheck.fonte).distinct())]


# --- lineage ---------------------------------------------------------------- #
def upsert_aresta(
    session: Session, *, origem: str, destino: str, tipo: str, detalhe: dict | None
) -> None:
    stmt = pg_insert(LineageEdge).values(
        id=uuid.uuid4(), origem=origem, destino=destino, tipo=tipo, detalhe=detalhe
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_lineage_edge_chave", set_={"detalhe": stmt.excluded.detalhe}
    )
    session.execute(stmt)


def listar_arestas(session: Session) -> Sequence[LineageEdge]:
    return list(session.scalars(select(LineageEdge).order_by(LineageEdge.tipo, LineageEdge.origem)))


def contar_arestas(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(LineageEdge)) or 0)
