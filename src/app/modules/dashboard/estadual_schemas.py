"""Schemas da Visão Estadual & Consolidação Territorial (Módulo 2, Sprint 23).

A rotulagem separa **sempre** três coisas que a auditoria (§§2.2/6) pediu para nunca
confundir: o *ente estadual* (Governo do Estado), o *consolidado dos municípios* da UF e a
*carteira* do usuário. O consolidado carrega numerador/denominador explícitos e a cobertura
como dado (n/total, ausentes, períodos mistos).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.shared.envelope import DrillEnvelope
from app.shared.source_ref import SourceRef


class EnteRef(BaseModel):
    """Referência mínima a um ente (o estadual, para linkar sem misturar com o consolidado)."""

    cod_ibge: str
    nome: str | None = None


class IndicadorConsolidado(BaseModel):
    """Um indicador consolidado da UF: Σnumerador/Σdenominador (nunca média de %)."""

    indicador: str
    rotulo: str
    tipo: str  # "ratio" (percentual do denominador) | "absoluto" (soma em R$)
    unidade: str  # "PCT_RCL" | "BRL"
    numerador: Decimal | None = None
    denominador: Decimal | None = None
    valor_pct: Decimal | None = None
    teto_pct: Decimal | None = None
    sentido: str | None = None  # teto | piso
    faixa: str | None = None
    cor: str
    # Cobertura do próprio indicador (pode diferir entre indicadores/relatórios).
    n_entes_total: int
    n_entes_com_dado: int
    cobertura_pct: Decimal | None = None
    entes_ausentes: list[str] = Field(default_factory=list)
    periodos_mistos: bool = False
    versao_calculo: str = "v1"
    source_ref: SourceRef


class ConsolidadoUfResponse(BaseModel):
    """Consolidado territorial dos **municípios** da UF (exclui o ente estadual)."""

    uf: str
    uf_nome: str | None = None
    periodo: str
    escopo: str = "municipios_consolidado"
    # O ente estadual é uma coisa distinta — vem referenciado, nunca somado ao consolidado.
    ente_estadual: EnteRef | None = None
    n_municipios: int
    n_municipios_com_dado: int
    cobertura_pct: Decimal | None = None
    indicadores: list[IndicadorConsolidado] = Field(default_factory=list)
    observacao: str
    source_ref: SourceRef


class RankingItem(BaseModel):
    """Uma linha do ranking municipal, com percentil e destaque."""

    cod_ibge: str
    nome: str | None = None
    regiao: str | None = None
    porte: str | None = None
    populacao: int | None = None
    valor_pct: Decimal | None = None
    valor_rs: Decimal | None = None
    faixa: str | None = None
    cor: str
    posicao: int
    percentil: Decimal | None = None
    destaque: bool = False
    no_escopo: bool = True


class RankingUfResponse(BaseModel):
    """Ranking municipal de um indicador na UF (respeitando o escopo do usuário)."""

    uf: str
    periodo: str
    indicador: str
    rotulo: str
    sentido: str
    unidade: str
    ordenar: str
    n_total: int
    n_com_valor: int
    itens: list[RankingItem] = Field(default_factory=list)
    source_ref: SourceRef


class HistogramaBin(BaseModel):
    faixa_inferior: Decimal
    faixa_superior: Decimal
    contagem: int


class DistribuicaoUfResponse(BaseModel):
    """Histograma + percentis + concentração (top-N % do total) de um indicador na UF."""

    uf: str
    periodo: str
    indicador: str
    rotulo: str
    unidade: str
    n_com_valor: int
    minimo: Decimal | None = None
    p10: Decimal | None = None
    p25: Decimal | None = None
    mediana: Decimal | None = None
    p75: Decimal | None = None
    p90: Decimal | None = None
    maximo: Decimal | None = None
    histograma: list[HistogramaBin] = Field(default_factory=list)
    # Concentração: quanto os maiores respondem do total (só faz sentido para absolutos).
    concentracao_top5_pct: Decimal | None = None
    concentracao_top10_pct: Decimal | None = None
    total: Decimal | None = None
    source_ref: SourceRef


class MapaUfEnte(BaseModel):
    """Valor de um município para o coroplético (ligado à malha por ``cod_ibge``)."""

    cod_ibge: str
    valor_pct: Decimal | None = None
    faixa: str | None = None
    cor: str
    no_escopo: bool = True


class MapaUfResponse(BaseModel):
    """Valores por município + referência à malha (``/geo/malha/{uf}``)."""

    uf: str
    periodo: str
    indicador: str
    rotulo: str
    legenda: dict[str, str]
    malha_ref: str  # caminho do GET da malha
    entes: list[MapaUfEnte] = Field(default_factory=list)
    source_ref: SourceRef


class MalhaResponse(BaseModel):
    """Malha municipal real da UF (GeoJSON do IBGE)."""

    uf: str
    formato: str
    fonte: str | None = None
    ano: int | None = None
    n_areas: int | None = None
    simplificacao: str | None = None
    malha: dict


class ArvoreUfResponse(DrillEnvelope):
    """Drill UF→região/porte→município (§6.1). Cada folha linka ``/entes/{ibge}/cockpit``."""

    uf: str
    indicador: str
    agrupar: str  # regiao | porte
