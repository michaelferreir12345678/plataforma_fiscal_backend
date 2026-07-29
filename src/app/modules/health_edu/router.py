"""Endpoints do módulo Saúde & Educação (Sprint 11)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.health_edu import service
from app.modules.health_edu.schemas import (
    EducacaoDetalhe,
    EnriquecimentoDetalhe,
    MemoriaMinimo,
    MinimoArvoreOut,
    MinimosProjecao,
    SaudeDetalhe,
    SerieMinimoOut,
)
from app.shared.scope import assert_ente_in_scope

router = APIRouter(tags=["health_edu"])

_PERIODO_Q = Query(..., description="Período RREO bimestral (ex.: 2024-B6).")
_AS_OF_Q = Query(None, description="Consulta bitemporal 'as of'.")
_ANOS_Q = Query(5, ge=1, le=10, description="Exercícios da janela da série plurianual.")


@router.get("/entes/{cod_ibge}/saude", response_model=SaudeDetalhe)
def detalhe_saude(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> SaudeDetalhe:
    """ASPS sobre impostos+transferências, piso por esfera, expurgo e projeção."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_saude(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/saude/memoria", response_model=MemoriaMinimo)
def memoria_saude(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> MemoriaMinimo:
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_saude(session, cod_ibge, periodo, as_of=as_of).memoria_calculo


@router.get("/entes/{cod_ibge}/saude/serie", response_model=SerieMinimoOut)
def serie_saude(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    anos: int = _ANOS_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> SerieMinimoOut:
    """Série plurianual do ASPS: um ponto por exercício, com a cobertura declarada."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_serie(session, cod_ibge, periodo, "saude", anos=anos, as_of=as_of)


@router.get("/entes/{cod_ibge}/saude/arvore", response_model=MinimoArvoreOut)
def arvore_saude(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    node: str | None = Query(None, description="Nó atual de gold.dim_funcao."),
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> MinimoArvoreOut:
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_saude_arvore(session, cod_ibge, periodo, node, as_of=as_of)


@router.get(
    "/entes/{cod_ibge}/saude/detalhamento-siops",
    response_model=EnriquecimentoDetalhe,
)
def detalhamento_siops(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> EnriquecimentoDetalhe:
    """Enriquecimento SIOPS; explicitamente não altera o mínimo primário RREO."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_siops(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/educacao", response_model=EducacaoDetalhe)
def detalhe_educacao(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> EducacaoDetalhe:
    """MDE e FUNDEB 70%, ambos primariamente apurados no RREO Anexo 8."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_educacao(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/educacao/memoria", response_model=MemoriaMinimo)
def memoria_educacao(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> MemoriaMinimo:
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_educacao(session, cod_ibge, periodo, as_of=as_of).memoria_calculo


@router.get("/entes/{cod_ibge}/educacao/serie", response_model=SerieMinimoOut)
def serie_educacao(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    anos: int = _ANOS_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> SerieMinimoOut:
    """Série plurianual do MDE: um ponto por exercício, com a cobertura declarada."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_serie(
        session, cod_ibge, periodo, "educacao", anos=anos, as_of=as_of
    )


@router.get("/entes/{cod_ibge}/educacao/arvore", response_model=MinimoArvoreOut)
def arvore_educacao(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    node: str | None = Query(None, description="Nó atual: MDE, impostos ou FUNDEB."),
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> MinimoArvoreOut:
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_educacao_arvore(session, cod_ibge, periodo, node, as_of=as_of)


@router.get(
    "/entes/{cod_ibge}/educacao/detalhamento-siope",
    response_model=EnriquecimentoDetalhe,
)
def detalhamento_siope(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> EnriquecimentoDetalhe:
    """Enriquecimentos SIOPE e FNDE; nunca substituem os valores do RREO."""
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_siope(session, cod_ibge, periodo, as_of=as_of)


@router.get("/entes/{cod_ibge}/minimos/projecao", response_model=MinimosProjecao)
def projecao_minimos(
    cod_ibge: str,
    periodo: str = _PERIODO_Q,
    as_of: datetime | None = _AS_OF_Q,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> MinimosProjecao:
    assert_ente_in_scope(session, principal, cod_ibge)
    return service.build_projecao(session, cod_ibge, periodo, as_of=as_of)
