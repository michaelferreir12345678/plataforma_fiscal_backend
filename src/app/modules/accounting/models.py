"""Gold de Patrimônio & MSC (Sprint 12).

- ``dim_conta_pcasp`` — hierarquia PCASP (ltree), conciliando MSC e DCA num código único.
- ``fato_msc_saldo`` — saldos mensais da MSC por conta (a maior tabela; **particionada por
  ``(uf, ano)``**, desenhada para migrar a um OLAP colunar — ver CLAUDE.md §3 e Sprint 12).
- ``mart_msc_rollup`` — rollup pré-calculado (pai = Σ filhos) por nó/mês, com ``has_children``:
  serve o explorador com drill *lazy* e a matriz mensal sem agregação em request (< 300 ms).
- ``fato_balanco`` — balanços da DCA (Patrimonial I-AB, Variações I-HI, Orçamentário I-C/I-D).

Dado público/compartilhado (sem RLS). Bitemporal por ``versao_entrega``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DimContaPcasp(Base):
    """gold.dim_conta_pcasp — hierarquia PCASP posicional (Classe→…→Subitem), via ltree.

    ``codigo`` é a máscara canônica ``C.G.SG.T.ST.II.SS`` (ver ``accounting.pcasp``); a
    hierarquia é derivada da posição dos dígitos, não de uma coluna da fonte. ``natureza``
    é D (devedora) / C (credora); ``fonte_dado`` registra se o nó foi observado na MSC,
    na DCA ou em ambas (rastreabilidade).
    """

    __tablename__ = "dim_conta_pcasp"
    __table_args__ = {"schema": "gold"}

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    parent_codigo: Mapped[str | None] = mapped_column(
        Text, ForeignKey("gold.dim_conta_pcasp.codigo"), nullable=True
    )
    nivel: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)  # ltree (texto no ORM)
    classe: Mapped[int] = mapped_column(Integer, nullable=False)
    natureza: Mapped[str] = mapped_column(String(1), nullable=False, default="D")
    fonte_dado: Mapped[str | None] = mapped_column(Text, nullable=True)  # msc | dca | ambas


class FatoMscSaldo(Base):
    """gold.fato_msc_saldo — saldo mensal por conta PCASP (MSC), particionada por (uf, ano).

    ``saldo_*`` estão na convenção **devedor líquido** (D ``+`` / C ``−``), na qual o rollup
    e a conciliação com o Balanço são exatos. ``mes`` (1..12) acompanha ``periodo``
    (``AAAA-Mnn``). A chave inclui ``uf``/``ano`` porque são colunas de partição.
    """

    __tablename__ = "fato_msc_saldo"
    __table_args__ = (
        UniqueConstraint(
            "uf", "ano", "cod_ibge", "periodo", "cod_conta", "versao_entrega",
            name="uq_fato_msc_saldo_chave",
        ),
        {"schema": "gold"},
    )

    uf: Mapped[str] = mapped_column(String(2), primary_key=True)
    ano: Mapped[int] = mapped_column(Integer, primary_key=True)
    cod_ibge: Mapped[str] = mapped_column(String(7), primary_key=True)
    periodo: Mapped[str] = mapped_column(Text, primary_key=True)  # AAAA-Mnn
    mes: Mapped[int] = mapped_column(Integer, nullable=False)
    cod_conta: Mapped[str] = mapped_column(Text, primary_key=True)
    natureza: Mapped[str | None] = mapped_column(String(1), nullable=True)
    saldo_inicial: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    mov_devedor: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    mov_credor: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    saldo_final: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, primary_key=True)


class MartMscRollup(Base):
    """gold.mart_msc_rollup — saldo *rolled-up* por nó/mês (pai = Σ filhos), leitura otimizada.

    Todo nó (folha e interno) tem seu saldo já agregado, permitindo drill *lazy* (filtra
    ``parent_conta = node``) e matriz mensal (filtra ``cod_conta``) sem recomputar em
    request. ``saldo_*`` em convenção devedor líquido; ``has_children`` evita round-trips.
    """

    __tablename__ = "mart_msc_rollup"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "periodo", "cod_conta", "versao_entrega",
            name="uq_mart_msc_rollup_chave",
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    ano: Mapped[int] = mapped_column(Integer, nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)  # AAAA-Mnn
    mes: Mapped[int] = mapped_column(Integer, nullable=False)
    cod_conta: Mapped[str] = mapped_column(Text, nullable=False)
    parent_conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    nivel: Mapped[int] = mapped_column(Integer, nullable=False)
    classe: Mapped[int] = mapped_column(Integer, nullable=False)
    natureza: Mapped[str] = mapped_column(String(1), nullable=False)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    saldo_inicial: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    saldo_final: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    has_children: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class FatoBalanco(Base):
    """gold.fato_balanco — balanços da DCA (anuais). ``tipo`` seleciona o demonstrativo.

    ``tipo`` ∈ {patrimonial, variacoes, orcamentario_receita, orcamentario_despesa}. Para
    patrimonial/variacoes o ``cod_conta`` é PCASP canônico (drill via ``parent_conta``);
    para os orçamentários é o slug/rótulo da DCA. ``coluna`` é o rótulo da coluna da fonte.
    """

    __tablename__ = "fato_balanco"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "ano", "tipo", "cod_conta", "coluna", "versao_entrega",
            name="uq_fato_balanco_chave",
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    ano: Mapped[int] = mapped_column(Integer, nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)  # = str(ano)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    anexo: Mapped[str | None] = mapped_column(Text, nullable=True)
    cod_conta: Mapped[str] = mapped_column(Text, nullable=False)
    parent_conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    nivel: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conta_descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    coluna: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)
