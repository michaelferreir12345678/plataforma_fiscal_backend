"""Acesso a dados da visão agregada de carteira (mart_carteira + lote)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.dashboard.models import CarteiraLoteJob, MartCarteira


def upsert_mart_carteira(session: Session, valores: dict[str, Any]) -> None:
    """Materializa o snapshot vigente de um (ente, período, indicador)."""
    stmt = pg_insert(MartCarteira).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "periodo", "indicador"],
        set_={
            "faixa": valores.get("faixa"),
            "valor_pct": valores.get("valor_pct"),
            "conformidade_status": valores["conformidade_status"],
            "versao_entrega": valores["versao_entrega"],
            "atualizado_em": func.now(),
        },
    )
    session.execute(stmt)


def list_mart_by_scope(
    session: Session, *, cods_ibge: Iterable[str], periodo: str, indicador: str | None = None
) -> list[MartCarteira]:
    """Snapshots do período para os entes no escopo (opcionalmente de um indicador)."""
    cods = list(cods_ibge)
    if not cods:
        return []
    stmt = select(MartCarteira).where(
        MartCarteira.cod_ibge.in_(cods), MartCarteira.periodo == periodo
    )
    if indicador is not None:
        stmt = stmt.where(MartCarteira.indicador == indicador)
    return list(session.scalars(stmt.order_by(MartCarteira.cod_ibge, MartCarteira.indicador)))


def insert_lote_job(
    session: Session,
    *,
    org_id: uuid.UUID,
    acao: str,
    periodo: str | None,
    entes: list[str],
    filtro: dict[str, Any] | None,
    criado_por: uuid.UUID | None,
) -> CarteiraLoteJob:
    job = CarteiraLoteJob(
        org_id=org_id,
        acao=acao,
        periodo=periodo,
        status="enfileirado",
        total_entes=len(entes),
        entes=entes,
        filtro=filtro,
        criado_por=criado_por,
    )
    session.add(job)
    session.flush()
    session.refresh(job)  # popula defaults do servidor (criado_em)
    return job
