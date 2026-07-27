"""Acesso a dados do módulo assistant (SQL/ORM). Sem regra de negócio (§7)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.assistant.models import Conversa, ConversaUso, NormaChunk


# --------------------------------------------------------------------------- #
# gold.norma_chunk (vector store normativo — compartilhado)
# --------------------------------------------------------------------------- #
def list_norma_chunks(session: Session, *, fontes: Sequence[str] | None = None) -> list[NormaChunk]:
    stmt = select(NormaChunk)
    if fontes:
        stmt = stmt.where(NormaChunk.fonte.in_(list(fontes)))
    return list(session.scalars(stmt.order_by(NormaChunk.fonte, NormaChunk.dispositivo)))


def get_norma_chunk(session: Session, *, fonte: str, dispositivo: str) -> NormaChunk | None:
    return session.scalar(
        select(NormaChunk).where(
            NormaChunk.fonte == fonte, NormaChunk.dispositivo == dispositivo
        )
    )


def count_norma_chunks(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(NormaChunk)) or 0)


def count_norma_com_modelo(session: Session, modelo_embedding: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(NormaChunk)
            .where(NormaChunk.modelo_embedding == modelo_embedding)
        )
        or 0
    )


def upsert_norma_chunk(
    session: Session,
    *,
    fonte: str,
    dispositivo: str,
    titulo: str | None,
    texto: str,
    tags: list[str],
    indicadores: list[str],
    modelo_embedding: str,
    dim: int,
    embedding: list[float],
    source_ref: dict | None,
) -> NormaChunk:
    row = get_norma_chunk(session, fonte=fonte, dispositivo=dispositivo)
    if row is None:
        row = NormaChunk(fonte=fonte, dispositivo=dispositivo)
        session.add(row)
    row.titulo = titulo
    row.texto = texto
    row.tags = tags
    row.indicadores = indicadores
    row.modelo_embedding = modelo_embedding
    row.dim = dim
    row.embedding = embedding
    row.source_ref = source_ref
    session.flush()
    return row


# --------------------------------------------------------------------------- #
# op.conversa / op.conversa_uso (privados do tenant, RLS por org_id)
# --------------------------------------------------------------------------- #
def insert_conversa(
    session: Session,
    *,
    org_id: uuid.UUID,
    usuario_id: uuid.UUID | None,
    tipo: str,
    cod_ibge: str | None,
    periodo: str | None,
    pergunta: str,
    resposta: str,
    recusa: bool,
    dado_disponivel: bool,
    modelo: str | None,
    fontes: list[dict[str, Any]],
    fatos: list[dict[str, Any]],
    source_refs: list[dict[str, Any]],
    dados_incompletos: list[dict[str, Any]],
    as_of: datetime | None,
) -> Conversa:
    row = Conversa(
        org_id=org_id,
        usuario_id=usuario_id,
        tipo=tipo,
        cod_ibge=cod_ibge,
        periodo=periodo,
        pergunta=pergunta,
        resposta=resposta,
        recusa=recusa,
        dado_disponivel=dado_disponivel,
        modelo=modelo,
        fontes=fontes,
        fatos=fatos,
        source_refs=source_refs,
        dados_incompletos=dados_incompletos,
        as_of=as_of,
    )
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def insert_conversa_uso(
    session: Session,
    *,
    org_id: uuid.UUID,
    conversa_id: uuid.UUID | None,
    modelo: str,
    tokens_entrada: int,
    tokens_saida: int,
    latencia_ms: int,
) -> ConversaUso:
    row = ConversaUso(
        org_id=org_id,
        conversa_id=conversa_id,
        modelo=modelo,
        tokens_entrada=tokens_entrada,
        tokens_saida=tokens_saida,
        latencia_ms=latencia_ms,
    )
    session.add(row)
    session.flush()
    return row


def list_conversas(session: Session, *, org_id: uuid.UUID, limit: int = 20) -> list[Conversa]:
    return list(
        session.scalars(
            select(Conversa)
            .where(Conversa.org_id == org_id)
            .order_by(Conversa.criado_em.desc())
            .limit(limit)
        )
    )


@dataclass(frozen=True)
class UsoSummary:
    consultas: int
    tokens_entrada: int
    tokens_saida: int


def usage_summary(session: Session, *, org_id: uuid.UUID, desde: datetime) -> UsoSummary:
    row = session.execute(
        select(
            func.count(ConversaUso.id),
            func.coalesce(func.sum(ConversaUso.tokens_entrada), 0),
            func.coalesce(func.sum(ConversaUso.tokens_saida), 0),
        ).where(ConversaUso.org_id == org_id, ConversaUso.ts >= desde)
    ).one()
    return UsoSummary(
        consultas=int(row[0] or 0),
        tokens_entrada=int(row[1] or 0),
        tokens_saida=int(row[2] or 0),
    )
