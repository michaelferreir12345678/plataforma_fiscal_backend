"""Schemas da visão agregada de carteira (Módulo 2, Sprint 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.shared.source_ref import SourceRef


class IndicadorFaixa(BaseModel):
    """Faixa de um indicador para um ente (linha da grade / drill do ente)."""

    indicador: str
    faixa: str | None = None
    cor: str
    valor_pct: Decimal | None = None
    conformidade_status: str


class CarteiraEnteRow(BaseModel):
    """Linha da grade/ranking: um ente com sua pior conformidade e faixas por indicador."""

    cod_ibge: str
    nome: str | None = None
    uf: str | None = None
    regiao: str | None = None
    porte: str | None = None
    populacao: int | None = None
    grupo: str | None = None
    tag: str | None = None
    conformidade: str  # conforme | alerta | prudencial | critico | sem_dados
    cor: str
    risco_score: int  # soma das severidades (maior = mais crítico) — para o ranking
    indicadores: list[IndicadorFaixa] = Field(default_factory=list)


class ResumoIndicador(BaseModel):
    """Consolidação de um indicador no escopo: contagem por faixa e por conformidade."""

    indicador: str
    total: int
    por_faixa: dict[str, int] = Field(default_factory=dict)
    por_conformidade: dict[str, int] = Field(default_factory=dict)


class CarteiraResumoResponse(BaseModel):
    """Consolidação da carteira no escopo do usuário (drill UP do ente)."""

    periodo: str
    total_entes: int  # entes no escopo (universo)
    entes_com_dados: int
    por_conformidade: dict[str, int]  # cada ente contado uma vez pela pior conformidade
    por_indicador: list[ResumoIndicador]
    source_ref: SourceRef


class MapaEnte(BaseModel):
    """Cor de um ente para o coroplético."""

    cod_ibge: str
    uf: str | None = None
    faixa: str | None = None
    cor: str
    valor_pct: Decimal | None = None
    conformidade_status: str


class CarteiraMapaResponse(BaseModel):
    """Dados do mapa: uma cor por ente. ``indicador`` ausente ⇒ pior conformidade do ente."""

    periodo: str
    indicador: str | None = None
    legenda: dict[str, str]  # faixa/conformidade -> cor
    entes: list[MapaEnte]
    source_ref: SourceRef


class CarteiraLoteRequest(BaseModel):
    """Seleção da ação em lote. ``entes`` ausente/vazio ⇒ todo o escopo."""

    entes: list[str] | None = None
    periodo: str | None = None


class LoteJobOut(BaseModel):
    """Job de lote enfileirado."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    acao: str
    status: str
    periodo: str | None = None
    total_entes: int
    entes: list[str] = Field(default_factory=list)
    criado_em: datetime | None = None
