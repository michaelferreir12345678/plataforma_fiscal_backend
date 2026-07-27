"""Persistência operacional dos relatórios e agendamentos (Sprint 16)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
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

MODELOS: tuple[str, ...] = (
    "executivo",
    "limites",
    "comparativo",
    "conformidade",
    "boletim",
)
FORMATOS: tuple[str, ...] = ("pdf", "xlsx", "pptx")
ESCOPOS: tuple[str, ...] = ("ente", "lote", "estadual")
STATUS_RELATORIO: tuple[str, ...] = (
    "enfileirado",
    "processando",
    "gerado",
    "parcial",
    "falhou",
)
PERIODICIDADES: tuple[str, ...] = ("diario", "semanal", "mensal", "bimestral")


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class Relatorio(Base):
    """Um artefato por ente; ``lote_id`` agrega N artefatos numa solicitação."""

    __tablename__ = "relatorio"
    __table_args__ = (
        CheckConstraint(f"modelo IN ({_sql_values(MODELOS)})", name="relatorio_modelo"),
        CheckConstraint(f"formato IN ({_sql_values(FORMATOS)})", name="relatorio_formato"),
        CheckConstraint(f"escopo IN ({_sql_values(ESCOPOS)})", name="relatorio_escopo"),
        CheckConstraint(f"status IN ({_sql_values(STATUS_RELATORIO)})", name="relatorio_status"),
        CheckConstraint("progresso BETWEEN 0 AND 100", name="relatorio_progresso"),
        UniqueConstraint("lote_id", "cod_ibge", name="uq_relatorio_lote_ente"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lote_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    modelo: Mapped[str] = mapped_column(String(32), nullable=False)
    modelo_versao: Mapped[str] = mapped_column(String(16), nullable=False, default="v1")
    formato: Mapped[str] = mapped_column(String(8), nullable=False)
    escopo: Mapped[str] = mapped_column(String(12), nullable=False)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="enfileirado")
    progresso: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parametros: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cabecalho: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    memoria: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dados_incompletos: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    arquivo_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    arquivo_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    tamanho_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    conteudo_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gerado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RelatorioAgendamento(Base):
    """Regra recorrente; um scheduler chama o worker quando ``proxima_execucao`` vence."""

    __tablename__ = "relatorio_agendamento"
    __table_args__ = (
        CheckConstraint(f"modelo IN ({_sql_values(MODELOS)})", name="relatorio_agendamento_modelo"),
        CheckConstraint(
            f"formato IN ({_sql_values(FORMATOS)})", name="relatorio_agendamento_formato"
        ),
        CheckConstraint(f"escopo IN ({_sql_values(ESCOPOS)})", name="relatorio_agendamento_escopo"),
        CheckConstraint(
            f"periodicidade IN ({_sql_values(PERIODICIDADES)})",
            name="relatorio_agendamento_periodicidade",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    modelo: Mapped[str] = mapped_column(String(32), nullable=False)
    formato: Mapped[str] = mapped_column(String(8), nullable=False)
    escopo: Mapped[str] = mapped_column(String(12), nullable=False)
    entes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    periodicidade: Mapped[str] = mapped_column(String(16), nullable=False)
    parametros: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    proxima_execucao: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ultima_execucao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    criado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
