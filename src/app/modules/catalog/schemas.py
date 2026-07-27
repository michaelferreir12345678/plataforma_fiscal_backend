"""Schemas do catálogo (dim_ente, dim_limite_legal)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.shared.source_ref import SourceRef


class EnteBusca(BaseModel):
    """Linha do seletor/⌘K: identifica o ente e diz se ele tem dado para abrir."""

    model_config = ConfigDict(from_attributes=True)

    cod_ibge: str
    nome: str | None = None
    uf: str | None = None
    esfera: str | None = None
    populacao: int | None = None
    tem_dado: bool = False
    periodo_mais_recente: str | None = None


class EntesBuscaResponse(BaseModel):
    data: list[EnteBusca]
    total: int
    escopo_total: int  # entes no escopo do usuário (denominador honesto da busca)


class PeriodoDisponivel(BaseModel):
    periodo: str
    relatorio: str
    versao_entrega: str | None = None
    vigente: bool = True


class PeriodosResponse(BaseModel):
    """Períodos **com dado** do ente, por relatório. O default do seletor é o mais recente."""

    cod_ibge: str
    relatorio: str | None = None
    default: str | None = None
    periodos: list[PeriodoDisponivel]


class EnteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cod_ibge: str
    nome: str | None = None
    esfera: str | None = None
    populacao: int | None = None
    rpps: bool = False
    possui_tcm: bool = False
    uf: str | None = None
    regiao: str | None = None
    pib: Decimal | None = None
    pop_ano_ref: int | None = None
    pib_ano_ref: int | None = None
    pop_source_ref: SourceRef | None = None
    pib_source_ref: SourceRef | None = None


class LimiteLegalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    indicador: str
    esfera: str
    poder: str
    sentido: str
    teto_pct: Decimal
    alerta_pct: Decimal | None = None
    prudencial_pct: Decimal | None = None
