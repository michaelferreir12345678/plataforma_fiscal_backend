"""Tarefas assíncronas de ingestão.

Orquestração inicial: um worker Redis/RQ com *cron* + gatilho por evento chama
``run_ingestion_task``/``replay_task``. Como o MVP roda contra Postgres local (sem
Redis obrigatório), o endpoint admin executa a ingestão de forma síncrona; estas
funções existem para o wiring de RQ e podem ser enfileiradas quando o Redis estiver
disponível. Cada task abre sua própria sessão e usa o cliente real do SICONFI.
"""

from __future__ import annotations

from typing import Any

from app.core.db import SessionLocal
from app.modules.ingestion import service
from app.modules.ingestion.schemas import RunRequest
from app.shared.ingestion.client import RealClientResolver


def run_ingestion_task(req_dict: dict[str, Any]) -> dict[str, Any]:
    """Task RQ: executa um backfill. ``req_dict`` = payload de ``RunRequest``."""
    req = RunRequest.model_validate(req_dict)
    resolver = RealClientResolver()
    try:
        with SessionLocal() as session:
            result = service.run(session, resolver, req)
            session.commit()
    finally:
        resolver.close()
    return result.model_dump()


def replay_task(ente: str, periodo: str, fonte: str | None = None) -> dict[str, Any]:
    """Task RQ: reprocessa silver do ente/período a partir do bronze."""
    resolver = RealClientResolver()
    try:
        with SessionLocal() as session:
            result = service.replay(session, resolver, ente=ente, periodo=periodo, fonte=fonte)
            session.commit()
    finally:
        resolver.close()
    return result.model_dump()
