"""Endpoint do Dashboard Executivo (Módulo 1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.dashboard import service
from app.modules.dashboard.schemas import DashboardResponse
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["dashboard"])


@router.get("/entes/{cod_ibge}/dashboard", response_model=DashboardResponse)
def dashboard(
    cod_ibge: str,
    periodo: str = Query(..., description="Período RREO (ex.: 2024-B6)."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> DashboardResponse:
    """Semáforo (pessoal/dívida/saúde/educação), KPIs, conformidade e destaques."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_dashboard(session, cod_ibge, periodo)
