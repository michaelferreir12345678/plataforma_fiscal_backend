"""Modelo da Sprint IA-3: ``op.mcp_credencial`` (RLS por org_id).

Uma credencial é **identidade**, nunca permissão. As capacidades vêm do ``papel_id``
(``op.papel_permissao``) e o escopo continua saindo de ``shared/scope.py``. Não há coluna
que conceda ente, capacidade ou licença — e essa ausência é o desenho, não uma lacuna.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class McpCredencial(Base):
    """op.mcp_credencial — credencial de organização para clientes MCP externos."""

    __tablename__ = "mcp_credencial"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizacao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    #: Parte pública da credencial (localiza a linha sem comparar hash de todas).
    prefixo: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    segredo_hash: Mapped[str] = mapped_column(Text, nullable=False)
    papel_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("papel.id", ondelete="CASCADE"), nullable=False
    )
    criado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    #: ``None`` ⇒ carteira inteira; lista ⇒ subconjunto, como ``op.membership_escopo``.
    escopo_ibges: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revogada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ultimo_uso_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def utilizavel_em(self, momento: datetime | None = None) -> bool:
        """Revogação e vencimento conferidos por **data**, não por status de job.

        Mesma decisão da licença (Sprint 19): esperar um job marcar a linha como expirada
        faria o acesso externo depender de o relógio ter passado por lá.
        """
        agora = momento or datetime.now(UTC)
        if self.revogada_em is not None:
            return False
        return not (self.expira_em is not None and self.expira_em <= agora)
