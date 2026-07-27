"""Modelos da Visão Estadual & Consolidação Territorial (Módulo 2, Sprint 23).

Dado público/compartilhado (gold, sem RLS): o consolidado dos municípios de uma UF, as
regiões da UF e a malha geográfica real. O ente estadual em si é servido pelos endpoints
de ente (``/entes/{ibge}``) e **nunca** se mistura ao consolidado dos municípios.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class MartConsolidadoUf(Base):
    """gold.mart_consolidado_uf — consolidação territorial por (uf, período, indicador).

    Regra invariante: ``valor_pct`` é ``Σnumerador/Σdenominador`` dos municípios que têm o
    indicador — **nunca** a média dos percentuais municipais. A cobertura é dado: quantos
    entes têm dado, quais faltam, o percentual e a marca de períodos mistos.
    """

    __tablename__ = "mart_consolidado_uf"
    __table_args__ = {"schema": "gold"}

    uf: Mapped[str] = mapped_column(String(2), primary_key=True)
    periodo: Mapped[str] = mapped_column(Text, primary_key=True)
    indicador: Mapped[str] = mapped_column(Text, primary_key=True)
    versao_calculo: Mapped[str] = mapped_column(Text, primary_key=True, default="v1")
    numerador: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    denominador: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    valor_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    n_entes_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_entes_com_dado: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cobertura_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    entes_ausentes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    periodos_mistos: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    atualizado_em: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DimRegiaoUf(Base):
    """gold.dim_regiao_uf — regiões da UF como DADO (nome + municípios), para o drill §6.1."""

    __tablename__ = "dim_regiao_uf"
    __table_args__ = {"schema": "gold"}

    uf: Mapped[str] = mapped_column(String(2), primary_key=True)
    regiao_codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    municipios: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    nivel_fonte: Mapped[str | None] = mapped_column(Text, nullable=True)
    fonte: Mapped[str | None] = mapped_column(Text, nullable=True)
    atualizado_em: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GeoMalhaUf(Base):
    """gold.geo_malha_uf — malha municipal real (GeoJSON do IBGE) por UF, para o coroplético."""

    __tablename__ = "geo_malha_uf"
    __table_args__ = {"schema": "gold"}

    uf: Mapped[str] = mapped_column(String(2), primary_key=True)
    formato: Mapped[str] = mapped_column(Text, nullable=False, default="geojson")
    malha: Mapped[dict] = mapped_column(JSONB, nullable=False)
    simplificacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    fonte: Mapped[str | None] = mapped_column(Text, nullable=True)
    ano: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_areas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    atualizado_em: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
