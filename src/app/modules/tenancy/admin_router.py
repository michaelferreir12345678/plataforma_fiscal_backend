"""Endpoints de administração avançada, carteira em lote e billing (Sprint 18).

Todos exigem a capacidade ``administrar``. Billing/auditoria/carteira são do plano de
dados (isolados por RLS na organização do principal).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.core.errors import AppError
from app.modules.tenancy import service
from app.modules.tenancy.schemas import (
    AssinaturaInput,
    AssinaturaOut,
    AuditoriaPage,
    BillingOverview,
    CarteiraLoteInput,
    CarteiraLoteResult,
    FaturaEmitInput,
    FaturaOut,
)

router = APIRouter(tags=["admin"])


# --- Carteira em lote ---
@router.post("/carteira/lote", response_model=CarteiraLoteResult)
def carteira_lote(
    body: CarteiraLoteInput,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> CarteiraLoteResult:
    """Adiciona/remove entes da carteira em lote (idempotente)."""
    return service.carteira_lote(session, principal, body)


# --- Billing ---
@router.get("/billing", response_model=BillingOverview)
def billing(
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> BillingOverview:
    """Assinatura, prévia da fatura corrente (computada ao vivo) e histórico de faturas."""
    return service.billing_overview(session, principal)


@router.post("/billing/assinatura", response_model=AssinaturaOut, status_code=201)
def set_assinatura(
    _: AssinaturaInput,
    __: Principal = Depends(require_capability("administrar")),
) -> AssinaturaOut:
    """Impede que um tenant altere a própria licença/preço/status."""
    raise AppError(
        status=403,
        title="Operação de licenciamento restrita",
        detail="Assinaturas só podem ser alteradas pelo control plane da plataforma.",
        type_="urn:plataforma-fiscal:error:platform-admin-required",
    )


@router.post("/billing/faturas", response_model=FaturaOut, status_code=201)
def emitir_fatura(
    body: FaturaEmitInput,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> FaturaOut:
    """Emite a fatura da competência refletindo a ``metrica_cobranca`` (com empenho/contrato)."""
    return service.emitir_fatura(session, principal, body)


# --- Auditoria filtrável ---
@router.get("/admin/auditoria", response_model=AuditoriaPage)
def auditoria(
    acao: str | None = Query(None, description="Filtra por ação (substring)."),
    recurso: str | None = Query(None, description="Filtra por recurso (substring)."),
    usuario_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, description="Busca livre em ação/recurso."),
    de: datetime | None = Query(None, description="Início do intervalo (ts >=)."),
    ate: datetime | None = Query(None, description="Fim do intervalo (ts <)."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> AuditoriaPage:
    """Consulta filtrável da trilha de auditoria da organização (RLS por org)."""
    return service.auditoria(
        session,
        principal,
        acao=acao,
        recurso=recurso,
        usuario_id=usuario_id,
        texto=q,
        de=de,
        ate=ate,
        limit=limit,
        offset=offset,
    )
