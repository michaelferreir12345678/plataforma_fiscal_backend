"""Schemas do módulo de receita (detalhe, drill, memória, dependência, realização, conciliação)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.modules.indicators.schemas import SerieAjuste
from app.shared.envelope import DrillChild, DrillNodeRef
from app.shared.source_ref import SourceRef


class ReceitaTotais(BaseModel):
    """Totais do ente/período (soma das categorias econômicas)."""

    previsto_inicial: Decimal | None = None
    previsto_atualizado: Decimal | None = None
    arrecadado_bimestre: Decimal | None = None
    arrecadado_acum: Decimal | None = None
    deducoes: Decimal | None = None


class DependenciaResumo(BaseModel):
    """Receita própria × transferida (origens 1.7/2.4) sobre o arrecadado acumulado."""

    propria: Decimal
    transferida: Decimal
    total: Decimal
    pct_propria: Decimal | None = None
    pct_transferida: Decimal | None = None


class SerieReceitaItem(BaseModel):
    periodo: str
    arrecadado_acum: Decimal | None = None
    # Comparabilidade multi-exercício (Sprint 25): nominal × real × por habitante.
    arrecadado_real: Decimal | None = None  # a preços do período consultado (IPCA)
    arrecadado_per_capita: Decimal | None = None
    populacao: int | None = None


class ComparacaoOut(BaseModel):
    """Comparação com o mesmo bimestre do exercício anterior."""

    periodo_anterior: str
    arrecadado_acum_anterior: Decimal | None = None
    delta_pct: Decimal | None = None


class ReceitaDetalhe(BaseModel):
    """Cabeçalho + composição (raiz) + série + comparação — Padrão de Detalhe de Indicador."""

    cod_ibge: str
    periodo: str
    as_of: datetime | None = None
    versao_entrega: str
    totais: ReceitaTotais
    realizacao_pct: Decimal | None = None
    rcl_12m: Decimal | None = None
    dependencia: DependenciaResumo
    composicao: list[DrillChild]  # raízes (categorias econômicas) com medidas
    serie: list[SerieReceitaItem]
    serie_ajuste: SerieAjuste | None = None  # deflator IPCA + população (§ comparabilidade)
    comparacao: ComparacaoOut | None = None
    periodo_breadcrumb: list[DrillNodeRef]  # drill temporal UP (bimestre → ano)
    source_ref: SourceRef


class InconsistenciaAgregacao(BaseModel):
    """Nó cujo valor difere da soma dos filhos (sinal de qualidade de dado)."""

    codigo: str
    medida: str
    valor_no: Decimal
    soma_filhos: Decimal


class MemoriaReceita(BaseModel):
    """Memória de cálculo rastreável: colunas mapeadas, totais e verificação de agregação."""

    cod_ibge: str
    periodo: str
    as_of: datetime | None = None
    versao_entrega: str
    medidas: list[str]
    totais: ReceitaTotais
    formula_realizacao: str
    hierarquia: str
    inconsistencias: list[InconsistenciaAgregacao]
    detalhes: dict[str, Any] = Field(default_factory=dict)
    source_ref: SourceRef


class TransferenciaTop(BaseModel):
    codigo: str
    descricao: str
    arrecadado_acum: Decimal
    pct_das_transferencias: Decimal | None = None


class DependenciaOut(BaseModel):
    cod_ibge: str
    periodo: str
    as_of: datetime | None = None
    versao_entrega: str
    resumo: DependenciaResumo
    maiores_transferencias: list[TransferenciaTop]
    rcl_12m: Decimal | None = None
    source_ref: SourceRef


class RealizacaoItem(BaseModel):
    codigo: str
    descricao: str
    previsto_atualizado: Decimal | None = None
    arrecadado_acum: Decimal | None = None
    realizacao_pct: Decimal | None = None


class RealizacaoOut(BaseModel):
    cod_ibge: str
    periodo: str
    as_of: datetime | None = None
    versao_entrega: str
    total: RealizacaoItem
    por_categoria: list[RealizacaoItem]
    source_ref: SourceRef


class ConciliacaoItem(BaseModel):
    """RREO × fonte externa (1B) para uma transferência; divergência = qualidade de dado."""

    transferencia: str
    fonte_externa: str
    rreo_acum: Decimal | None = None
    externo_acum: Decimal | None = None
    divergencia_pct: Decimal | None = None
    divergencia_rs: Decimal | None = None
    # conciliado | divergente | contido | excede_agregado | sem_dado_externo | sem_par_rreo
    status: str
    # Como o lado RREO foi obtido: linha própria da transferência ou o agregado que a
    # contém (nem todo ente abre FPM/FUNDEB no Anexo 01) — muda a leitura do resultado.
    base_comparacao: str = "linha_especifica"  # linha_especifica | agregado | ausente
    participacao_no_agregado_pct: Decimal | None = None
    # Fonte dos DOIS lados (aceite da Sprint 25A): sem isto a divergência é inauditável.
    tabela_externa: str  # tabela silver que sustenta o lado externo
    periodo_externo: str | None = None  # janela agregada na fonte externa (ano/meses)
    nos_rreo: list[str] = Field(default_factory=list)  # códigos casados no RREO A1
    independente: bool = True  # False ⇒ derivado do próprio RREO (não é contraprova)


class ConciliacaoOut(BaseModel):
    cod_ibge: str
    periodo: str
    as_of: datetime | None = None
    versao_entrega: str
    itens: list[ConciliacaoItem]
    tolerancia_pct: Decimal
    observacao: str
    source_ref: SourceRef
