"""Dependências FastAPI: sessão de banco, principal autenticado e RBAC."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import jwt
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, apply_context
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.modules.tenancy import repository

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


@dataclass(frozen=True)
class Principal:
    """Identidade + autorização do requisitante para a organização ativa."""

    usuario_id: uuid.UUID
    org_id: uuid.UUID | None
    papel: str | None
    capacidades: frozenset[str]
    escopo_ibges: frozenset[str] | None  # None = carteira inteira

    def has_cap(self, cap: str) -> bool:
        return cap in self.capacidades


def get_db() -> Iterator[Session]:
    """Sessão por requisição. Começa em *default-deny* (sem org, is_admin=off)."""
    session = SessionLocal()
    try:
        apply_context(session)  # RLS nega tudo até o principal fixar a org
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_current_principal(
    request: Request,
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_db),
) -> Principal:
    """Valida o JWT, fixa o contexto multi-tenant na sessão e carrega o RBAC da org ativa."""
    try:
        claims = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise AppError(
            status=401, title="Não autenticado", detail="Token inválido ou expirado."
        ) from exc

    try:
        usuario_id = uuid.UUID(str(claims["sub"]))
    except (KeyError, ValueError):
        raise AppError(
            status=401, title="Não autenticado", detail="Token malformado."
        ) from None

    org_raw = claims.get("org")
    org_id = uuid.UUID(str(org_raw)) if org_raw else None

    # Fixa o contexto de RLS na transação da sessão desta requisição.
    apply_context(session, org_id=org_id, user_id=usuario_id, is_admin=False)

    capacidades: frozenset[str] = frozenset()
    escopo_ibges: frozenset[str] | None = None
    papel: str | None = None
    if org_id is not None:
        view = repository.membership_view(session, org_id=org_id, usuario_id=usuario_id)
        if view is not None:
            capacidades = view.capacidades
            escopo_ibges = view.escopo_ibges
            papel = view.papel_nome

    principal = Principal(
        usuario_id=usuario_id,
        org_id=org_id,
        papel=papel,
        capacidades=capacidades,
        escopo_ibges=escopo_ibges,
    )
    request.state.principal = principal
    return principal


def require_capability(capacidade: str) -> Callable[[Principal], Principal]:
    """Fábrica de dependência que exige uma capacidade RBAC (§2 CLAUDE.md)."""

    def _dep(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not principal.has_cap(capacidade):
            raise AppError(
                status=403,
                title="Sem permissão",
                detail=f"Requer a capacidade '{capacidade}'.",
                type_="urn:plataforma-fiscal:error:missing-capability",
            )
        return principal

    return _dep
