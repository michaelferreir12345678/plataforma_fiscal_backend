"""Endpoints do catálogo: cadastro do ente e hierarquia temporal."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.catalog import service
from app.modules.catalog.schemas import EnteOut
from app.shared.envelope import DrillEnvelope
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["catalog"])


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
