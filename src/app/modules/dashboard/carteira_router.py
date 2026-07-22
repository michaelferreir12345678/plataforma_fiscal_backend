"""Endpoints da visão agregada de carteira / visão estadual (Módulo 2, Sprint 4).

Escopo (§6.4): ``resumo``/``entes``/``mapa`` operam sobre a carteira do usuário,
ampliada aos municípios da UF para contas estaduais (regra única em
``shared/scope.py``). O drill DOWN de cada linha é ``GET /entes/{ibge}/dashboard``;
o drill UP do ente para o consolidado é ``GET /carteira/resumo``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_current_principal, get_db, require_capability
from app.core.errors import AppError
from app.modules.dashboard import carteira_service
from app.modules.dashboard.carteira_schemas import (
    CarteiraEnteRow,
    CarteiraLoteRequest,
    CarteiraMapaResponse,
    CarteiraResumoResponse,
    LoteJobOut,
)
from app.shared.envelope import ListEnvelope
from app.shared.pagination import PageParams, page_params

router = APIRouter(prefix="/carteira", tags=["carteira"])

# Capacidade RBAC exigida por ação em lote.
_LOTE_CAPACIDADE = {"relatorio": "gerar_relatorio", "alerta": "config_alerta"}


@router.get("/resumo", response_model=CarteiraResumoResponse)
def resumo(
    periodo: str = Query(..., description="Período RREO (ex.: 2024-B6)."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CarteiraResumoResponse:
    """Consolidação por faixa/conformidade no escopo (drill UP do ente para o todo)."""
    return carteira_service.build_resumo(session, principal, periodo)


@router.get("/entes", response_model=ListEnvelope[CarteiraEnteRow])
def entes(
    periodo: str = Query(..., description="Período RREO (ex.: 2024-B6)."),
    ordenar: str = Query("risco", description="risco | nome | codigo."),
    porte: str | None = Query(None, description="pequeno | medio | grande | metropole."),
    regiao: str | None = Query(None, description="Filtra por região."),
    tag: str | None = Query(None, description="Filtra por tag da carteira."),
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> ListEnvelope[CarteiraEnteRow]:
    """Grade/ranking paginado; ordenável por risco, filtrável por porte/região/tag."""
    return carteira_service.list_entes(
        session, principal, periodo,
        params=params, ordenar=ordenar, porte=porte, regiao=regiao, tag=tag,
    )


@router.get("/mapa", response_model=CarteiraMapaResponse)
def mapa(
    periodo: str = Query(..., description="Período RREO (ex.: 2024-B6)."),
    indicador: str | None = Query(None, description="Indicador; ausente ⇒ pior conformidade."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CarteiraMapaResponse:
    """Cor por ente para o coroplético (escopo respeitado)."""
    return carteira_service.build_mapa(session, principal, periodo, indicador=indicador)


@router.post("/refresh", response_model=dict)
def refresh(
    periodo: str = Query(..., description="Período RREO a materializar."),
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> dict:
    """Materializa ``mart_carteira`` (snapshot vigente) para todo o escopo do usuário."""
    total = carteira_service.refresh_scope(session, principal, periodo)
    return {"periodo": periodo, "linhas_materializadas": total}


@router.post("/lote/{acao}", response_model=LoteJobOut, status_code=202)
def lote(
    acao: str,
    body: CarteiraLoteRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
) -> LoteJobOut:
    """Enfileira uma ação em lote (relatório/alerta) sobre entes do escopo."""
    capacidade = _LOTE_CAPACIDADE.get(acao)
    if capacidade is None:
        raise AppError(
            status=404, title="Ação inválida",
            detail=f"Ação '{acao}' desconhecida (use relatorio ou alerta).",
        )
    if not principal.has_cap(capacidade):
        raise AppError(
            status=403, title="Sem permissão",
            detail=f"Requer a capacidade '{capacidade}'.",
            type_="urn:plataforma-fiscal:error:missing-capability",
        )
    return carteira_service.enfileirar_lote(session, principal, acao, body)
