"""Acesso a dados da receita: gold (dim/fato) e leituras de silver (RREO Anexo 01 + 1B)."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.ingestion.models import (
    FndeFundebRepasse,
    SilverRreo,
    TesouroFpm,
    TransferenciaGenerica,
)
from app.modules.revenue.models import DimOrigemReceita, FatoReceita


# --- dim_origem_receita ---
def get_origem(session: Session, codigo: str) -> DimOrigemReceita | None:
    return session.get(DimOrigemReceita, codigo)


def list_origens(session: Session, codigos: Iterable[str] | None = None) -> list[DimOrigemReceita]:
    stmt = select(DimOrigemReceita)
    if codigos is not None:
        cods = list(codigos)
        if not cods:
            return []
        stmt = stmt.where(DimOrigemReceita.codigo.in_(cods))
    return list(session.scalars(stmt))


def upsert_origem(
    session: Session, valores: dict[str, Any], *, sobrescrever_descricao: bool
) -> None:
    """Insere o nó da hierarquia; atualiza a descrição só quando veio do dado real.

    Nós ancestrais sintetizados usam descrição de fallback e **não** sobrescrevem
    uma descrição já conhecida.
    """
    stmt = pg_insert(DimOrigemReceita).values(**valores)
    if sobrescrever_descricao:
        stmt = stmt.on_conflict_do_update(
            index_elements=["codigo"], set_={"descricao": valores["descricao"]}
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=["codigo"])
    session.execute(stmt)


# --- fato_receita ---
def upsert_fato(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(FatoReceita).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "periodo", "origem_codigo", "versao_entrega"],
        set_={
            k: valores.get(k)
            for k in (
                "previsto_inicial",
                "previsto_atualizado",
                "arrecadado_bimestre",
                "arrecadado_acum",
                "deducoes",
            )
        },
    )
    session.execute(stmt)


def list_fatos(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[FatoReceita]:
    return list(
        session.scalars(
            select(FatoReceita).where(
                FatoReceita.cod_ibge == cod_ibge,
                FatoReceita.periodo == periodo,
                FatoReceita.versao_entrega == versao_entrega,
            )
        )
    )


def distinct_periodos_fato(session: Session, *, cod_ibge: str) -> list[str]:
    return list(
        session.scalars(
            select(FatoReceita.periodo)
            .where(FatoReceita.cod_ibge == cod_ibge)
            .distinct()
            .order_by(FatoReceita.periodo)
        )
    )


# --- silver: RREO Anexo 01 ---
def read_anexo01(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[SilverRreo]:
    return list(
        session.scalars(
            select(SilverRreo).where(
                SilverRreo.cod_ibge == cod_ibge,
                SilverRreo.periodo == periodo,
                SilverRreo.versao_entrega == versao_entrega,
                SilverRreo.anexo.ilike("%01%"),
            )
        )
    )


# --- silver 1B: transferências externas (conciliação) ---
def soma_fpm(
    session: Session, *, cod_ibge: str, ano: int, meses: list[int]
) -> Decimal | None:
    """Soma do FPM líquido (fallback: bruto) nos meses; ``None`` se não há dado."""
    row = session.execute(
        select(
            func.count(TesouroFpm.id),
            func.sum(func.coalesce(TesouroFpm.valor_liquido, TesouroFpm.valor_bruto)),
        ).where(
            TesouroFpm.cod_ibge == cod_ibge,
            TesouroFpm.ano == ano,
            TesouroFpm.mes.in_(meses),
        )
    ).one()
    if int(row[0] or 0) == 0:
        return None
    return Decimal(row[1] or 0)


def soma_fundeb(
    session: Session, *, cod_ibge: str, ano: int, meses: list[int]
) -> Decimal | None:
    row = session.execute(
        select(
            func.count(FndeFundebRepasse.id),
            func.sum(func.coalesce(FndeFundebRepasse.valor_repassado, 0)),
        ).where(
            FndeFundebRepasse.cod_ibge == cod_ibge,
            FndeFundebRepasse.ano == ano,
            FndeFundebRepasse.mes.in_(meses),
        )
    ).one()
    if int(row[0] or 0) == 0:
        return None
    return Decimal(row[1] or 0)


def somas_transferencia_generica(
    session: Session, *, cod_ibge: str, ano: int, meses: list[int]
) -> list[tuple[str, Decimal]]:
    """Somas por ``tipo`` de ``silver.transferencia_generica`` nos meses do período."""
    rows = session.execute(
        select(
            TransferenciaGenerica.tipo,
            func.sum(func.coalesce(TransferenciaGenerica.valor, 0)),
        )
        .where(
            TransferenciaGenerica.cod_ibge == cod_ibge,
            TransferenciaGenerica.ano == ano,
            TransferenciaGenerica.mes.in_(meses),
        )
        .group_by(TransferenciaGenerica.tipo)
        .order_by(TransferenciaGenerica.tipo)
    ).all()
    return [(str(t), Decimal(v or 0)) for t, v in rows]
