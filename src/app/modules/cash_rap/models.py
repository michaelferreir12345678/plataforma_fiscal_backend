"""Gold de Caixa & Restos a Pagar (Sprint 10).

- ``dim_fonte_recurso`` — hierarquia da fonte de recurso (Total → grupo → fonte), com a
  flag ``vinculada`` (recurso legalmente vinculado, cuja disponibilidade só cobre
  obrigações da própria vinculação — LRF art. 8º, § único).
- ``fato_disponibilidade`` — suficiência financeira **por fonte** (RGF Anexo 5).
- ``fato_rap`` — quadro de restos a pagar por poder/órgão (RREO Anexo 7).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DimFonteRecurso(Base):
    """gold.dim_fonte_recurso — Total → (Não Vinculados / Vinculados / RPPS) → fonte.

    O SICONFI não expõe código de fonte no Anexo 5; a descrição é a chave natural. O
    ``codigo`` é um slug estável derivado da descrição normalizada; ``vinculada`` marca a
    semântica de vinculação (impacta a suficiência e o expurgo de RPNP sem lastro).
    """

    __tablename__ = "dim_fonte_recurso"
    __table_args__ = {"schema": "gold"}

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    parent_codigo: Mapped[str | None] = mapped_column(
        Text, ForeignKey("gold.dim_fonte_recurso.codigo"), nullable=True
    )
    nivel: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)  # ltree (texto no ORM)
    vinculada: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class FatoDisponibilidade(Base):
    """gold.fato_disponibilidade — suficiência de caixa por fonte (RGF Anexo 5).

    Identidades do domínio (STN, MDF):
    - ``disp_liquida_antes (f) = disp_bruta (a) − obrigacoes (b+c+d+e)``;
    - ``disp_liquida_apos (h) = disp_liquida_antes (f) − rpnp_exercicio (g)``;
    - ``rpnp_sem_lastro = max(0, rpnp_exercicio − max(0, disp_liquida_antes))``.

    ``disp_liquida_apos < 0`` ⇒ há RPNP inscrito sem disponibilidade de caixa (sem lastro),
    que **não conta** na apuração dos mínimos (regra invariante §2.5 do CLAUDE.md).
    """

    __tablename__ = "fato_disponibilidade"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "periodo", "fonte_codigo", "versao_entrega",
            name="uq_fato_disponibilidade_chave",
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    fonte_codigo: Mapped[str] = mapped_column(
        Text, ForeignKey("gold.dim_fonte_recurso.codigo"), nullable=False
    )
    disp_bruta: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    obrigacoes: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    disp_liquida_antes: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    rpnp_exercicio: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    disp_liquida_apos: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    rpnp_sem_lastro: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class FatoRap(Base):
    """gold.fato_rap — restos a pagar por poder/órgão (RREO Anexo 7)."""

    __tablename__ = "fato_rap"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "periodo", "orgao", "versao_entrega", name="uq_fato_rap_chave"
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    orgao: Mapped[str] = mapped_column(Text, nullable=False)
    rpp_inscritos: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    rpp_pagos: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    rpp_cancelados: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    rpp_a_pagar: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    rpnp_inscritos: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    rpnp_liquidados: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    rpnp_pagos: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    rpnp_cancelados: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    rpnp_a_pagar: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    saldo_total: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)
