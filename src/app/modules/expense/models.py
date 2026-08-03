"""Gold da despesa (Sprint 6): duas dimensões hierárquicas + fato dos estágios.

A despesa é lida em **dois eixos** independentes do RREO: por **função/subfunção**
(Anexo 02) e por **natureza da despesa** (Anexo 01). Cada linha do ``fato_despesa``
pertence a um só eixo — o outro eixo recebe a sentinela ``'*'`` (ver
``expense.classificacao.SENTINELA`` e a migration 0009).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DimFuncao(Base):
    """gold.dim_funcao — Função → Subfunção (RREO Anexo 02; padrão §6.1)."""

    __tablename__ = "dim_funcao"
    __table_args__ = {"schema": "gold"}

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    parent_codigo: Mapped[str | None] = mapped_column(Text, nullable=True)
    nivel: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)  # ltree (texto no ORM)


class DimNatureza(Base):
    """gold.dim_natureza — Categoria→Grupo→Modalidade→Elemento (RREO Anexo 01; §6.1)."""

    __tablename__ = "dim_natureza"
    __table_args__ = {"schema": "gold"}

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    parent_codigo: Mapped[str | None] = mapped_column(Text, nullable=True)
    nivel: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)  # ltree (texto no ORM)


class FatoDespesa(Base):
    """gold.fato_despesa — estágios da despesa por eixo (função **ou** natureza).

    Invariante do domínio: ``empenhado ≥ liquidado ≥ pago``; a lacuna
    ``empenhado − pago`` é o **potencial de restos a pagar** do exercício.
    """

    __tablename__ = "fato_despesa"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge",
            "periodo",
            "funcao_codigo",
            "natureza_codigo",
            "versao_entrega",
            name="uq_fato_despesa_chave",
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    funcao_codigo: Mapped[str] = mapped_column(
        Text, ForeignKey("gold.dim_funcao.codigo"), nullable=False
    )
    natureza_codigo: Mapped[str] = mapped_column(
        Text, ForeignKey("gold.dim_natureza.codigo"), nullable=False
    )
    dotacao_inicial: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    dotacao_atualizada: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    empenhado: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    liquidado: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    pago: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    inscrito_rap: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)
    #: Descrição **bruta** da linha do RREO que alimentou este nó — o vínculo do drill
    #: até a linha. Gravada na materialização porque é lá que ainda se sabe qual linha
    #: virou qual nó: o código da função é derivado do texto já limpo, e a mesma
    #: descrição se repete sob funções diferentes.
    linha_origem: Mapped[str | None] = mapped_column(Text, nullable=True)
