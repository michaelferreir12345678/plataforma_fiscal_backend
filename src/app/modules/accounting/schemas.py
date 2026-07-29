"""Schemas de Patrimônio & MSC (Sprint 12): matriz mensal, balanços, conciliação."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.shared.envelope import DrillNodeRef
from app.shared.source_ref import SourceRef


class MesSaldo(BaseModel):
    """Saldo de uma conta num mês (convenção natural da classe, para exibição)."""

    mes: int
    periodo: str
    saldo_inicial: Decimal | None = None
    saldo_final: Decimal | None = None
    movimento: Decimal | None = None  # saldo_final − saldo_inicial


class MatrizMensalOut(BaseModel):
    """Matriz mensal (12 meses) de uma conta PCASP — série de saldos ao longo do exercício."""

    cod_ibge: str
    ano: int
    as_of: str | None = None
    versao_entrega: str
    cod_conta: str
    descricao: str
    nivel: int
    classe: int
    natureza: str  # D | C
    breadcrumb: list[DrillNodeRef] = Field(default_factory=list)
    meses: list[MesSaldo]
    saldo_abertura: Decimal | None = None  # 1º mês disponível
    saldo_encerramento: Decimal | None = None  # último mês disponível
    variacao_exercicio: Decimal | None = None
    memoria: dict[str, Any] = Field(default_factory=dict)
    source_ref: SourceRef


class BalancoLinha(BaseModel):
    cod_conta: str
    descricao: str | None = None
    coluna: str | None = None
    valor: Decimal | None = None
    nivel: int | None = None
    parent_conta: str | None = None
    natureza: str | None = None


class BalancoOut(BaseModel):
    """Balanço da DCA (Patrimonial / Variações / Orçamentário) — visão tabular do exercício."""

    cod_ibge: str
    ano: int
    tipo: str
    anexo: str | None = None
    as_of: str | None = None
    versao_entrega: str
    destaques: dict[str, Decimal | None] = Field(default_factory=dict)
    linhas: list[BalancoLinha]
    memoria: dict[str, Any] = Field(default_factory=dict)
    source_ref: SourceRef


class BalancoTipoDisponivel(BaseModel):
    tipo: str
    rotulo: str
    anexo: str | None = None
    disponivel: bool


class BalancosIndex(BaseModel):
    """Índice dos balanços disponíveis para o ente/ano (para a navegação da tela)."""

    cod_ibge: str
    ano: int
    versao_entrega: str | None = None
    tipos: list[BalancoTipoDisponivel]
    source_ref: SourceRef | None = None


class ConciliacaoCheck(BaseModel):
    """Um teste de conciliação: compara dois lados e sinaliza divergência acima da tolerância."""

    chave: str
    titulo: str
    descricao: str
    esquerda_rotulo: str
    esquerda: Decimal | None = None
    direita_rotulo: str
    direita: Decimal | None = None
    diferenca: Decimal | None = None
    tolerancia: Decimal
    divergente: bool
    aplicavel: bool = True
    detalhe: str | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)


class ConciliacaoOut(BaseModel):
    """Conciliação do patrimônio: rollup (pai = Σ filhos) e cruzamento entre fontes."""

    cod_ibge: str
    ano: int
    as_of: str | None = None
    tem_msc: bool
    tem_dca: bool
    n_checks: int
    n_divergencias: int
    conciliado: bool
    checks: list[ConciliacaoCheck]
    observacao: str
    memoria: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[SourceRef] = Field(default_factory=list)


class CoberturaPatrimonio(BaseModel):
    """O que existe (e o que não existe) de patrimônio para este ente — Sprint 25D.

    Substitui o seletor de entes-demo da tela: em vez de trocar silenciosamente o ente
    por um que tem dado, a página mostra o ente do contexto e diz exatamente o que falta
    ingerir. Ausência aparece como ausência.
    """

    tem_dca: bool
    tem_msc: bool
    anos_dca: list[int] = Field(default_factory=list)
    meses_msc: list[str] = Field(default_factory=list)
    fontes_ausentes: list[str] = Field(default_factory=list)
    mensagem: str


class ComparacaoLinha(BaseModel):
    """Uma conta do balanço lida em vários exercícios."""

    cod_conta: str
    descricao: str | None = None
    nivel: int | None = None
    valores: dict[str, Decimal | None] = Field(default_factory=dict)  # ano -> valor
    variacao_abs: Decimal | None = None  # último − primeiro exercício com valor
    variacao_pct: Decimal | None = None
    anos_com_valor: list[int] = Field(default_factory=list)


class BalancoComparacaoOut(BaseModel):
    """Comparação anual de um balanço da DCA (Sprint 25D).

    Um balanço isolado diz a posição; a série diz a direção. Exercícios sem a conta
    ficam nulos — nunca zerados —, e a variação só é calculada quando existem os dois
    extremos.
    """

    cod_ibge: str
    tipo: str
    anexo: str | None = None
    anos: list[int] = Field(default_factory=list)
    anos_sem_balanco: list[int] = Field(default_factory=list)
    observacao: str | None = None
    linhas: list[ComparacaoLinha] = Field(default_factory=list)
    memoria: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[SourceRef] = Field(default_factory=list)


class PatrimonioDetalhe(BaseModel):
    """Cabeçalho do Padrão de Detalhe: posição patrimonial + estado das fontes."""

    cod_ibge: str
    ano: int
    as_of: str | None = None
    esfera: str | None = None
    uf: str | None = None
    tem_msc: bool
    tem_dca: bool
    ativo: Decimal | None = None
    passivo_pl: Decimal | None = None
    patrimonio_liquido: Decimal | None = None
    vpd: Decimal | None = None  # variação patrimonial diminutiva (classe 3)
    vpa: Decimal | None = None  # variação patrimonial aumentativa (classe 4)
    resultado_patrimonial: Decimal | None = None  # VPA − VPD
    balanco_fechado: bool | None = None  # Ativo == Passivo+PL
    meses_msc: list[str] = Field(default_factory=list)
    anos_disponiveis: list[int] = Field(default_factory=list)
    conciliado: bool | None = None
    n_divergencias: int | None = None
    cobertura: CoberturaPatrimonio | None = None
    source_ref: SourceRef | None = None
