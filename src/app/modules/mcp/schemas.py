"""Schemas de entrada/saída da administração de credenciais MCP (Sprint IA-3)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CredencialCreate(BaseModel):
    """Emissão de credencial. O papel é o que determina as capacidades — não há campo
    de capacidade aqui, e essa ausência é o desenho: credencial é identidade."""

    nome: str = Field(
        min_length=3,
        max_length=120,
        description="Rótulo do cliente MCP (ex.: 'Agente da Controladoria').",
    )
    papel_id: uuid.UUID = Field(
        description="Papel da organização de onde saem as capacidades RBAC da credencial."
    )
    escopo_ibges: list[str] | None = Field(
        default=None,
        description=(
            "Restrição opcional a um subconjunto da carteira. Ausente ⇒ a carteira "
            "inteira (ainda limitada por licença, como qualquer outro acesso)."
        ),
    )
    expira_em: datetime | None = Field(
        default=None, description="Vencimento opcional. Ausente ⇒ sem prazo."
    )


class CredencialOut(BaseModel):
    """Credencial já emitida. **Nunca** carrega o segredo — ele não é recuperável."""

    id: uuid.UUID
    nome: str
    prefixo: str
    papel_id: uuid.UUID
    escopo_ibges: list[str] | None = None
    expira_em: datetime | None = None
    revogada_em: datetime | None = None
    ultimo_uso_em: datetime | None = None
    criado_em: datetime
    ativa: bool


class CredencialCriadaOut(CredencialOut):
    """Resposta da emissão: a **única** vez em que o token aparece."""

    token: str = Field(
        description=(
            "Credencial em claro. Guarde-a agora: só o hash é persistido, e esta resposta "
            "não pode ser reemitida."
        )
    )


class CredenciaisOut(BaseModel):
    itens: list[CredencialOut] = Field(default_factory=list)
