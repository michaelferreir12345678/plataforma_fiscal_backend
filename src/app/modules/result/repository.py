"""Acesso a dados do Resultado Fiscal: gold (fato + ajustes) e leitura do silver RREO Anexo 6."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.ingestion.models import SilverRreo
from app.modules.result.models import FatoAjusteMetodologico, FatoResultado
from app.modules.result.resultado import MEDIDAS

_ANEXO_MARCA = "06"


# --- fato_resultado ---
def upsert_fato(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(FatoResultado).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "periodo", "versao_entrega"],
        set_={m: valores.get(m) for m in MEDIDAS},
    )
    session.execute(stmt)


def get_fato(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> FatoResultado | None:
    return session.scalar(
        select(FatoResultado).where(
            FatoResultado.cod_ibge == cod_ibge,
            FatoResultado.periodo == periodo,
            FatoResultado.versao_entrega == versao_entrega,
        )
    )


def distinct_periodos_fato(session: Session, *, cod_ibge: str) -> list[str]:
    return list(
        session.scalars(
            select(FatoResultado.periodo)
            .where(FatoResultado.cod_ibge == cod_ibge)
            .distinct()
            .order_by(FatoResultado.periodo)
        )
    )


# --- fato_ajuste_metodologico ---
def replace_ajustes(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str,
    ajustes: list[dict[str, Any]],
) -> None:
    session.execute(
        delete(FatoAjusteMetodologico).where(
            FatoAjusteMetodologico.cod_ibge == cod_ibge,
            FatoAjusteMetodologico.periodo == periodo,
            FatoAjusteMetodologico.versao_entrega == versao_entrega,
        )
    )
    for a in ajustes:
        session.execute(pg_insert(FatoAjusteMetodologico).values(**a))


def list_ajustes(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[FatoAjusteMetodologico]:
    return list(
        session.scalars(
            select(FatoAjusteMetodologico).where(
                FatoAjusteMetodologico.cod_ibge == cod_ibge,
                FatoAjusteMetodologico.periodo == periodo,
                FatoAjusteMetodologico.versao_entrega == versao_entrega,
            )
        )
    )


# --- silver: RREO Anexo 6 ---
def read_anexo6(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[SilverRreo]:
    return list(
        session.scalars(
            select(SilverRreo)
            .where(
                SilverRreo.cod_ibge == cod_ibge,
                SilverRreo.periodo == periodo,
                SilverRreo.versao_entrega == versao_entrega,
                SilverRreo.anexo.ilike(f"%{_ANEXO_MARCA}%"),
            )
            .order_by(SilverRreo.linha_seq)
        )
    )
