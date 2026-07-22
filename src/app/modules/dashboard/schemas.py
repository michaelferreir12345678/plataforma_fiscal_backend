"""Schemas do Dashboard Executivo (Módulo 1)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from app.shared.source_ref import SourceRef


class SemaforoItem(BaseModel):
    indicador: str
    faixa: str | None = None
    cor: str  # verde | amarelo | laranja | vermelho | cinza
    valor_pct_rcl: Decimal | None = None
    teto_pct: Decimal | None = None
    sentido: str


class KpiItem(BaseModel):
    chave: str
    rotulo: str
    valor: Decimal | None = None
    unidade: str
    disponivel: bool = True


class DestaqueItem(BaseModel):
    indicador: str
    faixa: str
    cor: str
    mensagem: str


class DashboardResponse(BaseModel):
    cod_ibge: str
    periodo: str
    versao_entrega: str
    conformidade: str  # conforme | alerta | prudencial | critico | sem_dados
    semaforo: list[SemaforoItem]
    kpis: list[KpiItem]
    destaques: list[DestaqueItem]
    source_ref: SourceRef
