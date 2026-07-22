"""Endpoints de indicadores: RCL com memória de cálculo e drill-down."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.indicators import service
from app.modules.indicators.schemas import RclResponse
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["indicators"])


@router.get("/entes/{cod_ibge}/rcl", response_model=RclResponse)
def get_rcl(
    cod_ibge: str,
    periodo: str = Query(..., description="Período RREO canônico (ex.: 2024-B6)."),
    as_of: datetime | None = Query(None, description="Reproduz a versão vigente naquele instante."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> RclResponse:
    """RCL (12 meses móveis) com memória de cálculo e drill DOWN para deduções (§6.5)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_rcl_response(session, cod_ibge, periodo, as_of=as_of)
