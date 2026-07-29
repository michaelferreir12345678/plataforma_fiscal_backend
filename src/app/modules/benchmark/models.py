"""Modelos gold do módulo de benchmarking (Sprint 13)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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


class DimCoorte(Base):
    """Coorte configurável por porte populacional, região ou faixa de PIB."""

    __tablename__ = "dim_coorte"
    __table_args__ = (
        CheckConstraint("criterio IN ('porte', 'regiao', 'pib')", name="ck_dim_coorte_criterio"),
        CheckConstraint(
            "limite_superior IS NULL OR limite_inferior IS NULL "
            "OR limite_superior >= limite_inferior",
            name="ck_dim_coorte_limites",
        ),
        UniqueConstraint("codigo", name="uq_dim_coorte_codigo"),
        UniqueConstraint("criterio", "faixa", name="uq_dim_coorte_criterio_faixa"),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    codigo: Mapped[str] = mapped_column(Text, nullable=False)
    criterio: Mapped[str] = mapped_column(String(20), nullable=False)
    faixa: Mapped[str] = mapped_column(Text, nullable=False)
    rotulo: Mapped[str] = mapped_column(Text, nullable=False)
    unidade_criterio: Mapped[str | None] = mapped_column(String(20), nullable=True)
    limite_inferior: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    limite_superior: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    inclusivo_superior: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ordem: Mapped[int] = mapped_column(Integer, nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DimCoorteVersao(Base):
    """Historico temporal da definicao ajustavel de cada coorte."""

    __tablename__ = "dim_coorte_versao"
    __table_args__ = (
        UniqueConstraint(
            "coorte_id", "valido_desde", name="uq_dim_coorte_versao_inicio"
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    coorte_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("gold.dim_coorte.id", ondelete="CASCADE"), nullable=False
    )
    codigo: Mapped[str] = mapped_column(Text, nullable=False)
    criterio: Mapped[str] = mapped_column(String(20), nullable=False)
    faixa: Mapped[str] = mapped_column(Text, nullable=False)
    rotulo: Mapped[str] = mapped_column(Text, nullable=False)
    unidade_criterio: Mapped[str | None] = mapped_column(String(20), nullable=True)
    limite_inferior: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    limite_superior: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    inclusivo_superior: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ordem: Mapped[int] = mapped_column(Integer, nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)
    valido_desde: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valido_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MartBenchmark(Base):
    """Valor e posição de um ente em um snapshot auditável de sua coorte."""

    __tablename__ = "mart_benchmark"
    __table_args__ = (
        CheckConstraint(
            "percentil >= 0 AND percentil <= 100", name="ck_mart_benchmark_percentil"
        ),
        CheckConstraint("posicao >= 1", name="ck_mart_benchmark_posicao"),
        UniqueConstraint(
            "snapshot_hash",
            "coorte_id",
            "indicador",
            "periodo",
            "cod_ibge",
            name="uq_mart_benchmark_snapshot_ente",
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    coorte_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("gold.dim_coorte.id", ondelete="RESTRICT"), nullable=False
    )
    indicador: Mapped[str] = mapped_column(Text, nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    percentil: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    posicao: Mapped[int] = mapped_column(Integer, nullable=False)
    faixa: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A unidade deixou de ser só 'percentual_rcl'/'brl' na Sprint 25C: os mínimos
    # declaram a própria base ('percentual_impostos_transferencias'), que não cabia
    # no varchar(20) original.
    unidade: Mapped[str] = mapped_column(Text, nullable=False)
    sentido: Mapped[str] = mapped_column(String(20), nullable=False)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)
    memoria: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    calculado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
