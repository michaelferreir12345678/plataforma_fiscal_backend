"""Gold do Resultado Fiscal (Sprint 9): fato do resultado + ajustes metodológicos."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class FatoResultado(Base):
    """gold.fato_resultado — resultado primário e nominal (RREO Anexo 6), versionado.

    Identidades do domínio (LRF; MDF/STN):
    - ``resultado_primario = receita_primaria − despesa_primaria`` (acima da linha);
    - ``resultado_nominal = resultado_primario − juros_liquidos`` (acima da linha);
    - ``resultado_nominal_abaixo = dcl_inicio − dcl_fim`` (= −variação da DCL);
    - os ajustes metodológicos reconciliam a apuração acima × abaixo da linha.
    """

    __tablename__ = "fato_resultado"
    __table_args__ = (
        UniqueConstraint("cod_ibge", "periodo", "versao_entrega", name="uq_fato_resultado_chave"),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    # Acima da linha (orçamentário).
    receita_primaria: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    despesa_primaria: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    resultado_primario: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    juros_ativos: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    juros_passivos: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    resultado_nominal: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    # Abaixo da linha (variação da dívida).
    dcl_inicio: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    dcl_fim: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    resultado_nominal_abaixo: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    resultado_nominal_abaixo_ajustado: Mapped[Decimal | None] = mapped_column(
        Numeric, nullable=True
    )
    resultado_primario_abaixo: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    # Metas fiscais (LDO).
    meta_primario: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    meta_nominal: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class FatoAjusteMetodologico(Base):
    """gold.fato_ajuste_metodologico — ajustes que reconciliam nominal acima × abaixo."""

    __tablename__ = "fato_ajuste_metodologico"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "periodo", "ajuste_codigo", "versao_entrega",
            name="uq_fato_ajuste_metodologico_chave",
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    ajuste_codigo: Mapped[str] = mapped_column(Text, nullable=False)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    valor: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)
