"""Contratos HTTP do módulo de relatórios."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.shared.source_ref import SourceRef


class ModeloRelatorioOut(BaseModel):
    codigo: str
    nome: str
    publico: str
    descricao: str
    secoes: list[str]
    formatos: list[str]
    formalidade: str
    modelo_versao: str = "v1"


class ModelosResponse(BaseModel):
    modelos: list[ModeloRelatorioOut]
    gerado_em: datetime


class RelatorioCreate(BaseModel):
    modelo: str
    formato: str = "pdf"
    escopo: str = "ente"
    ente: str | None = None
    entes: list[str] | None = None
    periodo: str
    secoes: list[str] | None = None
    as_of: datetime | None = None
    parametros: dict[str, Any] = Field(default_factory=dict)

    @field_validator("modelo", "formato", "escopo", mode="before")
    @classmethod
    def _lower(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("entes")
    @classmethod
    def _unique_entes(cls, value: list[str] | None) -> list[str] | None:
        return list(dict.fromkeys(value)) if value else value


class DadoIncompletoOut(BaseModel):
    tipo: str
    codigo: str
    mensagem: str
    periodo_esperado: str | None = None
    periodo_encontrado: str | None = None


class RelatorioOut(BaseModel):
    id: uuid.UUID
    lote_id: uuid.UUID
    modelo: str
    modelo_versao: str
    formato: str
    escopo: str
    cod_ibge: str
    periodo: str
    as_of: datetime
    status: str
    progresso: int
    cabecalho: dict[str, Any]
    source_refs: list[SourceRef]
    memoria: dict[str, Any]
    dados_incompletos: list[DadoIncompletoOut]
    arquivo_nome: str | None = None
    arquivo_url: str | None = None
    mime_type: str | None = None
    tamanho_bytes: int | None = None
    conteudo_hash: str | None = None
    gerado_em: datetime | None = None
    erro: str | None = None
    criado_em: datetime
    atualizado_em: datetime


class RelatorioSolicitacaoOut(BaseModel):
    lote_id: uuid.UUID
    total_entes: int
    status: str
    relatorios: list[RelatorioOut]


class RelatorioDetalheOut(RelatorioOut):
    lote_itens: list[RelatorioOut] = Field(default_factory=list)


class RelatorioListaOut(BaseModel):
    itens: list[RelatorioOut]
    total: int
    gerado_em: datetime


class AgendamentoCreate(BaseModel):
    modelo: str
    formato: str = "pdf"
    escopo: str = "ente"
    ente: str | None = None
    entes: list[str] | None = None
    periodo: str
    periodicidade: str = "mensal"
    proxima_execucao: datetime
    secoes: list[str] | None = None
    parametros: dict[str, Any] = Field(default_factory=dict)

    @field_validator("modelo", "formato", "escopo", "periodicidade", mode="before")
    @classmethod
    def _lower(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("entes")
    @classmethod
    def _unique_entes(cls, value: list[str] | None) -> list[str] | None:
        return list(dict.fromkeys(value)) if value else value


class AgendamentoPatch(BaseModel):
    """Edição de um agendamento (Sprint 25E). Campos ausentes não são alterados."""

    ativo: bool | None = None
    periodicidade: str | None = None
    periodo: str | None = None
    formato: str | None = None
    proxima_execucao: datetime | None = None

    @field_validator("periodicidade", "formato", mode="before")
    @classmethod
    def _lower(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class AgendamentoOut(BaseModel):
    id: uuid.UUID
    modelo: str
    formato: str
    escopo: str
    entes: list[str]
    periodo: str
    periodicidade: str
    parametros: dict[str, Any]
    proxima_execucao: datetime
    ultima_execucao: datetime | None = None
    ativo: bool
    criado_em: datetime
    atualizado_em: datetime
