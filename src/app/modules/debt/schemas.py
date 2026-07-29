"""Contratos HTTP do módulo de dívida (detalhe, memória, CAPAG e simulação)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.indicators.schemas import SerieAjuste
from app.shared.envelope import DrillChild, DrillNodeRef
from app.shared.source_ref import SourceRef


class DclHero(BaseModel):
    rotulo: str = "DCL líquida"
    natureza: Literal["liquida"] = "liquida"
    dc_bruta: Decimal
    disponibilidades: Decimal
    haveres: Decimal
    dcl: Decimal
    rcl_ajustada: Decimal | None = None
    pct_rcl: Decimal | None = None
    limite_pct: Decimal
    faixa: str | None = None
    as_of: datetime | None = None
    source_ref: SourceRef


class CapagHero(BaseModel):
    rotulo: str = "CAPAG — endividamento bruto"
    natureza: Literal["bruta"] = "bruta"
    ano_ref: int
    nota_final: str | None = None
    ind_endividamento: Decimal | None = None
    endividamento_pct: Decimal | None = None
    ind_poupanca: Decimal | None = None
    ind_liquidez: Decimal | None = None
    metodologia_versao: str | None = None
    as_of: datetime | None = None
    source_ref: SourceRef


class SerieDividaItem(BaseModel):
    periodo: str
    as_of: datetime
    dcl: Decimal
    pct_rcl: Decimal | None = None
    dc_bruta: Decimal
    # Comparabilidade multi-exercício (Sprint 25): a preços do período e por habitante.
    dcl_real: Decimal | None = None
    dcl_per_capita: Decimal | None = None
    populacao: int | None = None
    source_ref: SourceRef


class ComparacaoDivida(BaseModel):
    periodo_anterior: str
    dcl_anterior: Decimal
    variacao_rs: Decimal
    variacao_pct: Decimal | None = None


class DividaDetalhe(BaseModel):
    cod_ibge: str
    periodo: str
    as_of: datetime | None = None
    versao_entrega: str
    esfera: str
    dcl: DclHero
    capag: CapagHero
    composicao: list[DrillChild]
    serie: list[SerieDividaItem]
    serie_ajuste: SerieAjuste | None = None  # deflator IPCA + população
    comparacao: ComparacaoDivida | None = None
    periodo_breadcrumb: list[DrillNodeRef]
    source_ref: SourceRef


class ComponenteMemoria(BaseModel):
    componente: str
    operador: Literal["+", "-"]
    valor: Decimal
    conta_origem: str | None = None
    coluna_origem: str | None = None


class MemoriaDivida(BaseModel):
    cod_ibge: str
    periodo: str
    as_of: datetime | None = None
    versao_entrega: str
    componentes: list[ComponenteMemoria]
    dc_bruta: Decimal
    disponibilidades: Decimal
    haveres: Decimal
    dcl: Decimal
    dcl_reportada: Decimal | None = None
    rcl_ajustada: Decimal | None = None
    pct_rcl: Decimal | None = None
    formula_dcl: str
    formula_pct: str
    reconciliacao_ok: bool | None = None
    diferenca_reconciliacao: Decimal | None = None
    detalhes: dict[str, Any] = Field(default_factory=dict)
    source_ref: SourceRef


class DividaArvoreOut(BaseModel):
    eixo: Literal["origem", "credor"]
    node: DrillNodeRef | None = None
    breadcrumb: list[DrillNodeRef] = Field(default_factory=list)
    children: list[DrillChild] = Field(default_factory=list)
    measures: dict[str, Any] = Field(default_factory=dict)
    period: str
    as_of: datetime | None = None
    source_ref: SourceRef


class CapagMemoria(BaseModel):
    formula_endividamento: str
    base_numerador: str
    base_denominador: str
    escala: str
    observacoes: list[str] = Field(default_factory=list)


class CapagResponse(BaseModel):
    cod_ibge: str
    periodo: str
    as_of: datetime | None = None
    hero: CapagHero
    memoria: CapagMemoria
    source_ref: SourceRef


class VencimentoItem(BaseModel):
    ano: int
    principal: Decimal
    juros: Decimal
    encargos: Decimal
    valor: Decimal
    operacoes: int


class CronogramaResponse(BaseModel):
    cod_ibge: str
    periodo_ref: str
    as_of: datetime | None = None
    versao_entrega: str
    itens: list[VencimentoItem]
    total_principal: Decimal
    total_juros: Decimal
    total_encargos: Decimal
    total_valor: Decimal
    source_ref: SourceRef


class SimularOperacaoRequest(BaseModel):
    valor_operacao: Decimal = Field(gt=0)
    valor_garantia: Decimal = Field(default=Decimal(0), ge=0)
    valor_aro: Decimal = Field(default=Decimal(0), ge=0)
    garantias_atuais: Decimal | None = Field(default=None, ge=0)
    aro_atual: Decimal | None = Field(default=None, ge=0)


class PosicaoSimulada(BaseModel):
    indicador: str
    rotulo: str
    valor_atual: Decimal | None = None
    incremento: Decimal
    valor_projetado: Decimal | None = None
    pct_atual: Decimal | None = None
    pct_projetado: Decimal | None = None
    teto_pct: Decimal
    faixa_atual: str | None = None
    faixa_projetada: str | None = None
    posicao_atual_conhecida: bool


class SimulacaoResponse(BaseModel):
    cod_ibge: str
    periodo: str
    as_of: datetime | None = None
    rcl_ajustada: Decimal
    posicoes: list[PosicaoSimulada]
    persistido: Literal[False] = False
    memoria: dict[str, Any]
    source_refs: list[SourceRef]


class PvlItem(BaseModel):
    """Pedido de verificação de limites (PVL/CDP) apresentado ao Tesouro."""

    id_pvl: str | None = None
    tipo_operacao: str | None = None
    valor: Decimal | None = None
    status: str | None = None
    decisao: str | None = None
    data_analise: date | None = None


class PvlOut(BaseModel):
    """PVL/CDP do ente — pedidos em tramitação e decisões do Tesouro.

    Quando a silver não tem dado para o ente, ``itens`` vem vazio e ``observacao``
    diz por quê: ausência de ingestão não é ausência de pedidos.
    """

    cod_ibge: str
    itens: list[PvlItem]
    total_valor: Decimal | None = None
    versao_entrega: str | None = None
    observacao: str | None = None
    source_ref: SourceRef
