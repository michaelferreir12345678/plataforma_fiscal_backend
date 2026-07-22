"""Endpoints admin de ingestão (§ Sprint 1). Todos exigem a capacidade 'administrar'."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.modules.ingestion import service
from app.modules.ingestion.schemas import DataResponse, EntregaStatus, RunRequest, RunResult
from app.shared.ingestion.client import ClientResolver, RealClientResolver

router = APIRouter(prefix="/admin/ingestion", tags=["ingestion"])


def get_client_resolver() -> Iterator[ClientResolver]:
    """Resolve o cliente HTTP por fonte (sobrescrito nos testes por um resolver falso)."""
    resolver = RealClientResolver()
    try:
        yield resolver
    finally:
        resolver.close()


@router.get("/status", response_model=list[EntregaStatus])
def ingestion_status(
    fonte: str | None = Query(None, description="Filtra por fonte (ex.: siconfi_rreo)."),
    _: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> list[EntregaStatus]:
    return service.status(session, fonte=fonte)


@router.post("/run", response_model=RunResult)
def ingestion_run(
    req: RunRequest,
    _: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
    resolver: ClientResolver = Depends(get_client_resolver),
) -> RunResult:
    """Backfill controlado de uma fonte (idempotente; retificação por versão/homologação)."""
    return service.run(session, resolver, req)


@router.post("/replay", response_model=RunResult)
def ingestion_replay(
    ente: str = Query(..., description="Código IBGE do ente afetado."),
    periodo: str = Query(..., description="Período canônico (ex.: 2024-B6)."),
    fonte: str | None = Query(None, description="Fonte específica; vazio = todas."),
    _: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
    resolver: ClientResolver = Depends(get_client_resolver),
) -> RunResult:
    """Reprocessa o silver do ente/período a partir do bronze (sem tocar a rede)."""
    return service.replay(session, resolver, ente=ente, periodo=periodo, fonte=fonte)


@router.get("/data", response_model=DataResponse)
def ingestion_data(
    fonte: str = Query(..., description="Fonte tipada (siconfi_rreo/rgf/dca/msc)."),
    ente: str = Query(..., description="Código IBGE."),
    periodo: str = Query(..., description="Período canônico."),
    as_of: datetime | None = Query(
        None, description="Reproduz a versão vigente naquele instante (§6.5)."
    ),
    _: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> DataResponse:
    """Leitura silver 'as of': versão vigente (default) ou histórica (``as_of``)."""
    return service.read_data(session, fonte=fonte, ente=ente, periodo=periodo, as_of=as_of)
