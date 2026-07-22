"""Acesso a dados do monitor de limites (mart_indicador + providências)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.indicators.models import MartIndicador
from app.modules.limits.models import DimProvidenciaLegal


def list_mart_by_periodo(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[MartIndicador]:
    return list(
        session.scalars(
            select(MartIndicador)
            .where(
                MartIndicador.cod_ibge == cod_ibge,
                MartIndicador.periodo == periodo,
                MartIndicador.versao_entrega == versao_entrega,
            )
            .order_by(MartIndicador.indicador)
        )
    )


def distinct_periodos_mart(session: Session, *, cod_ibge: str, indicador: str) -> list[str]:
    return list(
        session.scalars(
            select(MartIndicador.periodo)
            .where(MartIndicador.cod_ibge == cod_ibge, MartIndicador.indicador == indicador)
            .distinct()
            .order_by(MartIndicador.periodo)
        )
    )


def providencias(session: Session, *, indicador: str, faixa: str) -> list[DimProvidenciaLegal]:
    return list(
        session.scalars(
            select(DimProvidenciaLegal)
            .where(
                DimProvidenciaLegal.indicador == indicador,
                DimProvidenciaLegal.faixa == faixa,
            )
            .order_by(DimProvidenciaLegal.id)
        )
    )


def upsert_providencia(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(DimProvidenciaLegal).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["indicador", "faixa", "texto"],
        set_={"base_legal": valores.get("base_legal")},
    )
    session.execute(stmt)
