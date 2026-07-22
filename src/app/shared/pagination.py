"""Paginação padrão (§6.2)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Query

from app.shared.envelope import ListEnvelope
from app.shared.source_ref import SourceRef


@dataclass(frozen=True)
class PageParams:
    """Parâmetros de paginação (1-based)."""

    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def page_params(
    page: int = Query(1, ge=1, description="Página (1-based)."),
    page_size: int = Query(50, ge=1, le=200, description="Itens por página (máx. 200)."),
) -> PageParams:
    """Dependência FastAPI que coleta os parâmetros de paginação."""
    return PageParams(page=page, page_size=page_size)


def paginate(
    items: list,
    total: int,
    params: PageParams,
    *,
    source_ref: SourceRef | None = None,
) -> ListEnvelope:
    """Monta o :class:`ListEnvelope` a partir de itens já paginados e do total."""
    return ListEnvelope(
        data=items,
        page=params.page,
        page_size=params.page_size,
        total=total,
        source_ref=source_ref,
    )
