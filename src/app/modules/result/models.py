"""Gold do Resultado Fiscal (Sprint 9): fato do resultado + ajustes metodológicos.

A Sprint 25B acrescenta ``op.meta_fiscal`` — meta da LDO **declarada pela organização**
quando o ente não a publicou no Anexo 6. É dado privado do tenant, não dado oficial.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
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


class MetaFiscal(Base):
    """op.meta_fiscal — meta da LDO declarada pela organização (RLS por ``org_id``).

    Usada **apenas** quando o RREO Anexo 6 do ente não traz a meta. Nunca se mistura com
    o dado oficial: a resposta declara a origem, e a meta manual fica restrita às telas do
    próprio ente — não entra em agregado de carteira/UF nem em relatório institucional
    (decisão §11.5 da auditoria).
    """

    __tablename__ = "meta_fiscal"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "cod_ibge", "exercicio", "indicador", name="uq_meta_fiscal_chave"
        ),
        CheckConstraint("indicador IN ('primario', 'nominal')", name="ck_meta_fiscal_indicador"),
        CheckConstraint("exercicio BETWEEN 1990 AND 2200", name="ck_meta_fiscal_exercicio"),
        {"schema": "op"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("op.organizacao.id", ondelete="CASCADE"), nullable=False
    )
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    exercicio: Mapped[int] = mapped_column(Integer, nullable=False)
    indicador: Mapped[str] = mapped_column(String(16), nullable=False)  # primario | nominal
    valor: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    fonte_declarada: Mapped[str] = mapped_column(Text, nullable=False)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_por: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    atualizado_por: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    criado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
