"""Endpoint do painel de divergências."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.core.errors import AppError
from app.modules.reconciliation import service
from app.modules.reconciliation.schemas import ReconciliacaoResultado
from app.shared.scope import carteira_scope_ibges

router = APIRouter(prefix="/reconciliacao", tags=["reconciliação"])


@router.get("/{codigo}", response_model=ReconciliacaoResultado)
def reconciliacao(
    codigo: str,
    limite: int = Query(200, ge=1, le=1000),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> ReconciliacaoResultado:
    """Onde a plataforma discorda de quem publicou o número.

    Calculado na hora, e não guardado: o dado dos dois lados muda com retificação, e uma
    divergência congelada continuaria sendo exibida depois de resolvida.
    """
    if codigo not in service.COMPARACOES:
        raise AppError(
            status=404,
            title="Comparação desconhecida",
            detail=(
                f"Não há reconciliação '{codigo}'. Disponíveis: "
                f"{', '.join(sorted(service.COMPARACOES))}."
            ),
        )
    escopo = carteira_scope_ibges(session, principal)
    return service.build_reconciliacao(session, codigo=codigo, entes=escopo, limite=limite)
