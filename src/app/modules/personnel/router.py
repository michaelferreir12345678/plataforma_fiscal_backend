"""Endpoints da Despesa com Pessoal (Módulo 6) — Padrão de Detalhe + drill §6.1."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.personnel import service
from app.modules.personnel.schemas import (
    MemoriaPessoal,
    PessoalDetalhe,
    PorPoderOut,
)
from app.shared.envelope import DrillEnvelope
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["personnel"])

_PERIODO_Q = Query(..., description="Período RGF (quadrimestral, ex.: 2024-Q3).")
_AS_OF_Q = Query(None, description="Consulta bitemporal 'as of' (§6.5).")


@router.get("/entes/{cod_ibge}/pessoal", response_model=PessoalDetalhe)
def detalhe_pessoal(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> PessoalDetalhe:
    """Cabeçalho + composição (por poder) + série + comparação (Executivo é o indicador-base)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_detalhe(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/pessoal/arvore", response_model=DrillEnvelope)
def arvore_pessoal(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    node: str | None = Query(None, description="Código do nó; vazio ⇒ consolidado do ente."),
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> DrillEnvelope:
    """Drill DOWN/UP por poder/órgão (Ente→Poder→Órgão→Unidade)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_arvore(session, cod_ibge, periodo, node, as_of=as_of)


@router.get("/entes/{cod_ibge}/pessoal/memoria", response_model=MemoriaPessoal)
def memoria_pessoal(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> MemoriaPessoal:
    """Memória de cálculo: bruta, exclusões (com/sem RPPS), líquida e cross-check reportado."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_memoria(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/pessoal/por-poder", response_model=PorPoderOut)
def por_poder_pessoal(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> PorPoderOut:
    """Despesa com pessoal por poder, com faixa do Executivo (teto 54% município / 49% estado)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_por_poder(session, cod_ibge, periodo, as_of=as_of)
