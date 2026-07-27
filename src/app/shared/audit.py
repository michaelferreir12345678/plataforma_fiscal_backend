"""Middleware de auditoria (§7): grava requisições autenticadas em ``op.audit_log``.

Roda após o endpoint, lê o :class:`Principal` publicado em ``request.state`` pela
dependência de autenticação e persiste ``(org_id, usuario_id, acao, recurso, ts)``.
Falhas de auditoria nunca quebram a resposta.
"""

from __future__ import annotations

import contextlib
import uuid

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.db import tenant_session
from app.modules.tenancy import repository

# Requisições de leitura triviais que não precisam ser auditadas.
_SKIP_PATHS = frozenset({"/health", "/", "/docs", "/openapi.json", "/redoc"})
_INGESTION_JOBS_PATH = "/admin/ingestion/jobs"


def _skip_request(request: Request) -> bool:
    path = request.url.path
    if path in _SKIP_PATHS:
        return True
    # A Central consulta lista/detalhe de jobs a cada dois segundos. Esses GETs são
    # telemetria operacional, não ações de negócio; criação, confirmação, cancelamento,
    # retry e resultado já geram eventos de domínio completos em op.audit_log.
    return request.method == "GET" and (
        path == _INGESTION_JOBS_PATH
        or path.startswith(f"{_INGESTION_JOBS_PATH}/")
    )


def _persist_audit(
    org_id: uuid.UUID, usuario_id: uuid.UUID, acao: str, recurso: str
) -> None:
    with tenant_session(org_id, user_id=usuario_id) as session:
        repository.insert_audit_log(
            session, org_id=org_id, usuario_id=usuario_id, acao=acao, recurso=recurso
        )


class AuditMiddleware(BaseHTTPMiddleware):
    """Registra em ``op.audit_log`` toda requisição autenticada bem-sucedida."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        if _skip_request(request) or response.status_code >= 400:
            return response

        principal = getattr(request.state, "principal", None)
        if principal is None or principal.org_id is None:
            return response

        acao = f"{request.method} {request.url.path}"
        recurso = request.url.path
        # Auditoria nunca quebra a resposta.
        with contextlib.suppress(Exception):
            await run_in_threadpool(
                _persist_audit,
                principal.org_id,
                principal.usuario_id,
                acao,
                recurso,
            )
        return response
