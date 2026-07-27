"""Endpoint do Dashboard Executivo (Módulo 1)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.dashboard import cockpit_service, service
from app.modules.dashboard.cockpit_schemas import CockpitResponse
from app.modules.dashboard.schemas import DashboardResponse
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["dashboard"])


@router.get("/entes/{cod_ibge}/cockpit", response_model=CockpitResponse)
def cockpit(
    cod_ibge: str,
    periodo: str = Query(..., description="Período RREO (ex.: 2024-B6)."),
    as_of: datetime | None = Query(
        None, description="Reproduz o cockpit como estava naquele instante (§6.5)."
    ),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CockpitResponse:
    """Cockpit executivo em 7 camadas: resumo, críticos, tendências, explicadores,
    comparações, riscos e qualidade do dado. Compõe marts existentes (sem mart próprio)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return cockpit_service.build_cockpit(
        session, principal, cod_ibge, periodo, as_of=as_of
    )


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
