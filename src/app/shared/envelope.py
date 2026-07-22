"""Envelopes de resposta (§6.1 e §6.2).

- ``ListEnvelope`` — respostas de lista paginadas: ``{data, page, page_size, total, source_ref}``.
- ``DrillEnvelope`` — envelope universal de drill-down hierárquico
  (``node`` / ``breadcrumb`` / ``children`` / ``measures`` / ``period`` / ``source_ref``).
- ``ProblemDetail`` — modelo do erro RFC 7807 (documentação/OpenAPI).
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from app.shared.source_ref import SourceRef

T = TypeVar("T")

Measures = dict[str, Any]


class ListEnvelope(BaseModel, Generic[T]):
    """Envelope padrão de listas paginadas (§6.2)."""

    data: list[T]
    page: int
    page_size: int
    total: int
    source_ref: SourceRef | None = None


class DrillNodeRef(BaseModel):
    """Nó da hierarquia (usado no ``node`` e em cada item do ``breadcrumb``)."""

    codigo: str
    descricao: str
    nivel: int | None = None


class DrillChild(BaseModel):
    """Filho no drill DOWN — inclui medidas agregadas e ``has_children`` (evita round-trips)."""

    codigo: str
    descricao: str
    nivel: int | None = None
    measures: Measures = Field(default_factory=dict)
    has_children: bool = False


class DrillEnvelope(BaseModel):
    """Envelope universal de drill (§6.1) — idêntico para toda hierarquia."""

    node: DrillNodeRef | None = None
    breadcrumb: list[DrillNodeRef] = Field(default_factory=list)  # drill UP (ancestrais)
    children: list[DrillChild] = Field(default_factory=list)  # drill DOWN
    measures: Measures = Field(default_factory=dict)  # agregado do próprio node
    period: str | None = None
    source_ref: SourceRef | None = None


class ProblemDetail(BaseModel):
    """Erro no padrão RFC 7807 (para documentação OpenAPI)."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
