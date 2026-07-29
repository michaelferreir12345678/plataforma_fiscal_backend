"""Modelos da Sprint 15.

- ``op.alerta`` — fila de alertas da organização (plano de dados, RLS por org_id).
- ``gold.calendario_obrigacao`` — calendário de obrigações (dado compartilhado).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
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


class Alerta(Base):
    """op.alerta — dedup por (org_id, chave); status do gestor preservado nas reavaliações."""

    __tablename__ = "alerta"
    __table_args__ = (
        UniqueConstraint("org_id", "chave", name="uq_alerta_org_chave"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    chave: Mapped[str] = mapped_column(Text, nullable=False)
    categoria: Mapped[str] = mapped_column(String(24), nullable=False)
    severidade: Mapped[str] = mapped_column(String(16), nullable=False)
    prioridade: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    motivo_legal: Mapped[str] = mapped_column(Text, nullable=False)
    acao_sugerida: Mapped[str] = mapped_column(Text, nullable=False)
    prazo: Mapped[date | None] = mapped_column(Date, nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="nova")
    indicador: Mapped[str | None] = mapped_column(Text, nullable=True)
    periodo: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    memoria: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Sprint 25E: sem quem/quando, "resolvido" é uma afirmação sem responsável.
    resolvido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolvido_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CalendarioObrigacao(Base):
    """gold.calendario_obrigacao — prazo por porte/esfera e status de entrega."""

    __tablename__ = "calendario_obrigacao"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "relatorio", "periodo", name="uq_calendario_obrigacao_chave"
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    relatorio: Mapped[str] = mapped_column(Text, nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    periodicidade: Mapped[str] = mapped_column(String(20), nullable=False)
    prazo: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pendente")
    entregue_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    versao_entrega: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
