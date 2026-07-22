"""Schemas Pydantic (entrada/saída) do módulo tenancy. Nunca expõe modelos ORM."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.modules.tenancy.models import CAPACIDADES, TIPOS_CONTA

Capacidade = Literal[
    "ver", "exportar", "config_alerta", "gerar_relatorio", "usar_ia", "administrar"
]
TipoConta = Literal["prefeitura", "estado", "consultoria"]

assert set(CAPACIDADES) == set(Capacidade.__args__)  # type: ignore[attr-defined]
assert set(TIPOS_CONTA) == set(TipoConta.__args__)  # type: ignore[attr-defined]


# --- Auth ---
class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class MembershipInfo(BaseModel):
    org_id: uuid.UUID
    org_nome: str
    tipo_conta: TipoConta
    papel: str
    capacidades: list[Capacidade]
    escopo_ibges: list[str] | None = Field(
        default=None, description="None = carteira inteira; lista = subconjunto."
    )


class MeResponse(BaseModel):
    usuario_id: uuid.UUID
    email: EmailStr
    nome: str
    org_ativa: MembershipInfo | None = None
    memberships: list[MembershipInfo] = Field(default_factory=list)


# --- Organização ---
class OrgCreate(BaseModel):
    nome: str
    tipo_conta: TipoConta
    metrica_cobranca: str | None = None


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nome: str
    tipo_conta: TipoConta
    metrica_cobranca: str | None = None
    criada_em: datetime


# --- Usuário ---
class UserCreate(BaseModel):
    email: EmailStr
    nome: str
    senha: str = Field(min_length=8)
    mfa_ativo: bool = False


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    nome: str
    mfa_ativo: bool


# --- Papel (RBAC) ---
class PapelCreate(BaseModel):
    nome: str
    capacidades: list[Capacidade] = Field(default_factory=list)


class PapelOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    nome: str
    capacidades: list[Capacidade]


# --- Carteira ---
class CarteiraEnteCreate(BaseModel):
    cod_ibge: str = Field(min_length=1, max_length=7)
    grupo: str | None = None
    tag: str | None = None


class CarteiraEnteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    cod_ibge: str
    grupo: str | None = None
    tag: str | None = None
