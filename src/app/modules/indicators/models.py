"""Fatos/marts da gold (Sprint 2): RCL materializada e indicadores classificados."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class FatoRcl(Base):
    """gold.fato_rcl — Receita Corrente Líquida (12 meses móveis), versionada e rastreável."""

    __tablename__ = "fato_rcl"
    __table_args__ = (
        UniqueConstraint("cod_ibge", "periodo_ref", "versao_entrega", name="uq_fato_rcl_chave"),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo_ref: Mapped[str] = mapped_column(Text, nullable=False)
    rcl_12m: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    deducoes: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    receita_corrente: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)
    memoria: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class MartIndicador(Base):
    """gold.mart_indicador — indicador × limite legal com faixa classificada e source_ref."""

    __tablename__ = "mart_indicador"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "periodo", "indicador", "versao_entrega", name="uq_mart_indicador_chave"
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    indicador: Mapped[str] = mapped_column(Text, nullable=False)
    valor_rs: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    valor_pct_rcl: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    faixa: Mapped[str | None] = mapped_column(Text, nullable=True)
    teto_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    source_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)
