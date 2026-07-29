"""Acesso a dados da Visão Estadual & Consolidação Territorial (Sprint 23).

Lê os marts já calculados por ente (``mart_indicador``, ``fato_rcl``,
``fato_disponibilidade``) usando **sempre a entrega vigente** (join a ``dim_entrega``),
para que a consolidação some numeradores/denominadores da versão correta — nunca misture
versões retificadas de entes diferentes. A soma em si (Σnum/Σden) é regra de negócio e
mora no *service*.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.modules.cash_rap.models import FatoDisponibilidade
from app.modules.catalog.models import DimEnte
from app.modules.dashboard.estadual_models import (
    DimRegiaoUf,
    GeoMalhaUf,
    MartConsolidadoUf,
)
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega


def _prefixo_uf(uf_prefixo: str) -> ColumnElement[bool]:
    """Filtro SQL: os 2 primeiros dígitos do código IBGE == prefixo da UF."""
    return func.substr(DimEnte.cod_ibge, 1, 2) == uf_prefixo


# --- Universo de entes da UF ---
def list_municipios_uf(session: Session, uf_prefixo: str) -> list[DimEnte]:
    """Municípios (código de 7 dígitos) da UF. O ente estadual (2 dígitos) fica de fora."""
    return list(
        session.scalars(
            select(DimEnte)
            .where(_prefixo_uf(uf_prefixo), func.length(DimEnte.cod_ibge) == 7)
            .order_by(DimEnte.cod_ibge)
        )
    )


def get_ente_estadual(session: Session, uf_prefixo: str) -> DimEnte | None:
    """O ente estadual da UF (código IBGE de 2 dígitos)."""
    return session.get(DimEnte, uf_prefixo)


# --- Valores por ente (sempre da entrega vigente) ---
def mart_valores_uf(
    session: Session, *, cods: Sequence[str], periodo: str, indicador: str
) -> dict[str, tuple[Decimal | None, Decimal | None]]:
    """``{cod_ibge: (valor_rs, valor_pct_rcl)}`` do indicador no período (RREO vigente)."""
    cods = list(cods)
    if not cods:
        return {}
    rows = session.execute(
        select(MartIndicador.cod_ibge, MartIndicador.valor_rs, MartIndicador.valor_pct_rcl)
        .join(
            DimEntrega,
            and_(
                DimEntrega.cod_ibge == MartIndicador.cod_ibge,
                DimEntrega.periodo == MartIndicador.periodo,
                DimEntrega.versao_entrega == MartIndicador.versao_entrega,
                DimEntrega.relatorio == "RREO",
            ),
        )
        .where(
            MartIndicador.cod_ibge.in_(cods),
            MartIndicador.periodo == periodo,
            MartIndicador.indicador == indicador,
            DimEntrega.vigente.is_(True),
        )
    ).all()
    return {r.cod_ibge: (r.valor_rs, r.valor_pct_rcl) for r in rows}


def rcl_uf(session: Session, *, cods: Sequence[str], periodo: str) -> dict[str, Decimal]:
    """``{cod_ibge: rcl_12m}`` no período (RREO vigente). Denominador dos indicadores-razão."""
    cods = list(cods)
    if not cods:
        return {}
    rows = session.execute(
        select(FatoRcl.cod_ibge, FatoRcl.rcl_12m)
        .join(
            DimEntrega,
            and_(
                DimEntrega.cod_ibge == FatoRcl.cod_ibge,
                DimEntrega.periodo == FatoRcl.periodo_ref,
                DimEntrega.versao_entrega == FatoRcl.versao_entrega,
                DimEntrega.relatorio == "RREO",
            ),
        )
        .where(
            FatoRcl.cod_ibge.in_(cods),
            FatoRcl.periodo_ref == periodo,
            DimEntrega.vigente.is_(True),
        )
    ).all()
    return {r.cod_ibge: r.rcl_12m for r in rows if r.rcl_12m is not None}


def disponibilidade_uf(
    session: Session, *, cods: Sequence[str], periodo_rgf: str
) -> dict[str, Decimal]:
    """``{cod_ibge: Σ disp_liquida_apos}`` no período RGF (vigente), somando as fontes.

    A disponibilidade líquida é aditiva em R$; a soma por ente cobre todas as fontes de
    recurso (a suficiência por fonte, essa sim, nunca se consolida — é decisão do módulo 9).
    """
    cods = list(cods)
    if not cods:
        return {}
    rows = session.execute(
        select(
            FatoDisponibilidade.cod_ibge,
            func.sum(FatoDisponibilidade.disp_liquida_apos).label("disp"),
        )
        .join(
            DimEntrega,
            and_(
                DimEntrega.cod_ibge == FatoDisponibilidade.cod_ibge,
                DimEntrega.periodo == FatoDisponibilidade.periodo,
                DimEntrega.versao_entrega == FatoDisponibilidade.versao_entrega,
                DimEntrega.relatorio == "RGF",
            ),
        )
        .where(
            FatoDisponibilidade.cod_ibge.in_(cods),
            FatoDisponibilidade.periodo == periodo_rgf,
            DimEntrega.vigente.is_(True),
        )
        .group_by(FatoDisponibilidade.cod_ibge)
    ).all()
    return {r.cod_ibge: r.disp for r in rows if r.disp is not None}


# --- "Períodos mistos": há entes da UF com dado do indicador em período != do consolidado ---
def periodos_mart_no_ano(
    session: Session, *, cods: Sequence[str], indicador: str, ano: int
) -> list[str]:
    cods = list(cods)
    if not cods:
        return []
    return list(
        session.scalars(
            select(MartIndicador.periodo)
            .where(
                MartIndicador.cod_ibge.in_(cods),
                MartIndicador.indicador == indicador,
                MartIndicador.periodo.like(f"{ano}-%"),
                MartIndicador.valor_rs.is_not(None),
            )
            .distinct()
        )
    )


def periodos_rcl_no_ano(session: Session, *, cods: Sequence[str], ano: int) -> list[str]:
    cods = list(cods)
    if not cods:
        return []
    return list(
        session.scalars(
            select(FatoRcl.periodo_ref)
            .where(FatoRcl.cod_ibge.in_(cods), FatoRcl.periodo_ref.like(f"{ano}-%"))
            .distinct()
        )
    )


def periodos_disp_no_ano(session: Session, *, cods: Sequence[str], ano: int) -> list[str]:
    cods = list(cods)
    if not cods:
        return []
    return list(
        session.scalars(
            select(FatoDisponibilidade.periodo)
            .where(
                FatoDisponibilidade.cod_ibge.in_(cods),
                FatoDisponibilidade.periodo.like(f"{ano}-%"),
            )
            .distinct()
        )
    )


# --- Regiões da UF (dado) ---
def list_regioes(session: Session, uf_prefixo: str) -> list[DimRegiaoUf]:
    return list(
        session.scalars(
            select(DimRegiaoUf).where(DimRegiaoUf.uf == uf_prefixo).order_by(DimRegiaoUf.nome)
        )
    )


def upsert_regiao(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(DimRegiaoUf).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["uf", "regiao_codigo"],
        set_={
            "nome": valores["nome"],
            "municipios": valores["municipios"],
            "nivel_fonte": valores.get("nivel_fonte"),
            "fonte": valores.get("fonte"),
            "atualizado_em": func.now(),
        },
    )
    session.execute(stmt)


# --- Malha geográfica (GeoJSON real do IBGE) ---
def get_malha(session: Session, uf_prefixo: str) -> GeoMalhaUf | None:
    return session.get(GeoMalhaUf, uf_prefixo)


def upsert_malha(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(GeoMalhaUf).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["uf"],
        set_={
            "formato": valores.get("formato", "geojson"),
            "malha": valores["malha"],
            "simplificacao": valores.get("simplificacao"),
            "fonte": valores.get("fonte"),
            "ano": valores.get("ano"),
            "n_areas": valores.get("n_areas"),
            "atualizado_em": func.now(),
        },
    )
    session.execute(stmt)


# --- Consolidado materializado ---
def upsert_consolidado(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(MartConsolidadoUf).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["uf", "periodo", "indicador", "versao_calculo"],
        set_={
            k: valores[k]
            for k in (
                "numerador",
                "denominador",
                "valor_pct",
                "n_entes_total",
                "n_entes_com_dado",
                "cobertura_pct",
                "entes_ausentes",
                "periodos_mistos",
            )
        }
        | {"atualizado_em": func.now()},
    )
    session.execute(stmt)


def get_consolidado(
    session: Session, *, uf_prefixo: str, periodo: str, versao_calculo: str = "v1"
) -> list[MartConsolidadoUf]:
    return list(
        session.scalars(
            select(MartConsolidadoUf).where(
                MartConsolidadoUf.uf == uf_prefixo,
                MartConsolidadoUf.periodo == periodo,
                MartConsolidadoUf.versao_calculo == versao_calculo,
            )
        )
    )


def list_ibges_por_prefixo(
    session: Session, uf_prefixo: str, cods: Iterable[str] | None = None
) -> list[str]:
    """Códigos de municípios da UF, opcionalmente restritos a ``cods`` (interseção de escopo)."""
    stmt = select(DimEnte.cod_ibge).where(
        _prefixo_uf(uf_prefixo), func.length(DimEnte.cod_ibge) == 7
    )
    if cods is not None:
        cods = list(cods)
        if not cods:
            return []
        stmt = stmt.where(DimEnte.cod_ibge.in_(cods))
    return list(session.scalars(stmt.order_by(DimEnte.cod_ibge)))


def entrega_vigente_identity(
    session: Session,
    *,
    cods: Sequence[str],
    relatorio: str,
    periodo: str,
) -> tuple[tuple[str, str, str | None], ...]:
    """Identidade imutável das entregas que alimentam um ranking.

    A consulta traz somente a chave da versão e o hash da entrega, em vez dos fatos
    completos. Ela permite reutilizar por poucos segundos um ranking grande sem servir
    dados de uma retificação nova.
    """
    cods = list(cods)
    if not cods:
        return ()
    rows = session.execute(
        select(
            DimEntrega.cod_ibge,
            DimEntrega.versao_entrega,
            DimEntrega.hash_payload,
        )
        .where(
            DimEntrega.cod_ibge.in_(cods),
            DimEntrega.relatorio == relatorio,
            DimEntrega.periodo == periodo,
            DimEntrega.vigente.is_(True),
        )
        .order_by(DimEntrega.cod_ibge, DimEntrega.versao_entrega)
    ).all()
    return tuple(
        (str(row.cod_ibge), str(row.versao_entrega), row.hash_payload)
        for row in rows
    )
