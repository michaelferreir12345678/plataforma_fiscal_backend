"""Endpoints de Previsões & Cenários (Módulo 13)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.forecast import service
from app.modules.forecast.schemas import (
    CenarioSalvo,
    CenarioSimularRequest,
    CenarioSimularResponse,
    ProjecaoResponse,
)
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["forecast"])

_INDICADOR_Q = Query("rcl", description="Indicador: rcl | receita | pessoal | divida.")
_AS_OF_Q = Query(None, description="Consulta bitemporal 'as of'.")


@router.get("/entes/{cod_ibge}/projecao", response_model=ProjecaoResponse)
def projecao(
    cod_ibge: str,
    indicador: str = _INDICADOR_Q,
    horizonte: int = Query(4, ge=1, le=24, description="Períodos à frente."),
    modelo: str | None = Query(None, description="fechamento | holt_winters | regressao_exogenas."),
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> ProjecaoResponse:
    """Histórico + projeção + IC + marcador de cruzamento de limite (materializa a gold)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_projecao(
        session, cod_ibge, indicador, horizonte=horizonte, modelo=modelo, as_of=as_of
    )


@router.post("/entes/{cod_ibge}/cenario/simular", response_model=CenarioSimularResponse)
def simular_cenario(
    cod_ibge: str,
    req: CenarioSimularRequest,
    indicador: str = _INDICADOR_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CenarioSimularResponse:
    """Deltas de gestor → recalcula projeção + impacto em limites/mínimos. Só grava se salvar."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.simular_cenario(session, principal, cod_ibge, indicador, req)


@router.get("/entes/{cod_ibge}/cenarios", response_model=list[CenarioSalvo])
def listar_cenarios(
    cod_ibge: str,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> list[CenarioSalvo]:
    """Cenários salvos da organização para o ente (RLS por org_id)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.listar_cenarios(session, principal, cod_ibge)


@router.get("/cenarios/{cenario_id}", response_model=CenarioSalvo)
def obter_cenario(
    cenario_id: uuid.UUID,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CenarioSalvo:
    return service.obter_cenario(session, principal, cenario_id)
