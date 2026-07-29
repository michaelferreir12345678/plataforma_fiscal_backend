"""Endpoints de qualidade e linhagem (Sprint 26)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.quality import service
from app.modules.quality.schemas import LineageResponse, QualidadeResponse

router = APIRouter(prefix="/admin", tags=["quality"])


@router.get("/qualidade", response_model=QualidadeResponse)
def qualidade(
    fonte: str | None = Query(None, description="Filtra por fonte (ex.: siconfi_rreo)."),
    status: str | None = Query(None, description="ok | aviso | falha."),
    ente: str | None = Query(None, description="Filtra por ente do escopo."),
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> QualidadeResponse:
    """Estado corrente dos checks no escopo do usuário — falhas primeiro."""
    return service.painel(
        session, principal, fonte=fonte, status=status, cod_ibge=ente,
        pagina=pagina, por_pagina=por_pagina,
    )


@router.get("/lineage", response_model=LineageResponse)
def lineage(
    no: str | None = Query(
        None,
        description=(
            "Nó do grafo (ex.: 'silver.siconfi_rgf' ou '/pessoal'); vazio devolve o mapa."
        ),
    ),
    _: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> LineageResponse:
    """Caminho do dado nos dois sentidos: de onde vem, e o que quebra se falhar."""
    return service.lineage(session, no=no)
