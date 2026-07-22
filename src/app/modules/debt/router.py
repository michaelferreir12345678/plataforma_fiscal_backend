"""Endpoints de Dívida & Endividamento (Módulo 7)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.debt import service
from app.modules.debt.schemas import (
    CapagResponse,
    CronogramaResponse,
    DividaArvoreOut,
    DividaDetalhe,
    MemoriaDivida,
    SimulacaoResponse,
    SimularOperacaoRequest,
)
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["debt"])

_PERIODO_Q = Query(..., description="Período RGF (ex.: 2024-Q3).")
_AS_OF_Q = Query(None, description="Consulta bitemporal 'as of'.")


@router.get("/entes/{cod_ibge}/divida", response_model=DividaDetalhe)
def detalhe_divida(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> DividaDetalhe:
    """Dois heróis separados: DCL líquida (RGF) e CAPAG/endividamento bruto."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_detalhe(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/divida/memoria", response_model=MemoriaDivida)
def memoria_divida(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> MemoriaDivida:
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_memoria(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/divida/arvore", response_model=DividaArvoreOut)
def arvore_divida(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    eixo: Literal["origem", "credor"] = Query("origem"),
    node: str | None = Query(None, description="Nó atual; vazio retorna as raízes."),
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> DividaArvoreOut:
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_arvore(
        session,
        cod_ibge,
        periodo,
        eixo,
        node,
        as_of=as_of,
    )


@router.get("/entes/{cod_ibge}/divida/capag", response_model=CapagResponse)
def capag_divida(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CapagResponse:
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_capag(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/divida/cronograma", response_model=CronogramaResponse)
def cronograma_divida(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CronogramaResponse:
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_cronograma(session, cod_ibge, periodo, as_of=as_of)


@router.post(
    "/entes/{cod_ibge}/divida/simular-operacao",
    response_model=SimulacaoResponse,
)
def simular_operacao(
    cod_ibge: str,
    req: SimularOperacaoRequest,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> SimulacaoResponse:
    """Cenário não persistente contra DCL, crédito 16%, garantias 22% e ARO 7%."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.simular_operacao(
        session,
        cod_ibge,
        periodo,
        req,
        as_of=as_of,
    )
