"""Endpoints da IA nas telas (Sprint IA-5).

Quatro rotas, uma por capacidade da §4 do plano de MCP. Todas exigem ``usar_ia`` — a
mesma capacidade do Assistente, porque é o mesmo consumo pago e o mesmo risco: quem não
pode conversar com a IA também não pede a ela que explique um número.

O escopo do ente **não** é verificado aqui: ele é verificado dentro da ferramenta, pelo
envelope (A22/E1). Repetir a checagem na borda daria a impressão de que é a borda que
protege — e no dia em que uma rota nova esquecesse, a proteção sumiria junto.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.assistant.llm import LLMProvider, get_llm_provider
from app.modules.insights import service
from app.modules.insights.schemas import (
    CentralDadosRequest,
    ExplicarAlertasRequest,
    ExplicarNumeroRequest,
    InsightOut,
    NarrarRelatorioRequest,
)

router = APIRouter(prefix="/ia", tags=["ia-telas"])


@router.post("/explicar-numero", response_model=InsightOut)
def explicar_numero(
    body: ExplicarNumeroRequest,
    principal: Principal = Depends(require_capability("usar_ia")),
    provider: LLMProvider = Depends(get_llm_provider),
    session: Session = Depends(get_db),
) -> InsightOut:
    """Explica um indicador da tela: linhagem, memória, base legal e o que mudaria a faixa."""
    return service.explicar_numero(session, principal, provider, body)


@router.post("/explicar-alertas", response_model=InsightOut)
def explicar_alertas(
    body: ExplicarAlertasRequest,
    principal: Principal = Depends(require_capability("usar_ia")),
    provider: LLMProvider = Depends(get_llm_provider),
    session: Session = Depends(get_db),
) -> InsightOut:
    """Explica a fila de alertas **já ordenada** pela regra — a IA não reordena nada."""
    return service.explicar_alertas(session, principal, provider, body)


@router.post("/narrar-relatorio", response_model=InsightOut)
def narrar_relatorio(
    body: NarrarRelatorioRequest,
    principal: Principal = Depends(require_capability("usar_ia")),
    provider: LLMProvider = Depends(get_llm_provider),
    session: Session = Depends(get_db),
) -> InsightOut:
    """Narrativa executiva do documento do relatório, com os mesmos números e fontes."""
    return service.narrar_relatorio(session, principal, provider, body)


@router.post("/central-dados", response_model=InsightOut)
def central_dados(
    body: CentralDadosRequest,
    principal: Principal = Depends(require_capability("usar_ia")),
    provider: LLMProvider = Depends(get_llm_provider),
    session: Session = Depends(get_db),
) -> InsightOut:
    """Busca em linguagem natural: cobertura + qualidade + calendário explicam a ausência."""
    return service.buscar_central_dados(session, principal, provider, body)
