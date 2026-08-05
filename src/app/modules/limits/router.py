"""Endpoints do Monitor de Limites (Módulo 3)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.limits import service
from app.modules.limits.schemas import (
    LimiteDetail,
    LimitesResponse,
    SimularRequest,
    SimularResponse,
)
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["limits"])

_AS_OF_Q = Query(None, description="Consulta bitemporal 'as of' (§6.5).")


@router.get("/entes/{cod_ibge}/limites", response_model=LimitesResponse)
def listar_limites(
    cod_ibge: str,
    periodo: str = Query(..., description="Período RREO (ex.: 2024-B6)."),
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> LimitesResponse:
    """Todos os limites do ente/período com faixa e distância ao teto/alerta."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_limites(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/limites/{indicador}", response_model=LimiteDetail)
def detalhar_limite(
    cod_ibge: str,
    indicador: str,
    periodo: str = Query(..., description="Período RREO (ex.: 2024-B6)."),
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> LimiteDetail:
    """Drill DOWN: memória de cálculo + providências (da faixa) + série histórica (drill UP)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_limite_detail(session, cod_ibge, periodo, indicador, as_of=as_of)


@router.post("/entes/{cod_ibge}/limites/{indicador}/simular", response_model=SimularResponse)
def simular_limite(
    cod_ibge: str,
    indicador: str,
    req: SimularRequest,
    periodo: str = Query(..., description="Período RREO (ex.: 2024-B6)."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> SimularResponse:
    """Recalcula a faixa para um delta/novo valor — **sem persistir**."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.simular(session, cod_ibge, periodo, indicador, req)
