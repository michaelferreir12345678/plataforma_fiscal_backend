"""Schemas do Resultado Fiscal (detalhe, cascata, reconciliação, meta, memória)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.indicators.schemas import SerieAjuste
from app.shared.envelope import DrillNodeRef
from app.shared.source_ref import SourceRef


class ResultadoValores(BaseModel):
    """Componentes do resultado primário e nominal (acima e abaixo da linha)."""

    receita_primaria: Decimal | None = None
    despesa_primaria: Decimal | None = None
    resultado_primario: Decimal | None = None
    juros_liquidos: Decimal | None = None
    resultado_nominal: Decimal | None = None
    dcl_inicio: Decimal | None = None
    dcl_fim: Decimal | None = None
    variacao_dcl: Decimal | None = None
    resultado_nominal_abaixo: Decimal | None = None
    resultado_primario_abaixo: Decimal | None = None
    meta_primario: Decimal | None = None
    meta_nominal: Decimal | None = None


class ComponenteOut(BaseModel):
    codigo: str
    descricao: str
    valor: Decimal


class SerieResultadoItem(BaseModel):
    periodo: str
    resultado_primario: Decimal | None = None
    resultado_nominal: Decimal | None = None
    # Comparabilidade multi-exercício (Sprint 25): a preços do período consultado.
    resultado_primario_real: Decimal | None = None
    resultado_primario_per_capita: Decimal | None = None
    populacao: int | None = None


class ComparacaoResultado(BaseModel):
    periodo_anterior: str
    resultado_primario_anterior: Decimal | None = None
    delta_rs: Decimal | None = None


class MetaResumo(BaseModel):
    """Realizado × meta fiscal (LDO). ``informada`` = a meta veio no Anexo 6."""

    informada: bool
    meta_primario: Decimal | None = None
    realizado_primario: Decimal | None = None
    esforco_primario: Decimal | None = None  # meta − realizado (positivo = falta)
    atingido_primario: bool | None = None
    meta_nominal: Decimal | None = None
    realizado_nominal: Decimal | None = None


class ResultadoDetalhe(BaseModel):
    """Cabeçalho (resultado × meta) + componentes primários + série — Padrão de Detalhe."""

    cod_ibge: str
    periodo: str
    versao_entrega: str
    valores: ResultadoValores
    meta: MetaResumo
    receita_componentes: list[ComponenteOut]  # drill: componentes primários (receita)
    despesa_componentes: list[ComponenteOut]  # drill: componentes primários (despesa)
    serie: list[SerieResultadoItem]
    serie_ajuste: SerieAjuste | None = None  # deflator IPCA + população (§ comparabilidade)
    comparacao: ComparacaoResultado | None = None
    periodo_breadcrumb: list[DrillNodeRef]
    source_ref: SourceRef


class CascataPasso(BaseModel):
    """Passo da cascata (waterfall): base, subtração, subtotal ou resultado."""

    rotulo: str
    valor: Decimal | None = None
    tipo: str  # base | subtrai | soma | subtotal | resultado
    acumulado: Decimal | None = None


class CascataOut(BaseModel):
    cod_ibge: str
    periodo: str
    versao_entrega: str
    acima_da_linha: list[CascataPasso]  # receita→−despesa→primário→−juros→nominal
    abaixo_da_linha: list[CascataPasso]  # DCL início→+variação→DCL fim (= −nominal)
    source_ref: SourceRef


class ReconAjuste(BaseModel):
    codigo: str
    descricao: str
    valor: Decimal


class ReconciliacaoOut(BaseModel):
    """Acima × abaixo da linha, com ajustes metodológicos e cruzamento com a Sprint 8."""

    cod_ibge: str
    periodo: str
    versao_entrega: str
    nominal_acima: Decimal | None = None  # XXXVIII (primário − juros)
    nominal_abaixo: Decimal | None = None  # XLIII (= início − fim da DCL)
    ajustes: list[ReconAjuste]
    soma_ajustes: Decimal
    nominal_abaixo_ajustado: Decimal | None = None  # L (reportado, ou XLIII + Σ ajustes)
    divergencia_acima_abaixo: Decimal | None = None  # acima − abaixo_ajustado
    variacao_dcl: Decimal | None = None
    dcl_inicio: Decimal | None = None
    dcl_fim: Decimal | None = None
    dcl_sprint8: Decimal | None = None  # DCL do módulo Dívida (RGF) — cruzamento
    dcl_sprint8_periodo: str | None = None
    concilia_com_sprint8: bool | None = None
    identidade_primario_ok: bool | None = None  # primário == receita − despesa
    identidade_nominal_dcl_ok: bool | None = None  # XLIII == início − fim (= −ΔDCL)
    observacao: str
    source_ref: SourceRef


class MetaCadastro(BaseModel):
    """Meta da LDO declarada pela organização (quando o Anexo 6 não a publica)."""

    id: uuid.UUID
    exercicio: int
    indicador: str  # primario | nominal
    valor: Decimal
    fonte_declarada: str
    observacao: str | None = None
    atualizado_em: datetime
    atualizado_por: uuid.UUID | None = None


class MetaFiscalUpsert(BaseModel):
    """Cadastro/atualização da meta da LDO (exige capacidade de administrar)."""

    exercicio: int = Field(ge=1990, le=2200)
    indicador: Literal["primario", "nominal"]
    valor: Decimal
    fonte_declarada: str = Field(min_length=3, max_length=400)
    observacao: str | None = Field(default=None, max_length=1000)


class MetaOut(BaseModel):
    """Realizado × meta + projeção de fechamento e esforço necessário.

    ``origem`` diz de onde veio a meta: ``a6`` (oficial, publicada pelo ente), ``manual``
    (cadastro da organização — **restrito às telas do ente**, nunca em agregado ou
    relatório institucional) ou ``ausente``.
    """

    cod_ibge: str
    periodo: str
    versao_entrega: str
    resumo: MetaResumo
    bimestre: int
    fracao_exercicio: Decimal  # bimestre / 6
    projecao_primario: Decimal | None = None  # fechamento estimado (linear)
    esforco_necessario: Decimal | None = None  # meta − projeção
    origem: str = "ausente"  # a6 | manual | ausente
    restrita_ao_ente: bool = False  # True ⇒ meta manual, fora de agregados/relatórios
    cadastros: list[MetaCadastro] = Field(default_factory=list)
    observacao: str
    source_ref: SourceRef


class MemoriaResultado(BaseModel):
    """Memória rastreável: componentes, fórmulas, identidades e origem de cada número."""

    cod_ibge: str
    periodo: str
    versao_entrega: str
    valores: ResultadoValores
    ajustes: list[ReconAjuste]
    formula_primario: str
    formula_nominal: str
    formula_nominal_abaixo: str
    identidade_primario_ok: bool | None = None
    identidade_nominal_dcl_ok: bool | None = None
    fontes: dict[str, str]  # medida → linha/coluna de origem no Anexo 6
    detalhes: dict[str, Any] = Field(default_factory=dict)
    source_ref: SourceRef
