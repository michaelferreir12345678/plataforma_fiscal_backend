"""Modelos da visão agregada de carteira (Módulo 2, Sprint 4).

- ``gold.mart_carteira`` — snapshot (versão vigente) por ente/período/indicador,
  materializado a partir de ``gold.mart_indicador``. É dado público/compartilhado
  (sem RLS): serve ranking, mapa e consolidação sem resolver versão em request.
- ``op.carteira_lote_job`` — ações em lote (relatório/alerta) enfileiradas; é
  operacional e isolado por organização via RLS (schema ``op``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

LOTE_ACOES: tuple[str, ...] = ("relatorio", "alerta")
_ACAO_SQL = ", ".join(f"'{a}'" for a in LOTE_ACOES)


class MartCarteira(Base):
    """gold.mart_carteira — sumarização por ente/período/indicador (faixa + conformidade)."""

    __tablename__ = "mart_carteira"
    __table_args__ = (
        UniqueConstraint("cod_ibge", "periodo", "indicador", name="uq_mart_carteira_chave"),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    indicador: Mapped[str] = mapped_column(Text, nullable=False)
    faixa: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    conformidade_status: Mapped[str] = mapped_column(Text, nullable=False)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CarteiraLoteJob(Base):
    """op.carteira_lote_job — ação em lote enfileirada (relatório/alerta) por organização."""

    __tablename__ = "carteira_lote_job"
    __table_args__ = (
        CheckConstraint(f"acao in ({_ACAO_SQL})", name="ck_carteira_lote_job_acao"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    acao: Mapped[str] = mapped_column(String(20), nullable=False)
    periodo: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="enfileirado")
    total_entes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entes: Mapped[list] = mapped_column(JSONB, nullable=False)
    filtro: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    criado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
