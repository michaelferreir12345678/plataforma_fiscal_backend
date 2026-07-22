"""Modelos SQLAlchemy do módulo tenancy (schema ``op``) — Sprint 0.

Isolamento por organização é imposto por RLS (ver migration). As tabelas com
``org_id`` são o plano de dados isolado; ``organizacao`` e ``usuario`` são o plano de
controle (protegidos na borda pela capacidade ``administrar``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

# Capacidades RBAC (op.papel_permissao.capacidade)
CAPACIDADES: tuple[str, ...] = (
    "ver",
    "exportar",
    "config_alerta",
    "gerar_relatorio",
    "usar_ia",
    "administrar",
)

# Tipos de conta / organização (op.organizacao.tipo_conta)
TIPOS_CONTA: tuple[str, ...] = ("prefeitura", "estado", "consultoria")

_CAP_SQL = ", ".join(f"'{c}'" for c in CAPACIDADES)
_TIPO_SQL = ", ".join(f"'{t}'" for t in TIPOS_CONTA)


class Organizacao(Base):
    __tablename__ = "organizacao"
    __table_args__ = (
        CheckConstraint(f"tipo_conta in ({_TIPO_SQL})", name="tipo_conta_valido"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    tipo_conta: Mapped[str] = mapped_column(String(20), nullable=False)
    metrica_cobranca: Mapped[str | None] = mapped_column(Text, nullable=True)
    criada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Usuario(Base):
    __tablename__ = "usuario"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    senha_hash: Mapped[str] = mapped_column(Text, nullable=False)
    mfa_ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Papel(Base):
    __tablename__ = "papel"
    __table_args__ = (UniqueConstraint("org_id", "nome", name="papel_org_nome"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nome: Mapped[str] = mapped_column(Text, nullable=False)

    permissoes: Mapped[list[PapelPermissao]] = relationship(
        back_populates="papel", cascade="all, delete-orphan"
    )


class PapelPermissao(Base):
    __tablename__ = "papel_permissao"
    __table_args__ = (
        CheckConstraint(f"capacidade in ({_CAP_SQL})", name="capacidade_valida"),
    )

    papel_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("papel.id", ondelete="CASCADE"), primary_key=True
    )
    capacidade: Mapped[str] = mapped_column(String(20), primary_key=True)

    papel: Mapped[Papel] = relationship(back_populates="permissoes")


class Membership(Base):
    __tablename__ = "membership"
    __table_args__ = (
        UniqueConstraint("org_id", "usuario_id", name="membership_org_usuario"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    usuario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("usuario.id", ondelete="CASCADE"), nullable=False, index=True
    )
    papel_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("papel.id", ondelete="RESTRICT"), nullable=False
    )

    escopos: Mapped[list[MembershipEscopo]] = relationship(
        back_populates="membership", cascade="all, delete-orphan"
    )


class MembershipEscopo(Base):
    """Restringe o usuário a um subconjunto da carteira (§6.4)."""

    __tablename__ = "membership_escopo"

    membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("membership.id", ondelete="CASCADE"), primary_key=True
    )
    cod_ibge: Mapped[str] = mapped_column(String(7), primary_key=True)

    membership: Mapped[Membership] = relationship(back_populates="escopos")


class CarteiraEnte(Base):
    """Ente monitorado pela organização (referencia por código IBGE, nunca copia o dado)."""

    __tablename__ = "carteira_ente"
    __table_args__ = (
        UniqueConstraint("org_id", "cod_ibge", name="carteira_org_ente"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    grupo: Mapped[str | None] = mapped_column(Text, nullable=True)
    tag: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="SET NULL"), nullable=True, index=True
    )
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    acao: Mapped[str] = mapped_column(Text, nullable=False)
    recurso: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
