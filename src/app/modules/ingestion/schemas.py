"""Schemas Pydantic do módulo de ingestão (admin)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.shared.source_ref import SourceRef


class RunRequest(BaseModel):
    """Backfill controlado de uma fonte.

    SICONFI usa ``entes``/``anos``/``periodos``; complementares usam o que faz sentido
    (BCB usa ``series`` + janela ``data_inicial``/``data_final``; IBGE/SADIPEM usam
    ``entes``/``anos``). Fontes sem versão própria usam a data de captura como versão.
    """

    fonte: str = Field(description="Ex.: siconfi_rreo, sadipem_pvl, bcb, ibge_populacao, ...")
    entes: list[str] = Field(default_factory=list, description="Códigos IBGE.")
    anos: list[int] = Field(default_factory=list)
    periodos: list[int] | None = Field(
        default=None, description="Nº do período (bimestre/quadrimestre/mês). Default = todos."
    )
    versao: str | None = Field(
        default=None, description="Versão da entrega; se ausente, usa a data de captura."
    )
    homologada_em: datetime | None = Field(
        default=None, description="Data de homologação da entrega (retificação usa data maior)."
    )
    # Fontes complementares
    series: list[int] | None = Field(default=None, description="Códigos de série do SGS/BCB.")
    data_inicial: date | None = Field(default=None, description="Janela inicial (BCB/SADIPEM).")
    data_final: date | None = Field(default=None, description="Janela final (BCB/SADIPEM).")
    # Fontes de arquivo (planilhas): url do arquivo, escopo, formato, etc.
    params: dict[str, Any] = Field(
        default_factory=dict, description="Parâmetros extras (ex.: url, escopo, formato)."
    )
    force: bool = Field(default=False, description="Reprocessa o silver mesmo sem novo bronze.")


class RunResult(BaseModel):
    fonte: str
    total_jobs: int
    ingeridos: int
    pulados: int
    silver_rows: int
    versoes_vigentes: list[str]


class EntregaStatus(BaseModel):
    cod_ibge: str
    relatorio: str
    periodo: str
    versao_entrega: str
    vigente: bool
    homologada_em: datetime


class DataResponse(BaseModel):
    """Leitura silver 'as of' (§6.5): versão vigente ou histórica conforme ``as_of``."""

    fonte: str
    cod_ibge: str
    periodo: str
    versao_entrega: str | None
    as_of: datetime | None
    total: int
    rows: list[dict[str, Any]]
    source_ref: SourceRef | None = None
