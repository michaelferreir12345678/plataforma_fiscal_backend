"""Contratos HTTP auditáveis dos mínimos de saúde e educação."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.shared.envelope import DrillEnvelope
from app.shared.source_ref import SourceRef


class MemoriaComponente(BaseModel):
    codigo: str
    rotulo: str
    valor: Decimal
    operacao: Literal["base", "soma", "subtrai", "resultado", "referencia"]
    source_ref: SourceRef
    as_of: datetime


class MemoriaMinimo(BaseModel):
    formula_aplicacao: str
    formula_percentual: str
    estagio_legal: Literal["liquidado", "empenhado"]
    componentes: list[MemoriaComponente]
    regra_expurgo: str
    detalhes: dict[str, Any] = Field(default_factory=dict)
    source_ref: SourceRef
    as_of: datetime


class SerieMinimoItem(BaseModel):
    periodo: str
    exercicio: int
    # Só o 6º bimestre fecha o exercício: um ponto parcial não é comparável a um fechado,
    # e a tela precisa dizer isso em vez de desenhar os dois com o mesmo peso.
    parcial: bool
    estagio_legal: Literal["liquidado", "empenhado"]
    base_impostos_transferencias: Decimal
    despesa_aplicada: Decimal
    pct_aplicado: Decimal
    minimo_pct: Decimal
    abaixo_do_minimo: bool
    source_ref: SourceRef
    as_of: datetime


class SerieMinimoOut(BaseModel):
    """Série plurianual de um mínimo — um ponto por exercício (Sprint 25C).

    A cobertura viaja junto: dizer "5 anos" e devolver 1 ponto sem explicar por quê é
    o mesmo que esconder a lacuna de ingestão.
    """

    cod_ibge: str
    periodo: str
    indicador: Literal["saude", "educacao"]
    minimo_pct: Decimal
    anos_solicitados: int
    exercicios_com_dado: list[int] = Field(default_factory=list)
    exercicios_sem_dado: list[int] = Field(default_factory=list)
    cobertura_completa: bool
    observacao: str | None = None
    # Um ponto por exercício (comparável entre anos).
    data: list[SerieMinimoItem]
    # Acumulado bimestre a bimestre **dentro do exercício consultado**. Responde outra
    # pergunta — "estamos no caminho do piso?" — e por isso não se mistura com a série
    # plurianual: 25% acumulados no 2º bimestre não são comparáveis a 25% no 6º.
    trajetoria_exercicio: list[SerieMinimoItem] = Field(default_factory=list)
    source_ref: SourceRef
    as_of: datetime


class SaudeDetalhe(BaseModel):
    cod_ibge: str
    periodo: str
    esfera: str
    base_impostos_transferencias: Decimal
    despesa_bruta: Decimal
    deducoes_outras: Decimal
    rpnp_sem_lastro: Decimal
    despesa_aplicada: Decimal
    pct_aplicado: Decimal
    minimo_pct: Decimal
    valor_minimo: Decimal
    abaixo_do_minimo: bool
    folga: Decimal
    projecao_pct: Decimal
    fonte_primaria: Literal["RREO"] = "RREO"
    versao_entrega: str
    source_ref: SourceRef
    source_ref_expurgo: SourceRef | None = None
    as_of: datetime
    memoria_calculo: MemoriaMinimo
    serie: list[SerieMinimoItem] = Field(default_factory=list)


class FundebDetalhe(BaseModel):
    base: Decimal
    aplicado_profissionais: Decimal
    pct_aplicado: Decimal
    minimo_pct: Decimal
    valor_minimo: Decimal
    abaixo_do_minimo: bool
    source_ref: SourceRef
    as_of: datetime


class EducacaoDetalhe(BaseModel):
    cod_ibge: str
    periodo: str
    esfera: str
    base_impostos_transferencias: Decimal
    despesa_bruta: Decimal
    despesa_impostos: Decimal
    despesa_fundeb: Decimal
    deducoes_outras: Decimal
    rpnp_sem_lastro: Decimal
    despesa_aplicada: Decimal
    pct_aplicado: Decimal
    minimo_pct: Decimal
    valor_minimo: Decimal
    abaixo_do_minimo: bool
    folga: Decimal
    projecao_pct: Decimal
    fundeb: FundebDetalhe
    fonte_primaria: Literal["RREO"] = "RREO"
    versao_entrega: str
    source_ref: SourceRef
    source_ref_expurgo: SourceRef | None = None
    as_of: datetime
    memoria_calculo: MemoriaMinimo
    serie: list[SerieMinimoItem] = Field(default_factory=list)


class MinimoArvoreOut(DrillEnvelope):
    as_of: datetime


class EnriquecimentoItem(BaseModel):
    codigo: str
    descricao: str | None = None
    valor: Decimal | None = None
    unidade: str | None = None
    periodo: str | None = None
    source_ref: SourceRef
    as_of: datetime


class EnriquecimentoDetalhe(BaseModel):
    cod_ibge: str
    periodo_solicitado: str
    periodo_fonte: str | None = None
    ultima_atualizacao: datetime | None = None
    defasado: bool
    defasagem_bimestres: int | None = None
    selo: Literal["atualizado", "defasado", "indisponivel"]
    nao_substitui_base: Literal[True] = True
    itens: list[EnriquecimentoItem] = Field(default_factory=list)
    source_ref: SourceRef | None = None
    as_of: datetime


class ProjecaoIndicador(BaseModel):
    indicador: Literal["saude", "educacao"]
    periodo: str
    pct_atual: Decimal
    pct_projetado: Decimal
    minimo_pct: Decimal
    valor_aplicado_projetado: Decimal
    valor_minimo_projetado: Decimal
    abaixo_do_minimo_projetado: bool
    metodo: str
    source_ref: SourceRef
    as_of: datetime


class ProjecaoSerieItem(BaseModel):
    periodo: str
    saude_pct: Decimal | None = None
    educacao_pct: Decimal | None = None
    source_ref_saude: SourceRef | None = None
    source_ref_educacao: SourceRef | None = None
    as_of: datetime


class MinimosProjecao(BaseModel):
    cod_ibge: str
    periodo: str
    saude: ProjecaoIndicador
    educacao: ProjecaoIndicador
    serie: list[ProjecaoSerieItem]
    source_ref: SourceRef
    as_of: datetime
