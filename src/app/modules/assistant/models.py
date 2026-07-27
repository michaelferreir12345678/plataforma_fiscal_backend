"""Modelos da Sprint 17.

- ``gold.norma_chunk`` — corpo normativo fatiado por dispositivo + embedding (RAG).
  Dado público/compartilhado (sem RLS).
- ``op.conversa`` — histórico do assistente, privado da organização (RLS por org_id).
- ``op.conversa_uso`` — telemetria por chamada (tokens/latência), alimenta a cota do plano.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

FONTES_NORMA: tuple[str, ...] = ("LRF", "CF", "MDF")
TIPOS_CONVERSA: tuple[str, ...] = ("pergunta", "resumo_executivo")


class NormaChunk(Base):
    """gold.norma_chunk — dispositivo normativo + embedding portável (pgvector em prod)."""

    __tablename__ = "norma_chunk"
    __table_args__ = (
        CheckConstraint("fonte IN ('LRF', 'CF', 'MDF')", name="ck_norma_chunk_fonte"),
        UniqueConstraint("fonte", "dispositivo", name="uq_norma_chunk_fonte_dispositivo"),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    fonte: Mapped[str] = mapped_column(String(8), nullable=False)
    dispositivo: Mapped[str] = mapped_column(Text, nullable=False)
    titulo: Mapped[str | None] = mapped_column(Text, nullable=True)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    indicadores: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    modelo_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Conversa(Base):
    """op.conversa — uma interação com o assistente (RLS por org_id)."""

    __tablename__ = "conversa"
    __table_args__ = (
        CheckConstraint("tipo IN ('pergunta', 'resumo_executivo')", name="ck_conversa_tipo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    tipo: Mapped[str] = mapped_column(String(24), nullable=False, default="pergunta")
    cod_ibge: Mapped[str | None] = mapped_column(String(7), nullable=True)
    periodo: Mapped[str | None] = mapped_column(Text, nullable=True)
    pergunta: Mapped[str] = mapped_column(Text, nullable=False)
    resposta: Mapped[str] = mapped_column(Text, nullable=False)
    recusa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dado_disponivel: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    modelo: Mapped[str | None] = mapped_column(Text, nullable=True)
    fontes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    fatos: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    dados_incompletos: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConversaUso(Base):
    """op.conversa_uso — telemetria por chamada de LLM (cota do plano, RLS por org_id)."""

    __tablename__ = "conversa_uso"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversa_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversa.id", ondelete="CASCADE"), nullable=True
    )
    modelo: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_entrada: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_saida: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latencia_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
