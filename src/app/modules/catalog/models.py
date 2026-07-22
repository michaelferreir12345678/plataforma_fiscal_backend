"""Dimensões conformadas da gold (Sprint 2). Dado público/compartilhado — sem RLS."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ESFERA_MUNICIPAL = "municipal"
ESFERA_ESTADUAL = "estadual"


class DimEnte(Base):
    """gold.dim_ente — cadastro conformado (SICONFI + IBGE). Esfera determina os limites."""

    __tablename__ = "dim_ente"
    __table_args__ = {"schema": "gold"}

    cod_ibge: Mapped[str] = mapped_column(String(7), primary_key=True)
    nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    esfera: Mapped[str | None] = mapped_column(String(10), nullable=True)
    populacao: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rpps: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    possui_tcm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    regiao: Mapped[str | None] = mapped_column(Text, nullable=True)
    pib: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    pop_ano_ref: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pib_ano_ref: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pop_source_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    pib_source_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class DimPeriodo(Base):
    """gold.dim_periodo — hierarquia temporal (ano → bimestre/quadrimestre → mês) via ltree."""

    __tablename__ = "dim_periodo"
    __table_args__ = {"schema": "gold"}

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    parent_codigo: Mapped[str | None] = mapped_column(Text, nullable=True)
    nivel: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)  # ltree (texto no ORM)
    ano: Mapped[int] = mapped_column(Integer, nullable=False)
    mes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bimestre: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quadrimestre: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DimLimiteLegal(Base):
    """gold.dim_limite_legal — tetos/pisos por esfera/poder (DADO, não código; §2 CLAUDE.md)."""

    __tablename__ = "dim_limite_legal"
    __table_args__ = (
        UniqueConstraint("indicador", "esfera", "poder", name="uq_dim_limite_legal_chave"),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    indicador: Mapped[str] = mapped_column(Text, nullable=False)
    esfera: Mapped[str] = mapped_column(String(10), nullable=False)
    poder: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sentido: Mapped[str] = mapped_column(String(5), nullable=False, default="teto")  # teto | piso
    teto_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    alerta_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    prudencial_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
