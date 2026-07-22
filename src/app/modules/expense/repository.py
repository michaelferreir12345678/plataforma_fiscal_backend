"""Acesso a dados da despesa: gold (dims + fato) e leitura do silver RREO (Anexos 01/02)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.expense.classificacao import MEDIDAS
from app.modules.expense.models import DimFuncao, DimNatureza, FatoDespesa
from app.modules.ingestion.models import SilverRreo

# Dimensão hierárquica de um eixo (mesma forma §6.1 nas duas). Resolvido no service.
DimModel = type[DimFuncao] | type[DimNatureza]
DimRow = DimFuncao | DimNatureza

# Modelo de dimensão por eixo (funcao/natureza).
DIM_MODEL: dict[str, DimModel] = {"funcao": DimFuncao, "natureza": DimNatureza}


# --- dimensões (dim_funcao / dim_natureza) ---
def get_dim(session: Session, model: DimModel, codigo: str) -> DimRow | None:
    return cast("DimRow | None", session.get(model, codigo))


def list_dim(
    session: Session, model: DimModel, codigos: Iterable[str] | None = None
) -> list[DimRow]:
    stmt = select(model)
    if codigos is not None:
        cods = list(codigos)
        if not cods:
            return []
        stmt = stmt.where(model.codigo.in_(cods))
    return list(session.scalars(stmt))


def upsert_dim(
    session: Session, model: DimModel, valores: dict[str, Any], *, sobrescrever_descricao: bool
) -> None:
    """Insere o nó da hierarquia; sobrescreve a descrição só quando veio do dado real.

    Nós ancestrais/sentinela sintetizados usam descrição de fallback e **não**
    sobrescrevem uma descrição já conhecida.
    """
    stmt = pg_insert(model).values(**valores)
    if sobrescrever_descricao:
        stmt = stmt.on_conflict_do_update(
            index_elements=["codigo"], set_={"descricao": valores["descricao"]}
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=["codigo"])
    session.execute(stmt)


# --- fato_despesa ---
def upsert_fato(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(FatoDespesa).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            "cod_ibge", "periodo", "funcao_codigo", "natureza_codigo", "versao_entrega"
        ],
        set_={m: valores.get(m) for m in MEDIDAS},
    )
    session.execute(stmt)


def list_fatos(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[FatoDespesa]:
    return list(
        session.scalars(
            select(FatoDespesa).where(
                FatoDespesa.cod_ibge == cod_ibge,
                FatoDespesa.periodo == periodo,
                FatoDespesa.versao_entrega == versao_entrega,
            )
        )
    )


def distinct_periodos_fato(session: Session, *, cod_ibge: str) -> list[str]:
    return list(
        session.scalars(
            select(FatoDespesa.periodo)
            .where(FatoDespesa.cod_ibge == cod_ibge)
            .distinct()
            .order_by(FatoDespesa.periodo)
        )
    )


# --- silver: RREO Anexos 01 (natureza) e 02 (função) ---
def read_anexo(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str, marca: str
) -> list[SilverRreo]:
    """Linhas do RREO cujo ``anexo`` contém ``marca`` (``'01'`` ou ``'02'``)."""
    return list(
        session.scalars(
            select(SilverRreo).where(
                SilverRreo.cod_ibge == cod_ibge,
                SilverRreo.periodo == periodo,
                SilverRreo.versao_entrega == versao_entrega,
                SilverRreo.anexo.ilike(f"%{marca}%"),
            )
        )
    )
