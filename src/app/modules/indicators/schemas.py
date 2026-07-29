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


class AjustePeriodo(BaseModel):
    """Insumos de comparabilidade de um ponto da série (deflação + população)."""

    periodo: str
    fator_deflator: Decimal | None = None  # nominal × fator = preços do período base
    ipca_acum_pct: Decimal | None = None  # inflação acumulada do período até a base
    populacao: int | None = None
    pop_ano_ref: int | None = None  # ano da estimativa efetivamente usada


class SerieAjuste(BaseModel):
    """Contexto para exibir uma série em valores reais e per capita (Sprint 25)."""

    base_periodo: str
    deflator_disponivel: bool
    populacao_disponivel: bool
    fonte_deflator: str
    fonte_populacao: str | None = None
    observacao: str | None = None
    itens: list[AjustePeriodo]


class IndicadorOut(BaseModel):
    cod_ibge: str
    periodo: str
    indicador: str
    esfera: str
    valor_rs: Decimal
    # Indicadores gerenciais (Sprint 25D) não têm limite legal: não há faixa nem teto, e
    # inventar um ("normal") faria o produto afirmar conformidade onde a lei nada exige.
    valor_pct_rcl: Decimal | None = None
    faixa: str | None = None
    teto_pct: Decimal | None = None
    versao_entrega: str
    source_ref: SourceRef
    # Sprint 25C: nem todo indicador do mart é percentual da RCL — os mínimos
    # constitucionais têm base própria. Quem exibe o número precisa saber qual.
    denominador: str = "rcl"
    base_valor: Decimal | None = None
