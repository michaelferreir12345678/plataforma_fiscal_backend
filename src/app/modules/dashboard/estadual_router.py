"""Endpoints da Visão Estadual & Consolidação Territorial da UF (Módulo 2, Sprint 23).

Separa, por contrato, o **consolidado dos municípios** (``/uf/{uf}/consolidado`` e afins) do
**ente estadual** (servido por ``/entes/{uf}`` — o Governo do Estado). O drill segue o
padrão §6.1 (``/uf/{uf}/arvore``), cada folha linkando ao cockpit do município. O escopo é
validado na borda: sem ente da UF no escopo ⇒ 403 (``assert_uf_in_scope``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.core.errors import AppError
from app.modules.dashboard import estadual_repository as repo
from app.modules.dashboard import estadual_service as svc
from app.modules.dashboard.estadual_schemas import (
    ArvoreUfResponse,
    ConsolidadoUfResponse,
    DistribuicaoUfResponse,
    MalhaResponse,
    MapaUfResponse,
    RankingUfResponse,
)

router = APIRouter(prefix="/uf", tags=["estadual"])
geo_router = APIRouter(prefix="/geo", tags=["estadual"])


@router.get("/{uf}/consolidado", response_model=ConsolidadoUfResponse)
def consolidado(
    uf: str,
    periodo: str = Query(..., description="Período RREO (ex.: 2024-B6)."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> ConsolidadoUfResponse:
    """Consolidado territorial dos municípios da UF: Σnum/Σden (nunca média) + cobertura."""
    return svc.build_consolidado(session, principal, uf, periodo)


@router.get("/{uf}/ranking", response_model=RankingUfResponse)
def ranking(
    uf: str,
    indicador: str = Query(
        ..., description="rcl | pessoal_executivo | divida_consolidada_liquida | disponibilidade."
    ),
    periodo: str = Query(..., description="Período RREO (ex.: 2024-B6)."),
    regiao: str | None = Query(None, description="Filtra por região da UF."),
    porte: str | None = Query(None, description="pequeno | medio | grande | metropole."),
    ordenar: str = Query("valor", description="valor | nome | codigo."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> RankingUfResponse:
    """Ranking municipal com percentil e destaque (valores nomeados: respeita o escopo)."""
    return svc.build_ranking(
        session,
        principal,
        uf,
        indicador=indicador,
        periodo=periodo,
        regiao=regiao,
        porte=porte,
        ordenar=ordenar,
    )


@router.get("/{uf}/distribuicao", response_model=DistribuicaoUfResponse)
def distribuicao(
    uf: str,
    indicador: str = Query(..., description="Indicador consolidável."),
    periodo: str = Query(..., description="Período RREO (ex.: 2024-B6)."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> DistribuicaoUfResponse:
    """Histograma + percentis + concentração (top-N % do total) — estatística territorial."""
    return svc.build_distribuicao(session, principal, uf, indicador=indicador, periodo=periodo)


@router.get("/{uf}/mapa", response_model=MapaUfResponse)
def mapa(
    uf: str,
    indicador: str = Query(..., description="Indicador consolidável."),
    periodo: str = Query(..., description="Período RREO (ex.: 2024-B6)."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> MapaUfResponse:
    """Valores por município para o coroplético + referência à malha (``/geo/malha/{uf}``)."""
    return svc.build_mapa(session, principal, uf, indicador=indicador, periodo=periodo)


@router.get("/{uf}/arvore", response_model=ArvoreUfResponse)
def arvore(
    uf: str,
    indicador: str = Query("pessoal_executivo", description="Indicador consolidável."),
    periodo: str = Query(..., description="Período RREO (ex.: 2024-B6)."),
    agrupar: str = Query("regiao", description="regiao | porte."),
    node: str | None = Query(None, description="Nó atual; ausente ⇒ raízes (§6.1)."),
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> ArvoreUfResponse:
    """Drill UF→região/porte→município (§6.1). Cada folha linka ``/entes/{ibge}/cockpit``."""
    return svc.build_arvore(
        session, principal, uf, indicador=indicador, periodo=periodo, agrupar=agrupar, node=node
    )


@router.post("/{uf}/consolidado/refresh", response_model=dict)
def refresh(
    uf: str,
    periodo: str = Query(..., description="Período RREO a materializar."),
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> dict:
    """Materializa ``mart_consolidado_uf`` (todos os indicadores v1) da UF/período."""
    svc.assert_uf_in_scope(session, principal, svc.normalizar_uf(uf))
    n = svc.refresh_consolidado(session, uf, periodo)
    return {"uf": svc.normalizar_uf(uf), "periodo": periodo, "indicadores_materializados": n}


@geo_router.get("/malha/{uf}", response_model=MalhaResponse)
def malha(
    uf: str,
    principal: Principal = Depends(require_capability("ver")),
    session: Session = Depends(get_db),
) -> MalhaResponse:
    """Malha municipal real da UF (GeoJSON do IBGE) para o mapa coroplético."""
    uf_prefixo = svc.normalizar_uf(uf)
    svc.assert_uf_in_scope(session, principal, uf_prefixo)
    m = repo.get_malha(session, uf_prefixo)
    if m is None:
        raise AppError(
            status=404,
            title="Malha indisponível",
            detail=f"A malha geográfica da UF {uf_prefixo} ainda não foi ingerida.",
        )
    return MalhaResponse(
        uf=m.uf,
        formato=m.formato,
        fonte=m.fonte,
        ano=m.ano,
        n_areas=m.n_areas,
        simplificacao=m.simplificacao,
        malha=m.malha,
    )
