"""Dimensão de providências legais por faixa (Sprint 3). Gold — sem RLS."""

from __future__ import annotations

import uuid

from sqlalchemy import Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DimProvidenciaLegal(Base):
    """gold.dim_providencia_legal — o que fazer em cada faixa de cada indicador (DADO)."""

    __tablename__ = "dim_providencia_legal"
    __table_args__ = (
        UniqueConstraint("indicador", "faixa", "texto", name="uq_dim_providencia_chave"),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    indicador: Mapped[str] = mapped_column(Text, nullable=False)
    faixa: Mapped[str] = mapped_column(Text, nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    base_legal: Mapped[str | None] = mapped_column(Text, nullable=True)
