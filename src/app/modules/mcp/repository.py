"""Acesso a dados de ``op.mcp_credencial`` (SQL/ORM só aqui, §7 do CLAUDE.md)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.mcp.models import McpCredencial


def insert_credencial(session: Session, credencial: McpCredencial) -> McpCredencial:
    session.add(credencial)
    session.flush()
    return credencial


def get_by_prefixo(session: Session, prefixo: str) -> McpCredencial | None:
    """Localiza a credencial pela parte pública. Roda no plano de controle (ver service)."""
    return session.scalars(
        select(McpCredencial).where(McpCredencial.prefixo == prefixo)
    ).one_or_none()


def get_credencial(
    session: Session, *, org_id: uuid.UUID, credencial_id: uuid.UUID
) -> McpCredencial | None:
    """Busca **sempre** filtrada por ``org_id``.

    O filtro explícito acompanha a RLS de propósito: é ele que faz o serviço devolver 404
    (e não 403) para identificador de outro tenant — a convenção que a Sprint E1 exigiu,
    para que o status não confirme a existência do recurso alheio.
    """
    return session.scalars(
        select(McpCredencial).where(
            McpCredencial.id == credencial_id, McpCredencial.org_id == org_id
        )
    ).one_or_none()


def list_credenciais(session: Session, *, org_id: uuid.UUID) -> list[McpCredencial]:
    return list(
        session.scalars(
            select(McpCredencial)
            .where(McpCredencial.org_id == org_id)
            .order_by(McpCredencial.criado_em.desc())
        )
    )


def marcar_uso(session: Session, *, credencial_id: uuid.UUID, momento: datetime) -> None:
    """Carimba o último uso — é o que permite revogar credencial ociosa com segurança."""
    credencial = session.get(McpCredencial, credencial_id)
    if credencial is not None:
        credencial.ultimo_uso_em = momento


def revogar(session: Session, credencial: McpCredencial, *, momento: datetime) -> McpCredencial:
    """Revoga sem apagar: a linha continua explicando o que aconteceu com aquele acesso."""
    credencial.revogada_em = momento
    session.flush()
    return credencial
