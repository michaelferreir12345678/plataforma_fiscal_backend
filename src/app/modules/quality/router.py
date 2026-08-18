"""Endpoints de qualidade e linhagem (Sprint 26)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.quality import service
from app.modules.quality.schemas import (
    AcaoRequest,
    LineageResponse,
    OcorrenciaQualidade,
    OcorrenciasResponse,
    QualidadeResponse,
)

router = APIRouter(prefix="/admin", tags=["quality"])


@router.get("/qualidade", response_model=QualidadeResponse)
def qualidade(
    fonte: str | None = Query(None, description="Filtra por fonte (ex.: siconfi_rreo)."),
    status: str | None = Query(None, description="ok | aviso | falha."),
    ente: str | None = Query(None, description="Filtra por ente do escopo."),
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> QualidadeResponse:
    """Estado corrente dos checks no escopo do usuário — falhas primeiro."""
    return service.painel(
        session, principal, fonte=fonte, status=status, cod_ibge=ente,
        pagina=pagina, por_pagina=por_pagina,
    )


@router.get("/lineage", response_model=LineageResponse)
def lineage(
    no: str | None = Query(
        None,
        description=(
            "Nó do grafo (ex.: 'silver.siconfi_rgf' ou '/pessoal'); vazio devolve o mapa."
        ),
    ),
    _: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> LineageResponse:
    """Caminho do dado nos dois sentidos: de onde vem, e o que quebra se falhar."""
    return service.lineage(session, no=no)


# --------------------------------------------------------------------------- #
# Sprint Q1 — resolução
# --------------------------------------------------------------------------- #
@router.get("/qualidade/ocorrencias", response_model=OcorrenciasResponse)
def ocorrencias(
    ente: str | None = Query(None, description="Filtra por ente do escopo."),
    incluir_encerradas: bool = Query(
        False, description="Inclui as já resolvidas ou aceitas como fato."
    ),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> OcorrenciasResponse:
    """As falhas com **classe da causa, evidência e as ações que cabem a cada uma**.

    É a lista que responde "e agora, o que eu faço?". Sem ela, o selo de qualidade diz que
    o número não está conferido e para aí — e um aviso que ninguém consegue encerrar é um
    aviso que todos aprendem a ignorar.
    """
    return service.painel_ocorrencias(
        session, principal, cod_ibge=ente, incluir_encerradas=incluir_encerradas
    )


@router.post("/qualidade/ocorrencias/acao", response_model=OcorrenciaQualidade)
def aplicar_acao_ocorrencia(
    body: AcaoRequest,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> OcorrenciaQualidade:
    """Aplica a ação, **reexecuta a verificação** e devolve a ocorrência atualizada.

    A capacidade é conferida por ação, dentro do serviço, e não aqui: `rematerializar`
    altera o schema `gold`, que é compartilhado por todas as organizações — exige
    `administrar` —, enquanto `aceitar_como_fato` escreve só na tratativa privada e exige
    `editar`. Pedir a mais na borda bloquearia quem pode aceitar; pedir a menos deixaria
    passar quem não pode reprocessar.
    """
    return service.aplicar_acao_ocorrencia(session, principal, body)
