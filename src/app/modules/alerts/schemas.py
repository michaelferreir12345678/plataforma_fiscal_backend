"""Schemas da Sprint 15 (Alertas & Conformidade).

Cada alerta carrega **motivo legal + ação + prazo + link** (critério de aceite).
Números fiscais que originaram o alerta trazem ``source_ref`` e a memória de avaliação.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.shared.source_ref import SourceRef


class AlertaOut(BaseModel):
    id: str
    cod_ibge: str
    categoria: str
    severidade: str
    prioridade: int
    titulo: str
    motivo_legal: str
    acao_sugerida: str
    prazo: date | None = None
    link: str | None = None
    status: str
    indicador: str | None = None
    periodo: str | None = None
    source_ref: SourceRef | None = None
    memoria: dict | None = None
    criado_em: datetime
    atualizado_em: datetime


class Contadores(BaseModel):
    critico: int = 0
    atencao: int = 0
    informativo: int = 0
    total: int = 0


class FilaAlertasResponse(BaseModel):
    """Fila priorizada (crítico → atenção → informativo) no escopo pedido."""

    escopo: str  # "ente" | "carteira"
    cod_ibge: str | None = None
    gerado_em: datetime
    contadores: Contadores
    alertas: list[AlertaOut]


class AlertaPatch(BaseModel):
    status: str = Field(description="nova | reconhecida | resolvida | descartada.")


class CalendarioItem(BaseModel):
    relatorio: str
    periodo: str
    periodicidade: str
    prazo: date | None = None
    status: str  # entregue | pendente | atrasado
    entregue_em: datetime | None = None
    versao_entrega: str | None = None
    base_legal: str | None = None
    source_ref: SourceRef | None = None


class CalendarioResponse(BaseModel):
    cod_ibge: str
    esfera: str | None = None
    populacao: int | None = None
    periodicidade_rgf: str
    gerado_em: datetime
    itens: list[CalendarioItem]


class CarteiraEnteAlertas(BaseModel):
    cod_ibge: str
    nome: str | None = None
    contadores: Contadores
    pior_severidade: str | None = None


class CarteiraCategoriaAgg(BaseModel):
    categoria: str
    total: int


class CarteiraAlertasResponse(BaseModel):
    """Agregados no nível carteira (drill UP do ente para o todo)."""

    n_entes: int
    gerado_em: datetime
    contadores: Contadores
    por_categoria: list[CarteiraCategoriaAgg]
    por_ente: list[CarteiraEnteAlertas]
    top_alertas: list[AlertaOut]
