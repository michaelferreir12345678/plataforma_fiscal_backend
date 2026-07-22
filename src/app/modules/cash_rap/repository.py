"""Persistência de Caixa & RP: gold materializada + leitura do silver (RGF A5, RREO A7)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.cash_rap.caixa import MEDIDAS_DISP, MEDIDAS_RAP
from app.modules.cash_rap.models import DimFonteRecurso, FatoDisponibilidade, FatoRap
from app.modules.ingestion.models import SilverRgf, SilverRreo

_ANEXO_A5 = "05"
_ANEXO_A7 = "07"


# --- silver ---
def read_rgf_anexo5(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[SilverRgf]:
    return list(
        session.scalars(
            select(SilverRgf)
            .where(
                SilverRgf.cod_ibge == cod_ibge,
                SilverRgf.periodo == periodo,
                SilverRgf.versao_entrega == versao_entrega,
                SilverRgf.anexo.ilike(f"%{_ANEXO_A5}%"),
            )
            .order_by(SilverRgf.linha_seq)
        )
    )


def read_rreo_anexo7(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[SilverRreo]:
    return list(
        session.scalars(
            select(SilverRreo)
            .where(
                SilverRreo.cod_ibge == cod_ibge,
                SilverRreo.periodo == periodo,
                SilverRreo.versao_entrega == versao_entrega,
                SilverRreo.anexo.ilike(f"%{_ANEXO_A7}%"),
            )
            .order_by(SilverRreo.linha_seq)
        )
    )


def distinct_periodos_disponibilidade(session: Session, *, cod_ibge: str) -> list[str]:
    return list(
        session.scalars(
            select(FatoDisponibilidade.periodo)
            .where(FatoDisponibilidade.cod_ibge == cod_ibge)
            .distinct()
            .order_by(FatoDisponibilidade.periodo)
        )
    )


def distinct_periodos_silver_a5(session: Session, *, cod_ibge: str) -> list[str]:
    return list(
        session.scalars(
            select(SilverRgf.periodo)
            .where(SilverRgf.cod_ibge == cod_ibge, SilverRgf.anexo.ilike(f"%{_ANEXO_A5}%"))
            .distinct()
            .order_by(SilverRgf.periodo)
        )
    )


# --- dim_fonte_recurso ---
def upsert_fonte(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(DimFonteRecurso).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["codigo"],
        set_={
            "descricao": valores["descricao"],
            "parent_codigo": valores.get("parent_codigo"),
            "nivel": valores["nivel"],
            "path": valores["path"],
            "vinculada": valores["vinculada"],
        },
    )
    session.execute(stmt)


def list_fontes(session: Session, codigos: list[str] | None = None) -> list[DimFonteRecurso]:
    stmt = select(DimFonteRecurso)
    if codigos is not None:
        if not codigos:
            return []
        stmt = stmt.where(DimFonteRecurso.codigo.in_(codigos))
    return list(session.scalars(stmt.order_by(DimFonteRecurso.codigo)))


# --- fato_disponibilidade ---
def upsert_disponibilidade(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(FatoDisponibilidade).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "periodo", "fonte_codigo", "versao_entrega"],
        set_={m: valores.get(m) for m in MEDIDAS_DISP},
    )
    session.execute(stmt)


def list_disponibilidades(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[FatoDisponibilidade]:
    return list(
        session.scalars(
            select(FatoDisponibilidade).where(
                FatoDisponibilidade.cod_ibge == cod_ibge,
                FatoDisponibilidade.periodo == periodo,
                FatoDisponibilidade.versao_entrega == versao_entrega,
            )
        )
    )


# --- fato_rap ---
def replace_raps(
    session: Session,
    *,
    cod_ibge: str,
    periodo: str,
    versao_entrega: str,
    rows: list[dict[str, Any]],
) -> None:
    session.execute(
        delete(FatoRap).where(
            FatoRap.cod_ibge == cod_ibge,
            FatoRap.periodo == periodo,
            FatoRap.versao_entrega == versao_entrega,
        )
    )
    for row in rows:
        session.execute(pg_insert(FatoRap).values(**row))


def list_raps(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[FatoRap]:
    return list(
        session.scalars(
            select(FatoRap)
            .where(
                FatoRap.cod_ibge == cod_ibge,
                FatoRap.periodo == periodo,
                FatoRap.versao_entrega == versao_entrega,
            )
            .order_by(FatoRap.orgao)
        )
    )


# expõe as medidas para o service montar árvore/consolidado sem repetir a lista.
__all__ = [
    "MEDIDAS_DISP",
    "MEDIDAS_RAP",
    "distinct_periodos_disponibilidade",
    "distinct_periodos_silver_a5",
    "list_disponibilidades",
    "list_fontes",
    "list_raps",
    "read_rgf_anexo5",
    "read_rreo_anexo7",
    "replace_raps",
    "upsert_disponibilidade",
    "upsert_fonte",
]
