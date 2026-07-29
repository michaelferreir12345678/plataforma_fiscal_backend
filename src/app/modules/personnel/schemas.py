"""Schemas do módulo de pessoal (detalhe, drill, memória, por-poder)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.modules.indicators.schemas import SerieAjuste
from app.shared.envelope import DrillChild, DrillNodeRef
from app.shared.source_ref import SourceRef


class PessoalTotais(BaseModel):
    """Totais consolidados do ente (soma dos poderes)."""

    despesa_bruta: Decimal | None = None
    exclusoes: Decimal | None = None
    despesa_liquida: Decimal | None = None
    pct_rcl: Decimal | None = None


class PoderItem(BaseModel):
    """Despesa com pessoal de um poder, com faixa quando há teto cadastrado."""

    poder_codigo: str
    descricao: str
    despesa_bruta: Decimal | None = None
    exclusoes: Decimal | None = None
    despesa_liquida: Decimal | None = None
    pct_rcl: Decimal | None = None
    faixa: str | None = None
    teto_pct: Decimal | None = None
    indicador: str | None = None


class SeriePessoalItem(BaseModel):
    periodo: str
    despesa_liquida: Decimal | None = None
    pct_rcl: Decimal | None = None
    # Comparabilidade multi-exercício (Sprint 25): a folha em preços do período e por habitante.
    despesa_liquida_real: Decimal | None = None
    despesa_liquida_per_capita: Decimal | None = None
    populacao: int | None = None


class ComparacaoPessoal(BaseModel):
    """Comparação com o mesmo período do exercício anterior."""

    periodo_anterior: str
    despesa_liquida_anterior: Decimal | None = None
    delta_pct: Decimal | None = None


class PessoalDetalhe(BaseModel):
    """Cabeçalho + composição (por poder) + série + comparação — Padrão de Detalhe."""

    cod_ibge: str
    periodo: str  # RGF quadrimestral (base do Anexo 1)
    # Período RREO correspondente (Q1→B2, Q2→B4, Q3→B6): é nele que a RCL e o
    # mart_indicador do limite são apurados — a tela precisa dele para pedir /rcl e
    # simular o limite no mesmo período em que o indicador existe.
    periodo_rreo: str | None = None
    versao_entrega: str
    esfera: str | None = None
    rpps: bool
    totais: PessoalTotais
    rcl_12m: Decimal | None = None
    executivo: PoderItem | None = None  # indicador-assinatura (teto 54%/49% da RCL)
    composicao: list[DrillChild]  # poderes (drill DOWN a partir da raiz)
    serie: list[SeriePessoalItem]
    serie_ajuste: SerieAjuste | None = None  # deflator IPCA + população
    comparacao: ComparacaoPessoal | None = None
    periodo_breadcrumb: list[DrillNodeRef]  # drill temporal UP
    source_ref: SourceRef


class ExclusaoItem(BaseModel):
    """Componente de exclusão (art. 19, §1º), com marca de inclusão sensível a RPPS."""

    componente: str
    valor: Decimal
    incluida: bool
    motivo: str | None = None


class MemoriaPessoal(BaseModel):
    """Memória rastreável: bruta, exclusões (com/sem RPPS), líquida e cross-check reportado."""

    cod_ibge: str
    periodo: str
    versao_entrega: str
    rpps: bool
    despesa_bruta: Decimal | None = None
    exclusoes: Decimal | None = None
    despesa_liquida: Decimal | None = None
    despesa_liquida_reportada: Decimal | None = None  # linha (III) do RGF, para conferência
    exclusoes_reportadas: Decimal | None = None  # linha (II) do RGF
    rcl_12m: Decimal | None = None
    pct_rcl: Decimal | None = None
    formula_liquida: str
    formula_pct: str
    exclusoes_detalhe: list[ExclusaoItem]
    hierarquia: str
    detalhes: dict[str, Any] = Field(default_factory=dict)
    source_ref: SourceRef


class PorPoderOut(BaseModel):
    cod_ibge: str
    periodo: str
    versao_entrega: str
    esfera: str | None = None
    rpps: bool
    consolidado: PoderItem
    itens: list[PoderItem]
    source_ref: SourceRef
