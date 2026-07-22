"""Gold da receita (Sprint 5): dimensão de origem (hierárquica) e fato do Anexo 01."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DimOrigemReceita(Base):
    """gold.dim_origem_receita — Categoria→Origem→Espécie→Rubrica→Alínea (padrão §6.1)."""

    __tablename__ = "dim_origem_receita"
    __table_args__ = {"schema": "gold"}

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    parent_codigo: Mapped[str | None] = mapped_column(Text, nullable=True)
    nivel: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)  # ltree (texto no ORM)


class FatoReceita(Base):
    """gold.fato_receita — valores do RREO Anexo 01 por natureza da receita, versionados."""

    __tablename__ = "fato_receita"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "periodo", "origem_codigo", "versao_entrega", name="uq_fato_receita_chave"
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    origem_codigo: Mapped[str] = mapped_column(
        Text, ForeignKey("gold.dim_origem_receita.codigo"), nullable=False
    )
    previsto_inicial: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    previsto_atualizado: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    arrecadado_bimestre: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    arrecadado_acum: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    deducoes: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)
