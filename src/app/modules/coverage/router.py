"""Endpoint da cobertura por página."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.coverage import service
from app.modules.coverage.schemas import CoberturaPagina
from app.shared.scope import carteira_scope_ibges

router = APIRouter(tags=["cobertura"])

_PAGINA_Q = Query(description="Chave da página (ex.: saude-educacao, divida, receita).")
_PERIODO_Q = Query(None, description="Período em contexto, quando houver.")


@router.get("/cobertura/pagina/{pagina}", response_model=CoberturaPagina)
def cobertura_da_pagina(
    pagina: str,
    ente: str = Query(description="Código IBGE do ente em contexto."),
    periodo: str | None = _PERIODO_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> CoberturaPagina:
    """Para quantos entes e períodos esta página de fato responde.

    A medida é sempre **dentro do escopo de quem pergunta**: "1 de 5.570" não diz nada a
    uma Sefaz que monitora 184 municípios. O denominador é a carteira dela.

    Não valida o ente contra o escopo de propósito — a resposta é sobre a **cobertura**, e
    negá-la a quem consulta um ente fora da carteira apenas trocaria uma informação
    inofensiva por um 403 que não protege nada: os números aqui são agregados do próprio
    escopo do solicitante.
    """
    escopo = carteira_scope_ibges(session, principal)
    return service.build_cobertura_pagina(
        session, pagina=pagina, cod_ibge=ente, periodo=periodo, entes_do_escopo=escopo
    )
