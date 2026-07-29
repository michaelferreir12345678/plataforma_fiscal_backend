"""Endpoints de distribuição e ranking de benchmarking."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.benchmark import service
from app.modules.benchmark.schemas import (
    BenchmarkEvolucaoResponse,
    BenchmarkRankingResponse,
    BenchmarkResponse,
    OrdemRanking,
    OrdenacaoRanking,
)
from app.shared.scope import assert_ente_in_scope

router = APIRouter(prefix="/benchmark", tags=["benchmark"])


@router.get("", response_model=BenchmarkResponse)
def benchmark(
    ente: str = Query(..., description="Código IBGE do ente destacado."),
    indicador: str | None = Query(None, description="Indicador de gold.mart_indicador."),
    coorte: str | None = Query(
        None,
        description=(
            "Código/UUID explícito ou critério (porte, regiao, pib) para resolver a faixa do ente."
        ),
    ),
    periodo: str | None = Query(None, description="Período canônico; ausente usa o mais atual."),
    as_of: datetime | None = Query(None, description="Reproduz o snapshot naquele instante."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> BenchmarkResponse:
    """Distribuição da coorte e posição/percentil do ente, sempre destacado."""
    assert_ente_in_scope(session, principal, ente)
    return service.build_benchmark(
        session,
        cod_ibge=ente,
        indicador=indicador,
        coorte=coorte,
        periodo=periodo,
        as_of=as_of,
    )


@router.get("/evolucao", response_model=BenchmarkEvolucaoResponse)
def evolucao(
    ente: str = Query(..., description="Código IBGE do ente acompanhado."),
    indicador: str | None = Query(None, description="Indicador de gold.mart_indicador."),
    coorte: str | None = Query(
        None, description="Coorte fixa da comparação; ausente resolve pela do ente."
    ),
    periodos: int = Query(6, ge=2, le=24, description="Tamanho da janela de períodos."),
    as_of: datetime | None = Query(None, description="Reproduz os snapshots naquele instante."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> BenchmarkEvolucaoResponse:
    """Posição do ente na **mesma** coorte ao longo dos períodos (multi-período)."""
    assert_ente_in_scope(session, principal, ente)
    return service.build_evolucao(
        session, cod_ibge=ente, indicador=indicador, coorte=coorte,
        periodos=periodos, as_of=as_of,
    )


@router.get("/ranking", response_model=BenchmarkRankingResponse)
def ranking(
    ente: str = Query(..., description="Código IBGE do ente ancorado."),
    indicador: str | None = Query(None, description="Indicador de gold.mart_indicador."),
    coorte: str | None = Query(
        None,
        description=(
            "Código/UUID explícito ou critério (porte, regiao, pib) para resolver a faixa do ente."
        ),
    ),
    periodo: str | None = Query(None, description="Período canônico; ausente usa o mais atual."),
    as_of: datetime | None = Query(None, description="Reproduz o snapshot naquele instante."),
    ordenar_por: OrdenacaoRanking = Query("posicao"),
    ordem: OrdemRanking = Query("asc"),
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(100, ge=1, le=500),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> BenchmarkRankingResponse:
    """Ranking ordenável; ``ente_ancora`` permanece disponível fora da ordenação."""
    assert_ente_in_scope(session, principal, ente)
    return service.build_ranking(
        session,
        cod_ibge=ente,
        indicador=indicador,
        coorte=coorte,
        periodo=periodo,
        as_of=as_of,
        ordenar=ordenar_por,
        ordem=ordem,
        pagina=pagina,
        por_pagina=por_pagina,
    )
