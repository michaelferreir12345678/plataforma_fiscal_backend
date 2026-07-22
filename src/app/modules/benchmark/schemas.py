"""Contratos HTTP do módulo de benchmarking."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.shared.source_ref import SourceRef

Sentido = Literal["menor_melhor", "maior_melhor", "neutro"]
OrdenacaoRanking = Literal["posicao", "valor", "percentil", "nome", "cod_ibge"]
OrdemRanking = Literal["asc", "desc"]


class CoorteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    codigo: str
    criterio: Literal["porte", "regiao", "pib"]
    faixa: str
    rotulo: str
    unidade_criterio: str | None = None
    limite_inferior: Decimal | None = None
    limite_superior: Decimal | None = None
    inclusivo_superior: bool = False
    ordem: int
    source_ref: SourceRef


class IndicadorDisponivel(BaseModel):
    codigo: str
    rotulo: str
    unidade: str
    sentido: Sentido


class DistribuicaoBenchmark(BaseModel):
    minimo: Decimal
    p10: Decimal
    p25: Decimal
    mediana: Decimal
    p75: Decimal
    p90: Decimal
    maximo: Decimal


class CoberturaBenchmark(BaseModel):
    entes_elegiveis: int
    entes_com_valor: int
    entes_sem_valor: int
    percentual: Decimal
    amostra_parcial: bool


class BenchmarkValue(BaseModel):
    cod_ibge: str
    nome: str | None = None
    uf: str | None = None
    valor: Decimal
    percentil: Decimal
    posicao: int
    faixa: str | None = None
    destaque: bool = False
    as_of: datetime
    source_ref: SourceRef
    memoria: dict[str, Any] | None = None


class BenchmarkResponse(BaseModel):
    indicador: str
    indicador_rotulo: str
    unidade: str
    sentido: Sentido
    periodo: str
    as_of: datetime
    coorte: CoorteOut
    coortes_disponiveis: list[CoorteOut] = Field(default_factory=list)
    indicadores_disponiveis: list[IndicadorDisponivel] = Field(default_factory=list)
    quantidade: int
    cobertura: CoberturaBenchmark
    distribuicao: DistribuicaoBenchmark
    ente: BenchmarkValue
    memoria: dict[str, Any]
    source_refs: list[SourceRef] = Field(default_factory=list)


class BenchmarkRankingResponse(BaseModel):
    indicador: str
    indicador_rotulo: str
    unidade: str
    sentido: Sentido
    periodo: str
    as_of: datetime
    coorte: CoorteOut
    coortes_disponiveis: list[CoorteOut] = Field(default_factory=list)
    indicadores_disponiveis: list[IndicadorDisponivel] = Field(default_factory=list)
    ordenar: OrdenacaoRanking
    ordem: OrdemRanking
    itens: list[BenchmarkValue] = Field(default_factory=list)
    ente_ancora: BenchmarkValue
    total: int
    pagina: int
    por_pagina: int
    total_paginas: int
    cobertura: CoberturaBenchmark
    memoria: dict[str, Any]
    source_refs: list[SourceRef] = Field(default_factory=list)
