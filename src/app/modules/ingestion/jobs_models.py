"""Modelo do job de ingestão (Central de Dados, Sprint 24).

``op.ingest_job`` é **privado do tenant** (RLS por ``org_id``): torna a operação de
ingestão (run/backfill/replay) um trabalho **assíncrono e rastreável** — com progresso,
tentativas, erro por item, log e resultado (marts recalculados + delta de cobertura).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Estados do ciclo de vida.
STATUS_NA_FILA = "na_fila"
STATUS_EXECUTANDO = "executando"
STATUS_CONCLUIDO = "concluido"
STATUS_FALHOU = "falhou"
STATUS_CANCELADO = "cancelado"

TIPOS = ("run", "backfill", "replay")


class IngestJob(Base):
    """op.ingest_job — uma execução de ingestão enfileirada (RLS por org)."""

    __tablename__ = "ingest_job"
    __table_args__ = {"schema": "op"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("op.organizacao.id", ondelete="CASCADE"), nullable=False
    )
    criado_por: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("op.usuario.id", ondelete="SET NULL"), nullable=True
    )
    fonte: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(String(16), nullable=False)
    entes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    periodos: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    parametros: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_NA_FILA)
    progresso_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    itens_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    itens_ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    itens_erro: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    erro_resumo: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    resultado: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    iniciado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
