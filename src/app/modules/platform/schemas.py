"""Contratos do control plane (Sprint 19). Entrada/saída de ``/platform``."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.modules.tenancy.models import STATUS_LICENCA, TIPOS_CONTA, TIPOS_LICENCA

TipoLicenca = Literal["ente", "uf", "global"]
StatusLicenca = Literal["ativa", "suspensa", "expirada"]

assert set(TIPOS_LICENCA) == {"ente", "uf", "global"}
assert set(STATUS_LICENCA) == {"ativa", "suspensa", "expirada"}


class LicencaCreate(BaseModel):
    tipo: TipoLicenca
    cod_ibge: str | None = Field(default=None, max_length=7)
    uf: str | None = Field(default=None, max_length=2)
    vigencia_inicio: date | None = None
    vigencia_fim: date | None = None
    observacao: str | None = None

    @model_validator(mode="after")
    def _alvo_coerente(self) -> LicencaCreate:
        """O alvo tem de casar com o tipo — a mesma regra do CHECK, dita antes do banco.

        Rejeitar aqui devolve 422 com o campo, em vez de um 500 vindo da constraint.
        """
        if self.tipo == "ente" and not self.cod_ibge:
            raise ValueError("tipo=ente exige cod_ibge.")
        if self.tipo == "uf" and not self.uf:
            raise ValueError("tipo=uf exige uf (código IBGE de 2 dígitos).")
        if self.tipo != "ente" and self.cod_ibge:
            raise ValueError("cod_ibge só se aplica a tipo=ente.")
        if self.tipo != "uf" and self.uf:
            raise ValueError("uf só se aplica a tipo=uf.")
        if self.vigencia_fim and self.vigencia_inicio and self.vigencia_fim < self.vigencia_inicio:
            raise ValueError("vigencia_fim não pode ser anterior a vigencia_inicio.")
        return self


class LicencaPatch(BaseModel):
    """Suspender, reativar ou prorrogar. Nunca troca o alvo: isso seria outra licença."""

    status: StatusLicenca | None = None
    vigencia_fim: date | None = None
    observacao: str | None = None

    @model_validator(mode="after")
    def _tem_algo(self) -> LicencaPatch:
        if self.status is None and self.vigencia_fim is None and self.observacao is None:
            raise ValueError("Informe ao menos um campo para alterar.")
        return self


class LicencaOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    tipo: TipoLicenca
    cod_ibge: str | None
    uf: str | None
    vigencia_inicio: date
    vigencia_fim: date | None
    status: StatusLicenca
    #: Status **efetivo hoje**: uma licença ``ativa`` cujo prazo venceu já não vale, e
    #: mostrar só o status guardado esconderia isso de quem confere.
    vigente: bool
    observacao: str | None
    criada_em: datetime


class OrgProvisionar(BaseModel):
    nome: str = Field(min_length=1)
    tipo_conta: Literal["prefeitura", "estado", "consultoria"]
    metrica_cobranca: str | None = None
    admin_email: EmailStr
    admin_nome: str = Field(min_length=1)
    admin_senha: str = Field(min_length=8)
    licencas: list[LicencaCreate] = Field(default_factory=list)


assert set(TIPOS_CONTA) == {"prefeitura", "estado", "consultoria"}


class OrgUsoOut(BaseModel):
    """Consumo da organização — o que sustenta a conversa comercial."""

    org_id: uuid.UUID
    nome: str
    tipo_conta: str
    logo_url: str | None
    criada_em: datetime
    licencas_ativas: int
    entes_licenciados: int | None = Field(
        default=None,
        description="Nulo quando há licença global — o alcance não é enumerável.",
    )
    entes_na_carteira: int
    usuarios: int
    relatorios_gerados: int
    consultas_ia: int
    entes_com_dado: int


class ProvisionamentoOut(BaseModel):
    org_id: uuid.UUID
    nome: str
    admin_usuario_id: uuid.UUID
    admin_email: str
    papel_admin_id: uuid.UUID
    licencas: list[LicencaOut]


class IdentidadeVisualOut(BaseModel):
    alvo: str
    url: str
