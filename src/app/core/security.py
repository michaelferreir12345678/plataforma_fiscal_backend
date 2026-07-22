"""Segurança: hashing de senha (pbkdf2_sha256) e emissão/validação de JWT."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import settings

# pbkdf2_sha256 é puro-Python (sem dependência binária de bcrypt) — portável no Windows.
_pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(senha: str) -> str:
    """Gera o hash da senha para persistir em ``op.usuario.senha_hash``."""
    return _pwd_context.hash(senha)


def verify_password(senha: str, senha_hash: str) -> bool:
    """Verifica a senha em texto contra o hash armazenado."""
    return _pwd_context.verify(senha, senha_hash)


def create_access_token(
    *,
    usuario_id: uuid.UUID,
    org_id: uuid.UUID | None,
    capacidades: list[str],
    expires_minutes: int | None = None,
) -> str:
    """Emite um JWT de acesso com o principal (usuário + org ativa + capacidades)."""
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=expires_minutes or settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": str(usuario_id),
        "org": str(org_id) if org_id else None,
        "caps": capacidades,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decodifica e valida o JWT. Lança ``jwt.PyJWTError`` se inválido/expirado."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
