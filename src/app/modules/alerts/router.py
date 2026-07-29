"""Endpoints de Alertas & Conformidade (Módulo 14)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.alerts import service
from app.modules.alerts.schemas import (
    AlertaOut,
    AlertaPatch,
    CalendarioResponse,
    CarteiraAlertasResponse,
    FilaAlertasResponse,
    HistoricoAlertasResponse,
)
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["alerts"])


@router.get("/alertas", response_model=FilaAlertasResponse)
def alertas(
    escopo: str = Query("ente", description="ente | carteira."),
    ente: str | None = Query(None, description="Código IBGE (obrigatório se escopo=ente)."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> FilaAlertasResponse:
    """Fila priorizada de alertas no escopo (motor avalia on-read; dedup idempotente)."""
    return service.listar_fila(session, principal, escopo=escopo, cod_ibge=ente)


@router.get("/alertas/historico", response_model=HistoricoAlertasResponse)
def historico_alertas(
    escopo: str = Query("ente", description="ente | carteira."),
    ente: str | None = Query(None, description="Código IBGE (obrigatório se escopo=ente)."),
    categoria: str | None = Query(None, description="Filtra por categoria do alerta."),
    limite: int = Query(100, ge=1, le=500),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> HistoricoAlertasResponse:
    """Alertas já tratados, com quem resolveu, quando e em quantos dias."""
    return service.historico(
        session, principal, escopo=escopo, cod_ibge=ente, categoria=categoria, limite=limite
    )


@router.get("/entes/{cod_ibge}/calendario", response_model=CalendarioResponse)
def calendario(
    cod_ibge: str,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CalendarioResponse:
    """Calendário de obrigações do ente, com periodicidade sensível ao porte."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.calendario(session, principal, cod_ibge)


@router.get("/carteira/alertas", response_model=CarteiraAlertasResponse)
def carteira_alertas(
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CarteiraAlertasResponse:
    """Agregados de alertas no nível carteira (drill UP do ente para o todo)."""
    return service.carteira_alertas(session, principal)


@router.patch("/alertas/{alerta_id}", response_model=AlertaOut)
def patch_alerta(
    alerta_id: uuid.UUID,
    body: AlertaPatch,
    principal: Principal = Depends(require_capability("config_alerta")),
    session: Session = Depends(get_db),
) -> AlertaOut:
    """Reconhecer/resolver/descartar um alerta (exige a capacidade config_alerta)."""
    return service.atualizar_status(session, principal, alerta_id, body.status)
