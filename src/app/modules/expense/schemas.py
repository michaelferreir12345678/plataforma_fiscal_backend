"""Schemas do módulo de despesa (detalhe, drill, memória, estágios, execução, rigidez)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.modules.indicators.schemas import SerieAjuste
from app.shared.envelope import DrillChild, DrillNodeRef
from app.shared.source_ref import SourceRef


class DespesaTotais(BaseModel):
    """Totais dos estágios do ente/período (soma das raízes do eixo)."""

    dotacao_inicial: Decimal | None = None
    dotacao_atualizada: Decimal | None = None
    empenhado: Decimal | None = None
    liquidado: Decimal | None = None
    pago: Decimal | None = None
    inscrito_rap: Decimal | None = None


class SerieDespesaItem(BaseModel):
    periodo: str
    empenhado: Decimal | None = None
    # Comparabilidade multi-exercício (Sprint 25): nominal × real × por habitante.
    empenhado_real: Decimal | None = None  # a preços do período consultado (IPCA)
    empenhado_per_capita: Decimal | None = None
    populacao: int | None = None


class ComparacaoDespesa(BaseModel):
    """Comparação com o mesmo bimestre do exercício anterior."""

    periodo_anterior: str
    empenhado_anterior: Decimal | None = None
    delta_pct: Decimal | None = None


class DespesaDetalhe(BaseModel):
    """Cabeçalho + composição (raiz do eixo) + série + comparação — Padrão de Detalhe."""

    cod_ibge: str
    periodo: str
    versao_entrega: str
    eixo: str
    totais: DespesaTotais
    potencial_rap: Decimal | None = None  # empenhado − pago (lacuna)
    empenhado_pct_rcl: Decimal | None = None
    rcl_12m: Decimal | None = None
    composicao: list[DrillChild]  # raízes do eixo (funções ou categorias) com medidas
    serie: list[SerieDespesaItem]
    serie_ajuste: SerieAjuste | None = None  # deflator IPCA + população (§ comparabilidade)
    comparacao: ComparacaoDespesa | None = None
    periodo_breadcrumb: list[DrillNodeRef]  # drill temporal UP (bimestre → ano)
    source_ref: SourceRef


class InconsistenciaAgregacao(BaseModel):
    """Nó cujo valor difere da soma dos filhos (sinal de qualidade de dado)."""

    eixo: str
    codigo: str
    medida: str
    valor_no: Decimal
    soma_filhos: Decimal


class ViolacaoEstagio(BaseModel):
    """Linha do fato que viola ``empenhado ≥ liquidado ≥ pago``."""

    eixo: str
    codigo: str
    empenhado: Decimal | None = None
    liquidado: Decimal | None = None
    pago: Decimal | None = None
    problema: str


class MemoriaDespesa(BaseModel):
    """Memória de cálculo rastreável: estágios, invariantes e conciliação dos eixos."""

    cod_ibge: str
    periodo: str
    versao_entrega: str
    medidas: list[str]
    totais_funcao: DespesaTotais
    totais_natureza: DespesaTotais
    reconciliacao_eixos_ok: bool  # total por função = total por natureza
    diferenca_eixos: Decimal | None = None  # empenhado_funcao − empenhado_natureza
    formula_potencial_rap: str
    hierarquia_funcao: str
    hierarquia_natureza: str
    inconsistencias: list[InconsistenciaAgregacao]
    violacoes_estagio: list[ViolacaoEstagio]
    detalhes: dict[str, Any] = Field(default_factory=dict)
    source_ref: SourceRef


class EstagioPasso(BaseModel):
    """Passo da cascata de execução (dotação → empenhado → liquidado → pago)."""

    estagio: str
    valor: Decimal | None = None
    delta_anterior: Decimal | None = None  # variação frente ao estágio anterior


class LacunaEstagio(BaseModel):
    """Lacuna entre estágios (crédito a empenhar, a liquidar, a pagar, potencial RAP)."""

    nome: str
    valor: Decimal
    formula: str


class EstagioItem(BaseModel):
    """Estágios de um nó do eixo (para drill), com o potencial de RAP da linha."""

    codigo: str
    descricao: str
    dotacao_atualizada: Decimal | None = None
    empenhado: Decimal | None = None
    liquidado: Decimal | None = None
    pago: Decimal | None = None
    inscrito_rap: Decimal | None = None
    potencial_rap: Decimal | None = None


class EstagiosOut(BaseModel):
    cod_ibge: str
    periodo: str
    versao_entrega: str
    eixo: str
    totais: DespesaTotais
    cascata: list[EstagioPasso]
    lacunas: list[LacunaEstagio]
    por_eixo: list[EstagioItem]
    estagios_ausentes: list[str] = Field(default_factory=list)  # não publicados no anexo
    observacao: str | None = None  # por que a cascata está incompleta, quando estiver
    source_ref: SourceRef


class ExecucaoEstagio(BaseModel):
    """Ritmo de um estágio ante o esperado linear no período (ritmo × calendário)."""

    estagio: str
    executado: Decimal | None = None
    base_dotacao: Decimal | None = None
    executado_pct: Decimal | None = None
    esperado_pct: Decimal
    ritmo_pp: Decimal | None = None  # executado_pct − esperado_pct (pontos percentuais)
    status: str  # adiantado | no_ritmo | atrasado | sem_base


class ExecucaoOut(BaseModel):
    cod_ibge: str
    periodo: str
    versao_entrega: str
    eixo: str = "funcao"  # anexo de origem: função (A02) não publica 'pago'
    bimestre: int
    esperado_pct: Decimal  # fração do exercício decorrida (calendário linear)
    estagios: list[ExecucaoEstagio]
    source_ref: SourceRef


class RigidezComponente(BaseModel):
    grupo: str
    descricao: str
    tipo: str  # rigida | discricionaria | semivariavel
    empenhado: Decimal
    pct_despesa: Decimal | None = None


class RigidezOut(BaseModel):
    """Rigidez orçamentária: peso da despesa obrigatória sobre a despesa empenhada."""

    cod_ibge: str
    periodo: str
    versao_entrega: str
    despesa_total: Decimal
    rigida: Decimal
    discricionaria: Decimal
    semivariavel: Decimal
    rigidez_pct: Decimal | None = None
    discricionaria_pct: Decimal | None = None
    componentes: list[RigidezComponente]
    source_ref: SourceRef
