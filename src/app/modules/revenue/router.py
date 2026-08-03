"""Endpoints da Receita (Módulo 4) — Padrão de Detalhe de Indicador + drill §6.1."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.revenue import service
from app.modules.revenue.schemas import (
    ConciliacaoOut,
    DependenciaOut,
    MemoriaReceita,
    RealizacaoOut,
    ReceitaDetalhe,
)
from app.shared.envelope import DrillEnvelope
from app.shared.linha_bruta import LinhaBrutaResponse
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["revenue"])

_PERIODO_Q = Query(..., description="Período RREO (ex.: 2024-B6).")
_AS_OF_Q = Query(None, description="Consulta bitemporal 'as of' (§6.5).")


@router.get("/entes/{cod_ibge}/receita", response_model=ReceitaDetalhe)
def detalhe_receita(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> ReceitaDetalhe:
    """Cabeçalho + composição (raiz) + série + comparação com o exercício anterior."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_detalhe(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/receita/linha/{origem_codigo}", response_model=LinhaBrutaResponse)
def linha_bruta_receita(
    cod_ibge: str,
    origem_codigo: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> LinhaBrutaResponse:
    """Fundo do drill: as linhas do RREO Anexo 01 que produziram este nó de receita.

    Último degrau navegável. Abaixo dele só existe o JSON bruto no bronze — e a página de
    procedência mostra o endereço exato de onde ele veio.
    """
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_linha_bruta(session, cod_ibge, periodo, origem_codigo, as_of=as_of)


@router.get("/entes/{cod_ibge}/receita/arvore", response_model=DrillEnvelope)
def arvore_receita(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    node: str | None = Query(None, description="Código da origem; vazio ⇒ categorias."),
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> DrillEnvelope:
    """Drill DOWN/UP da natureza da receita (Categoria→Origem→Espécie→Rubrica→Alínea)."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_arvore(session, cod_ibge, periodo, node, as_of=as_of)


@router.get("/entes/{cod_ibge}/receita/memoria", response_model=MemoriaReceita)
def memoria_receita(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> MemoriaReceita:
    """Memória de cálculo rastreável + verificação de agregação por nível."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_memoria(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/receita/dependencia", response_model=DependenciaOut)
def dependencia_receita(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> DependenciaOut:
    """Receita própria × transferida (origens 1.7/2.4) e maiores transferências."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_dependencia(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/receita/realizacao", response_model=RealizacaoOut)
def realizacao_receita(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> RealizacaoOut:
    """Arrecadado ÷ previsto (atualizado), no total e por categoria econômica."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_realizacao(session, cod_ibge, periodo, as_of=as_of)


@router.get(
    "/entes/{cod_ibge}/receita/transferencias/conciliacao", response_model=ConciliacaoOut
)
def conciliacao_transferencias(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> ConciliacaoOut:
    """RREO × FPM/FUNDEB (Sprint 1B): divergência sinalizada como qualidade de dado."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_conciliacao(session, cod_ibge, periodo, as_of=as_of)
