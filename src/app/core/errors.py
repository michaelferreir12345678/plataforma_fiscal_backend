"""Tratamento de erros no padrão Problem Details (RFC 7807)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_CONTENT_TYPE = "application/problem+json"


class AppError(Exception):
    """Erro de domínio já mapeado para um Problem Details."""

    def __init__(
        self,
        *,
        status: int,
        title: str,
        detail: str | None = None,
        type_: str = "about:blank",
    ) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.type = type_
        super().__init__(detail or title)


class ScopeForbiddenError(AppError):
    """Ente fora da carteira/escopo do usuário (§6.4) — HTTP 403."""

    def __init__(self, ente: str) -> None:
        super().__init__(
            status=403,
            title="Ente fora do escopo",
            detail=f"O ente {ente} não está na carteira/escopo do usuário.",
            type_="urn:plataforma-fiscal:error:scope-forbidden",
        )


def _problem(status: int, title: str, detail: str | None, type_: str) -> JSONResponse:
    body: dict[str, Any] = {"type": type_, "title": title, "status": status}
    if detail:
        body["detail"] = detail
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_CONTENT_TYPE)


def register_error_handlers(app: FastAPI) -> None:
    """Registra os handlers que serializam erros como ``application/problem+json``."""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _problem(exc.status, exc.title, exc.detail, exc.type)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else None
        title = {401: "Não autenticado", 403: "Acesso negado", 404: "Não encontrado"}.get(
            exc.status_code, "Erro"
        )
        return _problem(exc.status_code, title, detail, "about:blank")

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        body: dict[str, Any] = {
            "type": "urn:plataforma-fiscal:error:validation",
            "title": "Requisição inválida",
            "status": 422,
            "errors": jsonable_encoder(exc.errors()),
        }
        return JSONResponse(status_code=422, content=body, media_type=PROBLEM_CONTENT_TYPE)
