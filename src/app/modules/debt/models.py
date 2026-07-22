"""Gold da dívida: DDCL, CAPAG, credores e serviço futuro da dívida."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DimOrigemDivida(Base):
    """gold.dim_origem_divida — Consolidado → dívida interna/externa."""

    __tablename__ = "dim_origem_divida"
    __table_args__ = {"schema": "gold"}

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    parent_codigo: Mapped[str | None] = mapped_column(
        Text, ForeignKey("gold.dim_origem_divida.codigo"), nullable=True
    )
    nivel: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)


class DimCredor(Base):
    """gold.dim_credor — Consolidado → origem → credor SADIPEM."""

    __tablename__ = "dim_credor"
    __table_args__ = {"schema": "gold"}

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    parent_codigo: Mapped[str | None] = mapped_column(
        Text, ForeignKey("gold.dim_credor.codigo"), nullable=True
    )
    nivel: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    origem: Mapped[str | None] = mapped_column(String(20), nullable=True)


class FatoDivida(Base):
    """gold.fato_divida — apuração DDCL do RGF Anexo 02.

    A DCL é sempre recalculada como ``DC − disponibilidades − haveres``. A linha
    reportada pelo RGF é preservada somente para reconciliação/auditoria.
    """

    __tablename__ = "fato_divida"
    __table_args__ = (
        UniqueConstraint("cod_ibge", "periodo", "versao_entrega", name="uq_fato_divida_chave"),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    dc_bruta: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    disponibilidades: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    haveres: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    dcl: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    dcl_reportada: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    diferenca_reconciliacao: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    rcl_ajustada: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    pct_rcl: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    saldo_interno: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    saldo_externo: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class FatoCapag(Base):
    """gold.fato_capag — espelho versionado da publicação anual do Tesouro."""

    __tablename__ = "fato_capag"
    __table_args__ = (
        UniqueConstraint("cod_ibge", "ano_ref", "versao_entrega", name="uq_fato_capag_chave"),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    ano_ref: Mapped[int] = mapped_column(Integer, nullable=False)
    nota_final: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ind_endividamento: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    ind_poupanca: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    ind_liquidez: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    metodologia_versao: Mapped[str | None] = mapped_column(Text, nullable=True)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)


class FatoVencimento(Base):
    """gold.fato_vencimento — principal + juros + encargos por operação/ano."""

    __tablename__ = "fato_vencimento"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge",
            "periodo_ref",
            "id_operacao",
            "ano",
            "mes",
            "versao_entrega",
            name="uq_fato_vencimento_chave",
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    periodo_ref: Mapped[str] = mapped_column(Text, nullable=False)
    id_operacao: Mapped[str] = mapped_column(Text, nullable=False)
    credor_codigo: Mapped[str | None] = mapped_column(
        Text, ForeignKey("gold.dim_credor.codigo"), nullable=True
    )
    ano: Mapped[int] = mapped_column(Integer, nullable=False)
    mes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    principal: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=Decimal(0))
    juros: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=Decimal(0))
    encargos: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=Decimal(0))
    valor: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    versao_entrega: Mapped[str] = mapped_column(Text, nullable=False)
