"""Schemas Pydantic (entrada/saída) do módulo tenancy. Nunca expõe modelos ORM."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.modules.tenancy.models import CAPACIDADES, METRICAS_COBRANCA, TIPOS_CONTA

MetricaCobranca = Literal["por_ente", "por_populacao", "por_consulta_ia", "fixo"]
assert set(METRICAS_COBRANCA) == set(MetricaCobranca.__args__)  # type: ignore[attr-defined]

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
    #: Operador da plataforma (Sprint 19). A UI usa para não oferecer uma rota que
    #: devolveria 403; a garantia continua sendo do backend, não desta flag.
    is_superuser: bool = False


class TrocarOrgInput(BaseModel):
    """Troca da organização ativa. A senha é o crivo: mudar de organização muda tudo
    o que a sessão enxerga, e um token esquecido não pode passear entre clientes."""

    org_id: uuid.UUID
    senha: str = Field(min_length=1)


class AlterarSenhaInput(BaseModel):
    senha_atual: str = Field(min_length=1)
    senha_nova: str = Field(min_length=8)


class PerfilInput(BaseModel):
    nome: str = Field(min_length=1, max_length=160)


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
    papel_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Papel da organização ativa. Se omitido, usa o papel menos privilegiado "
            "disponível nessa organização."
        ),
    )


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    nome: str
    mfa_ativo: bool
    papel_id: uuid.UUID | None = None
    papel_nome: str | None = None


# --- Papel (RBAC) ---
class PapelCreate(BaseModel):
    nome: str
    capacidades: list[Capacidade] = Field(default_factory=list)


class PapelUpdate(BaseModel):
    """Atualização das capacidades de um papel (aba Permissões do Admin)."""

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


# --- Carteira em lote (Sprint 18) ---
class CarteiraLoteInput(BaseModel):
    adicionar: list[CarteiraEnteCreate] = Field(default_factory=list)
    remover: list[str] = Field(default_factory=list, description="Códigos IBGE a remover.")


class CarteiraLoteResult(BaseModel):
    adicionados: list[str]
    removidos: list[str]
    ignorados: list[str] = Field(default_factory=list, description="Já presentes ou inexistentes.")
    nao_licenciados: list[str] = Field(
        default_factory=list,
        description=(
            "Recusados por não estarem cobertos por licença ativa (Sprint 19). Lista "
            "separada de 'ignorados': aqui a causa é comercial, não cadastral."
        ),
    )
    total_carteira: int


# --- Billing (Sprint 18) ---
class AssinaturaInput(BaseModel):
    plano: str = "padrao"
    metrica_cobranca: MetricaCobranca
    preco_unitario: Decimal = Field(default=Decimal("0"), ge=0)
    moeda: str = "BRL"
    ciclo: Literal["mensal", "anual"] = "mensal"
    status: Literal["ativa", "suspensa", "cancelada"] = "ativa"
    inicio_vigencia: date | None = None
    fim_vigencia: date | None = None


class AssinaturaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    plano: str
    metrica_cobranca: str
    preco_unitario: Decimal
    moeda: str
    ciclo: str
    status: str
    inicio_vigencia: date | None = None
    fim_vigencia: date | None = None
    atualizada_em: datetime


class FaturaEmitInput(BaseModel):
    competencia: str | None = Field(default=None, description="YYYY-MM; default = mês corrente.")
    empenho_ref: str | None = Field(default=None, description="Nº do empenho (compra pública).")
    contrato_ref: str | None = Field(default=None, description="Nº do contrato (compra pública).")
    vencimento: date | None = None
    status: Literal["aberta", "paga", "cancelada"] = "aberta"


class FaturaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    assinatura_id: uuid.UUID | None = None
    competencia: str
    metrica_cobranca: str
    quantidade: Decimal
    preco_unitario: Decimal
    valor_total: Decimal
    moeda: str
    status: str
    empenho_ref: str | None = None
    contrato_ref: str | None = None
    vencimento: date | None = None
    memoria: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    emitida_em: datetime


class FaturaPreview(BaseModel):
    """Prévia da fatura corrente, computada ao vivo — auditável (memória + source_refs)."""

    competencia: str
    metrica_cobranca: str
    quantidade: Decimal
    preco_unitario: Decimal
    valor_total: Decimal
    moeda: str
    ja_emitida: bool
    memoria: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)


class BillingOverview(BaseModel):
    org_id: uuid.UUID
    metrica_cobranca: str
    assinatura: AssinaturaOut | None = None
    preview: FaturaPreview
    faturas: list[FaturaOut] = Field(default_factory=list)


# --- Auditoria filtrável (Sprint 18) ---
class AuditoriaItem(BaseModel):
    id: uuid.UUID
    usuario_id: uuid.UUID | None = None
    acao: str
    recurso: str
    ts: datetime


class AuditoriaPage(BaseModel):
    itens: list[AuditoriaItem] = Field(default_factory=list)
    total: int
    limit: int
    offset: int
