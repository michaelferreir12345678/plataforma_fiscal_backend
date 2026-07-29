"""Acesso a dados da despesa com pessoal: gold (dim/fato) e leitura do silver RGF."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.ingestion.models import SilverRgf
from app.modules.personnel.models import DimPoderOrgao, FatoPessoal

# Marca do Anexo de Despesa com Pessoal no RGF (Anexo 1 / "Anexo 01").
_ANEXO_PESSOAL_MARCA = "01"


# --- dim_poder_orgao ---
def get_poder(session: Session, codigo: str) -> DimPoderOrgao | None:
    return session.get(DimPoderOrgao, codigo)


def list_poderes(session: Session, codigos: Iterable[str] | None = None) -> list[DimPoderOrgao]:
    stmt = select(DimPoderOrgao)
    if codigos is not None:
        cods = list(codigos)
        if not cods:
            return []
        stmt = stmt.where(DimPoderOrgao.codigo.in_(cods))
    return list(session.scalars(stmt))


def upsert_poder(
    session: Session, valores: dict[str, Any], *, sobrescrever_descricao: bool
) -> None:
    stmt = pg_insert(DimPoderOrgao).values(**valores)
    if sobrescrever_descricao:
        stmt = stmt.on_conflict_do_update(
            index_elements=["codigo"], set_={"descricao": valores["descricao"]}
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=["codigo"])
    session.execute(stmt)


# --- fato_pessoal ---
def upsert_fato(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(FatoPessoal).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "periodo", "poder_codigo", "versao_entrega"],
        # A lista é explícita para não sobrescrever a chave, mas por isso mesmo precisa
        # acompanhar o modelo: uma coluna nova fora daqui é gravada no INSERT e ignorada
        # em toda rematerialização — o valor calculado atualiza e a base que o produziu
        # fica congelada, que é o pior dos dois mundos.
        set_={
            k: valores.get(k)
            for k in ("despesa_bruta", "exclusoes", "despesa_liquida", "rcl_ajustada", "pct_rcl")
            if k in valores
        },
    )
    session.execute(stmt)


def list_fatos(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[FatoPessoal]:
    return list(
        session.scalars(
            select(FatoPessoal).where(
                FatoPessoal.cod_ibge == cod_ibge,
                FatoPessoal.periodo == periodo,
                FatoPessoal.versao_entrega == versao_entrega,
            )
        )
    )


def distinct_periodos_fato(session: Session, *, cod_ibge: str) -> list[str]:
    return list(
        session.scalars(
            select(FatoPessoal.periodo)
            .where(FatoPessoal.cod_ibge == cod_ibge)
            .distinct()
            .order_by(FatoPessoal.periodo)
        )
    )


# --- silver: RGF Anexo de Despesa com Pessoal ---
def read_rgf_pessoal(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[SilverRgf]:
    """Linhas do Anexo 1 do RGF (Demonstrativo da Despesa com Pessoal) do ente/período."""
    return list(
        session.scalars(
            select(SilverRgf).where(
                SilverRgf.cod_ibge == cod_ibge,
                SilverRgf.periodo == periodo,
                SilverRgf.versao_entrega == versao_entrega,
                SilverRgf.anexo.ilike(f"%{_ANEXO_PESSOAL_MARCA}%"),
            )
        )
    )
