"""Regras de orquestração da ingestão (§7: regra só no service)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.ingestion import repository
from app.modules.ingestion.connectors.registry import CONNECTOR_REGISTRY, FONTE_RELATORIO
from app.modules.ingestion.connectors.siconfi import valid_time_from_periodo
from app.modules.ingestion.models import (
    FONTE_DCA,
    FONTE_MSC,
    FONTE_RGF,
    FONTE_RREO,
    SilverDca,
    SilverMsc,
    SilverRgf,
    SilverRreo,
)
from app.modules.ingestion.repository import MedallionRepository
from app.modules.ingestion.schemas import DataResponse, EntregaStatus, RunRequest, RunResult
from app.shared.ingestion.base import BaseConnector, IngestionJob
from app.shared.ingestion.client import ClientResolver
from app.shared.source_ref import SourceRef

# Silver tipado por relatório (leitura as_of). MSC tem forma própria.
SILVER_MODEL_BY_FONTE: dict[str, type] = {
    FONTE_RREO: SilverRreo,
    FONTE_RGF: SilverRgf,
    FONTE_DCA: SilverDca,
    FONTE_MSC: SilverMsc,
}


def _connector(fonte: str, client: Any) -> BaseConnector:
    cls = CONNECTOR_REGISTRY.get(fonte)
    if cls is None:
        raise AppError(
            status=400,
            title="Fonte desconhecida",
            detail=f"Fonte '{fonte}' não registrada. Válidas: {sorted(CONNECTOR_REGISTRY)}",
        )
    return cls(client, MedallionRepository())


def run(session: Session, resolver: ClientResolver, req: RunRequest) -> RunResult:
    """Executa o backfill de ``req.fonte`` para os entes/anos/períodos informados."""
    connector = _connector(req.fonte, resolver.get(req.fonte))
    state: dict[str, Any] = {**req.model_dump(), "session": session}
    # Parâmetros extras de fontes de arquivo (url/escopo/formato/…) sobem para o topo.
    state.update(state.pop("params", {}) or {})
    jobs = connector.discover(state)
    ingeridos = pulados = silver_rows = 0
    versoes: set[str] = set()
    for job in jobs:
        result = connector.run(session, job, force=req.force)
        if result.status == "ingested":
            ingeridos += 1
        else:
            pulados += 1
        silver_rows += result.silver_rows
        versoes.add(result.versao_entrega)

    return RunResult(
        fonte=req.fonte,
        total_jobs=len(jobs),
        ingeridos=ingeridos,
        pulados=pulados,
        silver_rows=silver_rows,
        versoes_vigentes=sorted(versoes),
    )


def _job_from_bronze(fonte: str, bronze: Any) -> IngestionJob:
    return IngestionJob(
        fonte=fonte,
        relatorio=FONTE_RELATORIO[fonte],
        cod_ibge=bronze.cod_ibge,
        ano=int(str(bronze.periodo)[:4]),
        periodo=bronze.periodo,
        versao=bronze.versao,
        homologada_em=None,
        valid_time=valid_time_from_periodo(bronze.periodo),
    )


def replay(
    session: Session,
    resolver: ClientResolver,
    *,
    ente: str,
    periodo: str,
    fonte: str | None = None,
) -> RunResult:
    """Reprocessa o silver a partir do bronze já armazenado (sem tocar a rede).

    Usado quando um extrato de entregas indica que o ente/período foi afetado.
    """
    fontes = [fonte] if fonte else list(CONNECTOR_REGISTRY)
    repo = MedallionRepository()
    total = silver_rows = 0
    versoes: set[str] = set()
    for f in fontes:
        connector = _connector(f, resolver.get(f))
        for bronze in repository.list_bronze(session, fonte=f, cod_ibge=ente, periodo=periodo):
            job = _job_from_bronze(f, bronze)
            repo.register_entrega(session, job, bronze.hash_payload)
            silver_rows += connector.to_silver(session, job, bronze.payload, bronze.versao)
            versoes.add(bronze.versao)
            total += 1
    return RunResult(
        fonte=fonte or "todas",
        total_jobs=total,
        ingeridos=total,
        pulados=0,
        silver_rows=silver_rows,
        versoes_vigentes=sorted(versoes),
    )


def status(session: Session, *, fonte: str | None = None) -> list[EntregaStatus]:
    """Status por ente/relatório/período (versões e vigência)."""
    relatorio = FONTE_RELATORIO.get(fonte) if fonte else None
    entregas = repository.list_entregas(session, relatorio=relatorio)
    return [
        EntregaStatus(
            cod_ibge=e.cod_ibge,
            relatorio=e.relatorio,
            periodo=e.periodo,
            versao_entrega=e.versao_entrega,
            vigente=e.vigente,
            homologada_em=e.homologada_em,
        )
        for e in entregas
    ]


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {col.name: getattr(row, col.name) for col in row.__table__.columns}


def read_data(
    session: Session,
    *,
    fonte: str,
    ente: str,
    periodo: str,
    as_of: datetime | None = None,
) -> DataResponse:
    """Leitura silver 'as of' (§6.5): resolve a versão efetiva e retorna suas linhas."""
    model = SILVER_MODEL_BY_FONTE.get(fonte)
    if model is None:
        raise AppError(
            status=400,
            title="Fonte sem leitura tipada",
            detail=f"Leitura as_of não suportada para a fonte '{fonte}'.",
        )
    relatorio = FONTE_RELATORIO[fonte]
    versao = repository.resolve_versao(
        session, cod_ibge=ente, relatorio=relatorio, periodo=periodo, as_of=as_of
    )
    rows: list[dict[str, Any]] = []
    if versao is not None:
        rows = [
            _row_to_dict(r)
            for r in repository.read_silver(
                session, model, cod_ibge=ente, periodo=periodo, versao_entrega=versao
            )
        ]
    return DataResponse(
        fonte=fonte,
        cod_ibge=ente,
        periodo=periodo,
        versao_entrega=versao,
        as_of=as_of,
        total=len(rows),
        rows=rows,
        source_ref=SourceRef(relatorio=relatorio, periodo=periodo, versao_entrega=versao),
    )
