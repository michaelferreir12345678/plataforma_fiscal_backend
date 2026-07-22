"""Gold dos mínimos constitucionais de saúde, educação e FUNDEB.

Os fatos guardam exclusivamente a apuração primária do RREO (Anexos 12 e 8).
SIOPS, SIOPE e repasses FNDE permanecem na silver como enriquecimentos e nunca
participam dos campos de cálculo abaixo.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class FatoSaude(Base):
    """gold.fato_saude — aplicação em ASPS apurada pelo RREO Anexo 12."""

    __tablename__ = "fato_saude"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "periodo", "versao_rreo", "versao_rgf",
            name="uq_fato_saude_chave",
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    base_impostos_transferencias: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    despesa_bruta: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    deducoes_outras: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    rpnp_sem_lastro: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    despesa_aplicada: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    pct_aplicado: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    minimo_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    valor_minimo: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    abaixo_do_minimo: Mapped[bool] = mapped_column(Boolean, nullable=False)
    versao_rreo: Mapped[str] = mapped_column(Text, nullable=False)
    versao_rgf: Mapped[str] = mapped_column(Text, nullable=False, default="nao_aplicavel")


class FatoSaudeSubfuncao(Base):
    """Composição ASPS por subfunção, ligada à ``gold.dim_funcao``."""

    __tablename__ = "fato_saude_subfuncao"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "periodo", "funcao_codigo", "versao_rreo",
            name="uq_fato_saude_subfuncao_chave",
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    funcao_codigo: Mapped[str] = mapped_column(
        Text, ForeignKey("gold.dim_funcao.codigo"), nullable=False
    )
    empenhado: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    liquidado: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    pago: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    valor_computado: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    versao_rreo: Mapped[str] = mapped_column(Text, nullable=False)


class FatoEducacao(Base):
    """gold.fato_educacao — MDE e subvinculação de 70% do FUNDEB (RREO A8)."""

    __tablename__ = "fato_educacao"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "periodo", "versao_rreo", "versao_rgf",
            name="uq_fato_educacao_chave",
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    base_impostos_transferencias: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    despesa_bruta: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    despesa_impostos: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    despesa_fundeb: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    deducoes_outras: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    rpnp_sem_lastro: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    despesa_aplicada: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    pct_aplicado: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    minimo_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    valor_minimo: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    abaixo_do_minimo: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fundeb_base_profissionais: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    fundeb_aplicado_profissionais: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    fundeb_pct_profissionais: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    fundeb_minimo_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    fundeb_valor_minimo: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    fundeb_abaixo_do_minimo: Mapped[bool] = mapped_column(Boolean, nullable=False)
    versao_rreo: Mapped[str] = mapped_column(Text, nullable=False)
    versao_rgf: Mapped[str] = mapped_column(Text, nullable=False, default="nao_aplicavel")
