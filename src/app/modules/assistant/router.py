"""Endpoints do Assistente de IA (Módulo 15, Sprint 17)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.assistant import service
from app.modules.assistant.llm import LLMProvider, get_llm_provider
from app.modules.assistant.schemas import (
    ConversasOut,
    PerguntaRequest,
    RespostaOut,
    ResumoExecutivoRequest,
    UsoResumoOut,
)

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/perguntar", response_model=RespostaOut)
def perguntar(
    body: PerguntaRequest,
    principal: Principal = Depends(require_capability("usar_ia")),
    provider: LLMProvider = Depends(get_llm_provider),
    session: Session = Depends(get_db),
) -> RespostaOut:
    """Pergunta livre fundamentada nos indicadores do ente + corpo normativo (RAG)."""
    return service.perguntar(session, principal, provider, body)


@router.post("/resumo-executivo", response_model=RespostaOut)
def resumo_executivo(
    body: ResumoExecutivoRequest,
    principal: Principal = Depends(require_capability("usar_ia")),
    provider: LLMProvider = Depends(get_llm_provider),
    session: Session = Depends(get_db),
) -> RespostaOut:
    """Resumo executivo narrado (handoff Sprint 16), fundamentado e rastreável."""
    return service.resumo_executivo(session, principal, provider, body)


@router.get("/conversas", response_model=ConversasOut)
def conversas(
    limit: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(require_capability("usar_ia")),
    session: Session = Depends(get_db),
) -> ConversasOut:
    """Histórico recente de conversas da organização (RLS por org_id)."""
    return service.historico(session, principal, limit=limit)


@router.get("/uso", response_model=UsoResumoOut)
def uso(
    principal: Principal = Depends(require_capability("usar_ia")),
    session: Session = Depends(get_db),
) -> UsoResumoOut:
    """Consumo de IA da organização no mês (cota 'Consultas IA/mês')."""
    return service.uso_mensal(session, principal)
