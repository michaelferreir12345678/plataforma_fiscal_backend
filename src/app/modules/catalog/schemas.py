"""Schemas do catálogo (dim_ente, dim_limite_legal)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.shared.source_ref import SourceRef


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
