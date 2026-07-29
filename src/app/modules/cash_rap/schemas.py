"""Schemas de Caixa & Restos a Pagar (detalhe, suficiência, RPNP sem lastro, art. 42)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.modules.indicators.schemas import SerieAjuste
from app.shared.envelope import DrillNodeRef
from app.shared.source_ref import SourceRef


class FonteSuficienciaItem(BaseModel):
    """Suficiência de uma fonte (nunca compensada com outra) + semáforo de 3 níveis."""

    fonte_codigo: str
    descricao: str
    vinculada: bool
    grupo_codigo: str
    grupo_descricao: str
    disp_bruta: Decimal | None = None
    obrigacoes: Decimal | None = None
    disp_liquida_antes: Decimal | None = None
    rpnp_exercicio: Decimal | None = None
    disp_liquida_apos: Decimal | None = None
    rpnp_sem_lastro: Decimal | None = None
    status: str  # suficiente | insuficiente_rpnp | deficit
    semaforo: str  # verde | amarelo | vermelho
    suficiente: bool


class GrupoSubtotal(BaseModel):
    """Subtotal por grupo de vinculação (informativo — a suficiência que vale é por fonte)."""

    grupo_codigo: str
    descricao: str
    vinculada: bool
    disp_liquida_antes: Decimal | None = None
    rpnp_exercicio: Decimal | None = None
    disp_liquida_apos: Decimal | None = None
    rpnp_sem_lastro: Decimal | None = None
    n_fontes: int


class SuficienciaResumo(BaseModel):
    n_fontes: int
    n_suficientes: int
    n_insuficientes: int  # status != suficiente (inclui déficit)
    n_deficit: int
    total_rpnp_sem_lastro: Decimal
    total_disp_liquida_apos_positiva: Decimal  # soma só dos superávits (informativo)


class SuficienciaMatriz(BaseModel):
    """Matriz de suficiência **por fonte** com semáforo — nunca consolidada (LRF art. 8º)."""

    cod_ibge: str
    periodo: str
    as_of: str | None = None
    versao_entrega: str
    esfera: str | None = None
    itens: list[FonteSuficienciaItem]
    grupos: list[GrupoSubtotal]
    resumo: SuficienciaResumo
    observacao: str
    source_ref: SourceRef


class RpnpSemLastroItem(BaseModel):
    fonte_codigo: str
    descricao: str
    vinculada: bool
    rpnp_exercicio: Decimal | None = None
    disp_liquida_antes: Decimal | None = None
    rpnp_sem_lastro: Decimal


class RpnpSemLastroOut(BaseModel):
    """RPNP inscrito sem disponibilidade de caixa, **por fonte** (consumido pelos mínimos)."""

    cod_ibge: str
    periodo: str
    as_of: str | None = None
    versao_entrega: str
    itens: list[RpnpSemLastroItem]  # só fontes com rpnp_sem_lastro > 0
    total_rpnp_sem_lastro: Decimal
    total_vinculada: Decimal
    total_nao_vinculada: Decimal
    observacao: str
    source_ref: SourceRef


class Art42FonteItem(BaseModel):
    fonte_codigo: str
    descricao: str
    vinculada: bool
    disp_bruta: Decimal | None = None
    obrigacoes_ate_fim: Decimal | None = None  # obrigações + RPNP a lastrear
    lastro: Decimal | None = None  # disponibilidade após inscrição em RPNP
    cumpre: bool
    lacuna: Decimal  # obrigação sem lastro (0 se cumpre)


class Art42Out(BaseModel):
    """Painel do art. 42 LRF — só é aplicável em ano de fim de mandato."""

    cod_ibge: str
    periodo: str
    as_of: str | None = None
    versao_entrega: str | None = None
    esfera: str | None = None
    ano: int
    quadrimestre: int | None = None
    aplicavel: bool  # ano de fim de mandato
    janela_vedacao: bool  # 2 últimos quadrimestres (Q2/Q3)
    atende: bool | None = None  # todas as fontes cumprem
    n_descumprimentos: int = 0
    total_lacuna: Decimal = Decimal(0)
    fontes: list[Art42FonteItem] = Field(default_factory=list)
    observacao: str
    source_ref: SourceRef | None = None


class RapOrgaoItem(BaseModel):
    orgao: str
    rpp_inscritos: Decimal | None = None
    rpp_pagos: Decimal | None = None
    rpp_cancelados: Decimal | None = None
    rpp_a_pagar: Decimal | None = None
    rpnp_inscritos: Decimal | None = None
    rpnp_liquidados: Decimal | None = None
    rpnp_pagos: Decimal | None = None
    rpnp_cancelados: Decimal | None = None
    rpnp_a_pagar: Decimal | None = None
    saldo_total: Decimal | None = None


class SerieCaixaItem(BaseModel):
    periodo: str
    disp_liquida_apos_total: Decimal | None = None
    rpnp_sem_lastro_total: Decimal | None = None
    # Comparabilidade multi-exercício (Sprint 25): a preços do período e por habitante.
    rpnp_sem_lastro_real: Decimal | None = None
    rpnp_sem_lastro_per_capita: Decimal | None = None
    populacao: int | None = None


class ComparacaoCaixa(BaseModel):
    periodo_anterior: str
    rpnp_sem_lastro_anterior: Decimal | None = None
    delta_rs: Decimal | None = None


class CaixaDetalhe(BaseModel):
    """Cabeçalho (suficiência + RP) + fontes críticas + série (Padrão de Detalhe)."""

    cod_ibge: str
    periodo: str  # RGF quadrimestral (base do Anexo 5)
    periodo_rreo: str | None = None  # RREO bimestral correspondente (base do Anexo 7)
    as_of: str | None = None
    versao_entrega: str
    esfera: str | None = None
    resumo: SuficienciaResumo
    disp_liquida_apos_total: Decimal  # soma (informativa) das disponibilidades líquidas
    fontes_criticas: list[FonteSuficienciaItem]  # status != suficiente
    rap_consolidado: RapOrgaoItem | None = None
    rap_por_orgao: list[RapOrgaoItem]
    art42_aplicavel: bool
    serie: list[SerieCaixaItem]
    serie_ajuste: SerieAjuste | None = None  # deflator IPCA + população
    comparacao: ComparacaoCaixa | None = None
    periodo_breadcrumb: list[DrillNodeRef]
    source_ref: SourceRef  # RGF Anexo 5 (caixa)
    source_ref_rap: SourceRef | None = None  # RREO Anexo 7 (restos a pagar)


class CaixaMemoria(BaseModel):
    """Memória rastreável: fórmulas, identidades e origem de cada número (auditável)."""

    cod_ibge: str
    periodo: str
    as_of: str | None = None
    versao_entrega: str
    fontes: list[FonteSuficienciaItem]
    total_rpnp_sem_lastro: Decimal
    formula_liquida_antes: str
    formula_liquida_apos: str
    formula_rpnp_sem_lastro: str
    regra_suficiencia: str
    detalhes: dict[str, Any] = Field(default_factory=dict)
    source_ref: SourceRef
