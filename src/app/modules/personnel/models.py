"""Gold da despesa com pessoal (Sprint 7): dimensão de poder/órgão + fato por poder."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DimPoderOrgao(Base):
    """gold.dim_poder_orgao — Ente→Poder→Órgão→Unidade (padrão §6.1).

    Dimensão **genérica** (compartilhada): o nó raiz ``ENTE`` representa o consolidado
    e os filhos são os poderes (Executivo, Legislativo, …). O recorte por ente vem do
    ``fato_pessoal`` (``cod_ibge``), nunca da dimensão.
    """

    __tablename__ = "dim_poder_orgao"
    __table_args__ = {"schema": "gold"}

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    parent_codigo: Mapped[str | None] = mapped_column(Text, nullable=True)
    nivel: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)  # ltree (texto no ORM)


class FatoPessoal(Base):
    """gold.fato_pessoal — despesa com pessoal por poder (RGF Anexo de Despesa com Pessoal).

    ``despesa_liquida = despesa_bruta − exclusoes`` (LRF art. 19, §1º); as exclusões
    são **sensíveis a RPPS** (inativos/pensionistas com recursos vinculados só saem
    quando o ente tem regime próprio). ``pct_rcl`` = líquida ÷ RCL × 100.
    """

    __tablename__ = "fato_pessoal"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "periodo", "poder_codigo", "versao_entrega", name="uq_fato_pessoal_chave"
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    poder_codigo: Mapped[str] = mapped_column(
        Text, ForeignKey("gold.dim_poder_orgao.codigo"), nullable=False
    )
    despesa_bruta: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    exclusoes: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    despesa_liquida: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    #: Denominador do limite, **como o ente publicou** no Anexo 01 (art. 20 da LRF sobre
    #: a RCL ajustada, EC 105/2019). Guardado junto da apuração porque é dela que faz
    #: parte: sem ele, o percentual não é reproduzível anos depois.
    rcl_ajustada: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    pct_rcl: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)
