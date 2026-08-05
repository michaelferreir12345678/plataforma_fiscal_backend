"""Modelos SQLAlchemy do módulo tenancy (schema ``op``) — Sprint 0.

Isolamento por organização é imposto por RLS (ver migration). As tabelas com
``org_id`` são o plano de dados isolado; ``organizacao`` e ``usuario`` são o plano de
controle (protegidos na borda pela capacidade ``administrar``).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

# Métricas de cobrança suportadas (op.assinatura.metrica_cobranca / op.organizacao).
METRICAS_COBRANCA: tuple[str, ...] = ("por_ente", "por_populacao", "por_consulta_ia", "fixo")

# Capacidades RBAC (op.papel_permissao.capacidade)
#
# A19 (Sprint G1): "editar" nasceu **consumida** — forecast/router.py já condicionava
# renomear/arquivar cenário a ``require_capability("editar")`` desde a Sprint C2, mas a
# capacidade nunca existiu aqui nem no CheckConstraint do banco. Não era possível conceder
# "editar" a papel nenhum (o INSERT em op.papel_permissao violaria a constraint), então os
# dois endpoints devolviam 403 para todo mundo, sempre. A migration 0040 estende o
# CheckConstraint; esta tupla é a única fonte de verdade que ele espelha.
CAPACIDADES: tuple[str, ...] = (
    "ver",
    "exportar",
    "editar",
    "config_alerta",
    "gerar_relatorio",
    "usar_ia",
    "administrar",
)

# Tipos de conta / organização (op.organizacao.tipo_conta)
TIPOS_CONTA: tuple[str, ...] = ("prefeitura", "estado", "consultoria")

# Licenciamento (op.licenca) — Sprint 19.
# ``uf`` libera o território inteiro: os municípios **e** o ente estadual.
TIPOS_LICENCA: tuple[str, ...] = ("ente", "uf", "global")
LICENCA_ATIVA = "ativa"
LICENCA_SUSPENSA = "suspensa"
LICENCA_EXPIRADA = "expirada"
STATUS_LICENCA: tuple[str, ...] = (LICENCA_ATIVA, LICENCA_SUSPENSA, LICENCA_EXPIRADA)

_CAP_SQL = ", ".join(f"'{c}'" for c in CAPACIDADES)
_TIPO_SQL = ", ".join(f"'{t}'" for t in TIPOS_CONTA)
_TIPO_LICENCA_SQL = ", ".join(f"'{t}'" for t in TIPOS_LICENCA)
_STATUS_LICENCA_SQL = ", ".join(f"'{s}'" for s in STATUS_LICENCA)


class Organizacao(Base):
    __tablename__ = "organizacao"
    __table_args__ = (
        CheckConstraint(f"tipo_conta in ({_TIPO_SQL})", name="tipo_conta_valido"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    tipo_conta: Mapped[str] = mapped_column(String(20), nullable=False)
    metrica_cobranca: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    #: Operador da plataforma. Não é papel RBAC: papel vive **dentro** de uma
    #: organização, e o superuser existe fora de todas.
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )


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


class Licenca(Base):
    """O que a organização contratou — a chave comercial que nenhum tenant altera.

    Histórico preservado: suspender troca o ``status``, não apaga a linha. "Esta
    organização já teve acesso a este ente, e entre quais datas?" é pergunta de
    auditoria e de cobrança, e some se a suspensão for um DELETE.
    """

    __tablename__ = "licenca"
    __table_args__ = (
        CheckConstraint(f"tipo in ({_TIPO_LICENCA_SQL})", name="licenca_tipo_valido"),
        CheckConstraint(f"status in ({_STATUS_LICENCA_SQL})", name="licenca_status_valido"),
        CheckConstraint(
            "(tipo = 'ente' AND cod_ibge IS NOT NULL AND uf IS NULL) "
            "OR (tipo = 'uf' AND uf IS NOT NULL AND cod_ibge IS NULL) "
            "OR (tipo = 'global' AND cod_ibge IS NULL AND uf IS NULL)",
            name="licenca_alvo_coerente",
        ),
        CheckConstraint(
            "vigencia_fim IS NULL OR vigencia_fim >= vigencia_inicio",
            name="licenca_vigencia_coerente",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tipo: Mapped[str] = mapped_column(String(8), nullable=False)
    cod_ibge: Mapped[str | None] = mapped_column(String(7), nullable=True)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    vigencia_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    #: ``None`` = sem prazo. Quando presente, vale **até este dia, inclusive**.
    vigencia_fim: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(9), nullable=False, default=LICENCA_ATIVA)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    criada_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    criada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def vigente_em(self, dia: date) -> bool:
        """Ativa **e** dentro da vigência. Expirada por data não precisa de job para valer."""
        if self.status != LICENCA_ATIVA:
            return False
        if dia < self.vigencia_inicio:
            return False
        return self.vigencia_fim is None or dia <= self.vigencia_fim


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


class Assinatura(Base):
    """op.assinatura — plano/preço/métrica de cobrança da organização (RLS por org_id)."""

    __tablename__ = "assinatura"
    __table_args__ = (
        CheckConstraint(
            "status in ('ativa', 'suspensa', 'cancelada')", name="ck_assinatura_status"
        ),
        UniqueConstraint("org_id", name="uq_assinatura_org"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plano: Mapped[str] = mapped_column(Text, nullable=False, default="padrao")
    metrica_cobranca: Mapped[str] = mapped_column(Text, nullable=False)
    preco_unitario: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=Decimal("0"))
    moeda: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    ciclo: Mapped[str] = mapped_column(String(12), nullable=False, default="mensal")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="ativa")
    inicio_vigencia: Mapped[date | None] = mapped_column(Date, nullable=True)
    fim_vigencia: Mapped[date | None] = mapped_column(Date, nullable=True)
    criada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Fatura(Base):
    """op.fatura — fatura por competência com empenho/contrato e memória (RLS por org_id)."""

    __tablename__ = "fatura"
    __table_args__ = (
        CheckConstraint("status in ('aberta', 'paga', 'cancelada')", name="ck_fatura_status"),
        UniqueConstraint("org_id", "competencia", name="uq_fatura_org_competencia"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assinatura_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("assinatura.id", ondelete="SET NULL"), nullable=True
    )
    competencia: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    metrica_cobranca: Mapped[str] = mapped_column(Text, nullable=False)
    quantidade: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=Decimal("0"))
    preco_unitario: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=Decimal("0"))
    valor_total: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=Decimal("0"))
    moeda: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="aberta")
    empenho_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    contrato_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    vencimento: Mapped[date | None] = mapped_column(Date, nullable=True)
    memoria: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    emitida_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    criada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
