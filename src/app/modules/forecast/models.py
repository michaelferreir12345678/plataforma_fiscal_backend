"""Modelos da Sprint 14 (Previsões & Cenários).

- ``gold.fato_projecao`` — projeção materializada (dado analítico compartilhado).
- ``op.cenario`` — simulação salva pela organização (plano de dados, RLS por org).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class FatoProjecao(Base):
    """gold.fato_projecao — previsão de um indicador, sempre com IC (nunca número único)."""

    __tablename__ = "fato_projecao"
    __table_args__ = (
        UniqueConstraint(
            "cod_ibge", "indicador", "modelo", "periodo_alvo", name="uq_fato_projecao_chave"
        ),
        {"schema": "gold"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cod_ibge: Mapped[str] = mapped_column(String(7), nullable=False)
    indicador: Mapped[str] = mapped_column(Text, nullable=False)
    periodo_alvo: Mapped[str] = mapped_column(Text, nullable=False)
    horizonte: Mapped[int] = mapped_column(Integer, nullable=False)
    valor_previsto: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    ic_inferior: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    ic_superior: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    nivel_confianca: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=Decimal(95))
    unidade: Mapped[str] = mapped_column(String(20), nullable=False)
    modelo: Mapped[str] = mapped_column(Text, nullable=False)
    teto_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    faixa: Mapped[str | None] = mapped_column(Text, nullable=True)
    cruza_limite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    gerado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    memoria: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class Cenario(Base):
    """op.cenario — simulação salva de um ente (privada da organização, RLS por org_id).

    Só existe quando o gestor **salva** explicitamente; a rota de simular não grava.
    """

    __tablename__ = "cenario"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ente: Mapped[str] = mapped_column(String(7), nullable=False)
    indicador: Mapped[str] = mapped_column(Text, nullable=False)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    parametros: Mapped[dict] = mapped_column(JSONB, nullable=False)
    resultado: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    criado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Arquivar em vez de apagar: um cenário que embasou decisão não deve sumir do
    #: histórico porque alguém limpou a lista. Some da tela, permanece auditável.
    arquivado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CenarioVersao(Base):
    """op.cenario_versao — cada salvamento do cenário, com a procedência do dado.

    Editar um cenário **cria versão**, não sobrescreve: cenário é peça de decisão, e "o
    que eu levei à reunião de agosto" precisa sobreviver ao ajuste de outubro.

    As três colunas de procedência (``as_of``, ``versoes_entrega``,
    ``premissas_observadas``) são o que separa *reproduzir* de *recalcular*. A última é a
    menos óbvia: "aceitei o IPCA observado" muda de significado quando o observado muda, e
    sem gravar o valor vigente à época a premissa registrada não reproduz nada.
    """

    __tablename__ = "cenario_versao"
    __table_args__ = (
        UniqueConstraint("cenario_id", "versao", name="uq_cenario_versao"),
        {"schema": "op"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cenario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("cenario.id", ondelete="CASCADE"), nullable=False, index=True
    )
    versao: Mapped[int] = mapped_column(Integer, nullable=False)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    parametros: Mapped[dict] = mapped_column(JSONB, nullable=False)
    resultado: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    versoes_entrega: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    premissas_observadas: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    modelo: Mapped[str | None] = mapped_column(Text, nullable=True)
    horizonte: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nota: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
