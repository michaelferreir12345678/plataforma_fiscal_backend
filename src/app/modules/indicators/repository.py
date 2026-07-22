"""Acesso a dados dos indicadores (fato_rcl, mart_indicador) e leitura do silver RREO."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion import repository as ingestion_repo
from app.modules.ingestion.models import SilverRreo

RELATORIO_RREO = "RREO"


def resolve_versao_rreo(
    session: Session, *, cod_ibge: str, periodo: str, as_of: datetime | None = None
) -> str | None:
    """Versão vigente (ou 'as of') do RREO para o ente/período (§6.5)."""
    return ingestion_repo.resolve_versao(
        session, cod_ibge=cod_ibge, relatorio=RELATORIO_RREO, periodo=periodo, as_of=as_of
    )


def read_anexo03(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[SilverRreo]:
    return list(
        session.scalars(
            select(SilverRreo).where(
                SilverRreo.cod_ibge == cod_ibge,
                SilverRreo.periodo == periodo,
                SilverRreo.versao_entrega == versao_entrega,
                SilverRreo.anexo.ilike("%03%"),
            )
        )
    )


def upsert_fato_rcl(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(FatoRcl).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "periodo_ref", "versao_entrega"],
        set_={k: valores[k] for k in ("rcl_12m", "deducoes", "receita_corrente", "memoria")},
    )
    session.execute(stmt)


def get_fato_rcl(
    session: Session, *, cod_ibge: str, periodo_ref: str, versao_entrega: str
) -> FatoRcl | None:
    return session.scalar(
        select(FatoRcl).where(
            FatoRcl.cod_ibge == cod_ibge,
            FatoRcl.periodo_ref == periodo_ref,
            FatoRcl.versao_entrega == versao_entrega,
        )
    )


def upsert_mart_indicador(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(MartIndicador).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "periodo", "indicador", "versao_entrega"],
        set_={
            k: valores[k]
            for k in ("valor_rs", "valor_pct_rcl", "faixa", "teto_pct", "source_ref")
        },
    )
    session.execute(stmt)


def get_mart_indicador(
    session: Session, *, cod_ibge: str, periodo: str, indicador: str, versao_entrega: str
) -> MartIndicador | None:
    return session.scalar(
        select(MartIndicador).where(
            MartIndicador.cod_ibge == cod_ibge,
            MartIndicador.periodo == periodo,
            MartIndicador.indicador == indicador,
            MartIndicador.versao_entrega == versao_entrega,
        )
    )


def _num(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)
