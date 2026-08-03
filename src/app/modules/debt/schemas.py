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
    """Serviço da dívida de um ano, no corte que a fonte publica.

    Não há ``juros``: o SADIPEM publica amortização e encargos, sem separá-los. O campo
    existia e vinha sempre zerado, o que a tela mostrava como "juros: R$ 0,00" — que se lê
    como "não há juros", e não como "a fonte não separa".
    """

    ano: int
    principal: Decimal
    #: Inclui os juros — a fonte não os discrimina.
    encargos: Decimal
    valor: Decimal
    #: Dívida consolidada (estoque) × operações contratadas (o que foi assumido de novo).
    dc_amortizacao: Decimal | None = None
    dc_encargos: Decimal | None = None
    oc_amortizacao: Decimal | None = None
    oc_encargos: Decimal | None = None
    operacoes: int


class CronogramaResponse(BaseModel):
    cod_ibge: str
    periodo_ref: str
    as_of: datetime | None = None
    versao_entrega: str
    itens: list[VencimentoItem]
    total_principal: Decimal
    total_encargos: Decimal
    total_valor: Decimal
    #: Totais do corte da fonte: estoque × contratado. Somados dão ``total_valor``.
    total_dc: Decimal = Decimal(0)
    total_oc: Decimal = Decimal(0)
    #: **O que vence além do horizonte publicado.** A fonte fecha a série com uma linha
    #: "Restante a pagar"; ela não é um ano e não entra em ``itens``, mas entra no
    #: compromisso. Ignorá-la subestimava o serviço da dívida em 16,6% (Fortaleza).
    restante_amortizacao: Decimal = Decimal(0)
    restante_encargos: Decimal = Decimal(0)
    #: Último ano explícito da série — é o "após" que rotula o residual.
    horizonte_ate: int | None = None
    #: ``total_valor`` + residual: o compromisso remanescente inteiro.
    total_com_residual: Decimal = Decimal(0)
    source_ref: SourceRef


class CdpSituacao(BaseModel):
    """Situação do processo no Cadastro da Dívida Pública (base nacional, casada por processo)."""

    num_pvl: str | None = None
    num_processo: str | None = None
    data_ref: date | None = None
    situacao: str | None = None
    motivo: str | None = None


class OperacaoDetalhe(BaseModel):
    """Uma operação de crédito **inteira**: do pedido à situação cadastral e ao cronograma.

    É o fundo do drill da página de dívida. A tabela lista; aqui está tudo o que a fonte
    publica sobre aquele processo, reunindo três endpoints do SADIPEM que até então viviam
    separados — inclusive o CDP, que a plataforma ingeria e nenhuma tela consumia.
    """

    cod_ibge: str
    pleito: PvlItem
    #: Situação cadastral do mesmo processo. Vazio quando o CDP não traz aquele processo.
    cdp: list[CdpSituacao] = Field(default_factory=list)
    #: Cronograma daquele pleito, ano a ano. Vazio se a operação não foi contratada.
    cronograma: list[VencimentoItem] = Field(default_factory=list)
    total_amortizacao: Decimal = Decimal(0)
    total_encargos: Decimal = Decimal(0)
    restante_amortizacao: Decimal = Decimal(0)
    restante_encargos: Decimal = Decimal(0)
    horizonte_ate: int | None = None
    #: Por que uma seção está vazia — ausência explicada em vez de espaço em branco.
    observacoes: dict[str, str] = Field(default_factory=dict)
    source_refs: list[SourceRef] = Field(default_factory=list)


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
    #: Identificadores do processo no Tesouro — sem eles a operação não é rastreável.
    num_pvl: str | None = None
    num_processo: str | None = None
    tipo_operacao: str | None = None
    #: Para que serve o dinheiro e quem empresta — o corte analítico de crédito.
    finalidade: str | None = None
    credor: str | None = None
    tipo_credor: str | None = None
    moeda: str | None = None
    valor: Decimal | None = None
    status: str | None = None
    data_protocolo: date | None = None
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
