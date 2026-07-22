"""Schemas dos indicadores (RCL com memória de cálculo; indicador × limite)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.shared.source_ref import SourceRef


class ComponenteOut(BaseModel):
    conta: str
    valor: Decimal


class RclResponse(BaseModel):
    """RCL com memória de cálculo rastreável e drill DOWN para componentes/deduções."""

    cod_ibge: str
    periodo: str
    versao_entrega: str
    as_of: datetime | None = None
    rcl_12m: Decimal
    receita_corrente: Decimal
    deducoes_total: Decimal
    componentes: list[ComponenteOut]  # drill DOWN (receitas correntes)
    deducoes: list[ComponenteOut]  # drill DOWN (deduções: RPPS/compensação/FUNDEB)
    source_ref: SourceRef


class IndicadorOut(BaseModel):
    cod_ibge: str
    periodo: str
    indicador: str
    esfera: str
    valor_rs: Decimal
    valor_pct_rcl: Decimal
    faixa: str
    teto_pct: Decimal
    versao_entrega: str
    source_ref: SourceRef
