"""Endpoints de Caixa & Restos a Pagar (Módulo 9) — suficiência por fonte, RPNP, art. 42."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.cash_rap import service
from app.modules.cash_rap.schemas import (
    Art42Out,
    CaixaDetalhe,
    CaixaMemoria,
    RpnpSemLastroOut,
    SuficienciaMatriz,
)
from app.shared.envelope import DrillEnvelope
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["cash_rap"])

_PERIODO_Q = Query(..., description="Período RGF quadrimestral (ex.: 2024-Q3).")
_AS_OF_Q = Query(None, description="Consulta bitemporal 'as of' (§6.5).")
_NODE_Q = Query(None, description="Código da fonte (drill); vazio = raízes da hierarquia.")


@router.get("/entes/{cod_ibge}/caixa", response_model=CaixaDetalhe)
def detalhe_caixa(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CaixaDetalhe:
    """Suficiência (por fonte) + restos a pagar (por poder) + fontes críticas + série."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_detalhe(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/caixa/arvore", response_model=DrillEnvelope)
def arvore_caixa(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    node: str | None = _NODE_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> DrillEnvelope:
    """Drill DOWN/UP por fonte de recurso (Total → grupo → fonte) — §6.1."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_arvore(session, cod_ibge, periodo, node, as_of=as_of)


@router.get("/entes/{cod_ibge}/caixa/suficiencia", response_model=SuficienciaMatriz)
def suficiencia_caixa(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> SuficienciaMatriz:
    """Matriz de suficiência financeira **por fonte** com semáforo — nunca consolidada."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_suficiencia(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/caixa/rpnp-sem-lastro", response_model=RpnpSemLastroOut)
def rpnp_sem_lastro_caixa(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> RpnpSemLastroOut:
    """RPNP inscrito sem disponibilidade de caixa, por fonte (base do expurgo dos mínimos)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_rpnp_sem_lastro(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/caixa/art42", response_model=Art42Out)
def art42_caixa(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> Art42Out:
    """Painel do art. 42 LRF — só é aplicável em ano de fim de mandato (``aplicavel``)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_art42(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/caixa/memoria", response_model=CaixaMemoria)
def memoria_caixa(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CaixaMemoria:
    """Memória de cálculo: fórmulas, identidades e origem de cada número (auditável)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_memoria(session, cod_ibge, periodo, as_of=as_of)
