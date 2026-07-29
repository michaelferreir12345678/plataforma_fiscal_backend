"""Schemas do Cockpit Executivo Fiscal (Sprint 22, Módulo 1).

Sete camadas com **hierarquia de decisão**: resumo → críticos → tendências →
explicadores → comparações → riscos → qualidade. Cada bloco carrega ``source_ref``
(§6.3) e a ausência de base é dita explicitamente (``disponivel=false`` + motivo), nunca
preenchida com zero.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.shared.source_ref import SourceRef


# --- 1. Resumo -------------------------------------------------------------
class MudancaRelevante(BaseModel):
    """O que mudou desde o período anterior, com número — não adjetivo."""

    indicador: str
    rotulo: str
    valor_atual: Decimal | None = None
    valor_anterior: Decimal | None = None
    delta_pp: Decimal | None = None  # variação em pontos percentuais (indicador % da RCL)
    faixa_atual: str | None = None
    faixa_anterior: str | None = None
    mudou_de_faixa: bool = False
    periodo_anterior: str | None = None


class CockpitResumo(BaseModel):
    farol: str  # conforme | alerta | prudencial | critico | sem_dados
    cor: str
    indicadores_avaliados: int
    n_alertas: int
    n_alertas_criticos: int
    mudancas_relevantes: list[MudancaRelevante] = Field(default_factory=list)
    source_ref: SourceRef


# --- 2. Indicadores críticos ----------------------------------------------
class CriticoItem(BaseModel):
    indicador: str
    rotulo: str
    sentido: str  # teto | piso
    valor_pct: Decimal | None = None
    valor_rs: Decimal | None = None
    limite_pct: Decimal
    faixa: str | None = None
    cor: str
    distancia_pp: Decimal | None = None  # folga até o teto (ou acima do piso), em p.p.
    source_ref: SourceRef


# --- 3. Tendências ---------------------------------------------------------
class PontoSerie(BaseModel):
    periodo: str
    valor: Decimal | None = None


class PontoProjetado(BaseModel):
    periodo: str
    previsto: Decimal
    ic_inferior: Decimal
    ic_superior: Decimal


class TendenciaItem(BaseModel):
    indicador: str
    rotulo: str
    unidade: str
    modelo: str | None = None
    historico: list[PontoSerie] = Field(default_factory=list)
    projecao: list[PontoProjetado] = Field(default_factory=list)
    limite_pct: Decimal | None = None
    # Período projetado em que a série cruza o limite (None = não cruza no horizonte).
    cruzamento_periodo: str | None = None
    disponivel: bool = True
    motivo_indisponivel: str | None = None
    source_ref: SourceRef | None = None


# --- 4. Explicadores (por que mudou) --------------------------------------
class ComponenteVariacao(BaseModel):
    codigo: str
    descricao: str
    atual: Decimal | None = None
    anterior: Decimal | None = None
    delta_abs: Decimal | None = None
    delta_pct: Decimal | None = None


class ExplicadorItem(BaseModel):
    dimensao: str  # receita_origem | despesa_funcao | pessoal_poder
    rotulo: str
    medida: str
    periodo_atual: str
    periodo_anterior: str | None = None
    componentes: list[ComponenteVariacao] = Field(default_factory=list)
    disponivel: bool = True
    motivo_indisponivel: str | None = None
    source_ref: SourceRef | None = None


# --- 5. Comparações --------------------------------------------------------
class ComparacaoItem(BaseModel):
    """Uma base de comparação. Sem base ⇒ ``disponivel=false`` + motivo (nunca zero)."""

    base: str  # periodo_anterior | exercicio_anterior | orcamento | coorte_mediana
    rotulo: str
    indicador: str
    disponivel: bool
    motivo_indisponivel: str | None = None
    valor_atual: Decimal | None = None
    valor_base: Decimal | None = None
    delta_abs: Decimal | None = None
    delta_pct: Decimal | None = None
    referencia: str | None = None  # período/coorte que serviu de base
    source_ref: SourceRef | None = None


# --- 6. Riscos e ações -----------------------------------------------------
class RiscoItem(BaseModel):
    id: str
    severidade: str
    categoria: str
    titulo: str
    motivo_legal: str
    acao_sugerida: str
    prazo: date | None = None
    link: str | None = None
    indicador: str | None = None
    periodo: str | None = None
    source_ref: SourceRef | None = None


# --- 7. Qualidade do dado --------------------------------------------------
class QualidadeFonte(BaseModel):
    fonte: str
    relatorio: str
    cadencia: str
    periodo_mais_recente: str | None = None
    defasagem_periodos: int | None = None
    ultima_carga: datetime | None = None
    n_registros: int = 0
    versao_entrega_vigente: str | None = None
    retificacoes: int = 0  # entregas além da 1ª para o período vigente


class ChecagemAberta(BaseModel):
    """Check de qualidade em falha/aviso que recai sobre os números desta tela."""

    check_codigo: str
    rotulo: str
    status: str
    fonte: str
    periodo: str | None = None
    diferenca: Decimal | None = None
    tolerancia: Decimal | None = None
    motivo: str | None = None


class CockpitQualidade(BaseModel):
    fontes: list[QualidadeFonte] = Field(default_factory=list)
    defasagem_maxima: int | None = None
    confiavel: bool = True
    observacao: str | None = None
    # Sprint 26: a camada de qualidade deixou de falar só de atualidade e passa a dizer
    # se alguma **verificação** sobre estes números está aberta.
    checks_abertos: list[ChecagemAberta] = Field(default_factory=list)
    n_checks_falha: int = 0
    n_checks_aviso: int = 0


# --- Envelope --------------------------------------------------------------
class CockpitResponse(BaseModel):
    cod_ibge: str
    nome: str | None = None
    esfera: str | None = None
    periodo: str
    as_of: datetime | None = None
    resumo: CockpitResumo
    criticos: list[CriticoItem] = Field(default_factory=list)
    tendencias: list[TendenciaItem] = Field(default_factory=list)
    explicadores: list[ExplicadorItem] = Field(default_factory=list)
    comparacoes: list[ComparacaoItem] = Field(default_factory=list)
    riscos: list[RiscoItem] = Field(default_factory=list)
    qualidade: CockpitQualidade
    source_ref: SourceRef
