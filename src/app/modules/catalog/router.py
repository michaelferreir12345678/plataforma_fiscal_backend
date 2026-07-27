"""Endpoints do catálogo: cadastro do ente e hierarquia temporal."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.catalog import service
from app.modules.catalog.schemas import EnteOut, EntesBuscaResponse, PeriodosResponse
from app.shared.envelope import DrillEnvelope
from app.shared.scope import assert_ente_in_scope, carteira_scope_ibges

router = APIRouter(tags=["catalog"])


@router.get("/entes", response_model=EntesBuscaResponse)
def buscar_entes(
    q: str | None = Query(None, description="Termo: nome ou início do código IBGE."),
    uf: str | None = Query(None, description="Prefixo IBGE da UF (2 dígitos, ex.: 23)."),
    limit: int = Query(20, ge=1, le=200),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> EntesBuscaResponse:
    """Busca de entes **restrita ao escopo** do usuário (seletor de ente e ⌘K).

    Não existe vazamento por busca: o universo é o escopo (§6.4), então um ente fora da
    carteira/licença nunca aparece — não precisa de 403 porque não é encontrável.
    """
    return service.buscar_entes(
        session, cods_escopo=carteira_scope_ibges(session, principal), q=q, uf=uf, limit=limit
    )


@router.get("/entes/{cod_ibge}/periodos", response_model=PeriodosResponse)
def periodos_do_ente(
    cod_ibge: str,
    relatorio: str | None = Query(None, description="RREO | RGF | DCA | MSC; vazio ⇒ todos."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> PeriodosResponse:
    """Períodos **com dado** do ente; ``default`` = o mais recente (seletor de período)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.periodos_do_ente(session, cod_ibge, relatorio=relatorio)


@router.get("/entes/{cod_ibge}", response_model=EnteOut)
def get_ente(
    cod_ibge: str,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> EnteOut:
    """Cadastro conformado do ente (nome, esfera, população/PIB). Valida escopo (§6.4)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.get_ente(session, cod_ibge)


@router.get("/periodos", response_model=DrillEnvelope)
def periodos(
    node: str | None = Query(None, description="Código do período; vazio ⇒ raízes (anos)."),
    _: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> DrillEnvelope:
    """Hierarquia temporal (ano → bimestre/quadrimestre → mês) no envelope de drill (§6.1)."""
    return service.periodos_drill(session, node)
