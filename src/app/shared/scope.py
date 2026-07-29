"""Escopo multi-tenant (§6.4).

Toda rota ligada a um ente recebe ``ente`` (cod_ibge) e valida que ele está no
escopo do usuário. O escopo é a **carteira da organização** (RLS no ``op``),
possivelmente **restrita** ao subconjunto do usuário (``op.membership_escopo``) e,
para contas do tipo ``estado``, **ampliada** a todos os municípios da UF (Módulo 2,
Sprint 4). Sem escopo válido ⇒ 403.

Desde a Sprint 19 há um quarto termo, e ele é o mais forte: **a licença**. Escopo
efetivo = (carteira ∪ expansão estadual) ∩ escopo do membership ∩ entes cobertos por
licença ativa e vigente. Carteira é o que a organização *quer* olhar; licença é o que
ela *pode*. Um ente na carteira sem licença dá 403 — e é assim que uma suspensão tem
efeito imediato sem precisar mexer na carteira do cliente.

Este módulo é a fonte única da definição de escopo: o gate por ente
(``assert_ente_in_scope``) e a resolução do conjunto agregado
(``carteira_scope_ibges``) compartilham a mesma regra, de modo que o drill
ente↔carteira é sempre coerente.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_current_principal, get_db
from app.core.errors import AppError, ScopeForbiddenError
from app.modules.catalog import repository as catalog_repo
from app.modules.catalog.models import ESFERA_ESTADUAL
from app.modules.tenancy import repository

TIPO_CONTA_ESTADO = "estado"


class EnteNaoLicenciadoError(AppError):
    """403 distinto do escopo: o ente existe e está na carteira, mas fora da licença.

    Separar as duas causas é o que evita o suporte perder tempo: "não está na sua
    carteira" e "sua licença não cobre este ente" pedem ações opostas — uma é
    cadastro do próprio cliente, a outra é comercial.
    """

    def __init__(self, ente: str) -> None:
        super().__init__(
            status=403,
            title="Ente não licenciado",
            detail=(
                f"O ente {ente} não está coberto por licença ativa e vigente desta "
                f"organização."
            ),
            type_="urn:plataforma-fiscal:error:ente-nao-licenciado",
        )


@dataclass(frozen=True)
class CoberturaLicenca:
    """O que as licenças ativas da organização cobrem, sem materializar 5.595 códigos."""

    global_: bool
    entes: frozenset[str]
    #: Prefixos de 2 dígitos. Cobrem os municípios da UF **e** o próprio ente estadual.
    ufs: frozenset[str]

    @property
    def vazia(self) -> bool:
        return not self.global_ and not self.entes and not self.ufs

    def cobre(self, cod_ibge: str) -> bool:
        if self.global_:
            return True
        if cod_ibge in self.entes:
            return True
        return cod_ibge[:2] in self.ufs

    def filtrar(self, cods: set[str]) -> set[str]:
        if self.global_:
            return cods
        return {c for c in cods if self.cobre(c)}


def cobertura_licenca(
    session: Session, org_id: uuid.UUID, *, hoje: date | None = None
) -> CoberturaLicenca:
    """Resolve as licenças ativas **e vigentes** da organização.

    A vigência é conferida por data, não só por status: uma licença cujo prazo venceu
    ontem já não vale hoje, mesmo que nenhum job tenha rodado para marcá-la como
    ``expirada``. Deixar isso para um job faria o acesso depender de o relógio ter
    passado por lá.
    """
    dia = hoje or date.today()
    # Uma sessão é uma requisição. Resolver a cobertura uma vez por requisição evita
    # somar uma consulta a cada rota com ``?ente=`` — o gate roda em todas elas.
    chave = ("licenca", org_id, dia)
    em_cache = session.info.get(chave)
    if isinstance(em_cache, CoberturaLicenca):
        return em_cache

    entes: set[str] = set()
    ufs: set[str] = set()
    global_ = False
    for lic in repository.list_licencas(session, org_id, somente_ativas=True):
        if not lic.vigente_em(dia):
            continue
        if lic.tipo == "global":
            global_ = True
        elif lic.tipo == "ente" and lic.cod_ibge:
            entes.add(lic.cod_ibge)
        elif lic.tipo == "uf" and lic.uf:
            ufs.add(lic.uf)
    cobertura = CoberturaLicenca(global_=global_, entes=frozenset(entes), ufs=frozenset(ufs))
    session.info[chave] = cobertura
    return cobertura


def invalidar_cobertura(session: Session, org_id: uuid.UUID) -> None:
    """Descarta a cobertura memorizada — suspender licença tem de valer na hora."""
    for chave in [k for k in session.info if isinstance(k, tuple) and k[:2] == ("licenca", org_id)]:
        session.info.pop(chave, None)


def _estado_prefixes(session: Session, org_id: uuid.UUID) -> set[str]:
    """Prefixos de UF (2 dígitos) monitorados por uma conta estadual.

    Vem dos entes estaduais da carteira: o código do ente estadual (2 dígitos) é o
    prefixo do código IBGE de 7 dígitos dos seus municípios. Para contas não
    estaduais, o conjunto é vazio (nenhuma ampliação por UF).
    """
    prefixes: set[str] = set()
    for c in repository.list_carteira(session, org_id):
        cod = c.cod_ibge
        if len(cod) == 2:
            prefixes.add(cod)
        else:
            ente = catalog_repo.get_dim_ente(session, cod)
            if ente is not None and ente.esfera == ESFERA_ESTADUAL:
                prefixes.add(cod[:2])
    return prefixes


def _is_estado(session: Session, org_id: uuid.UUID) -> bool:
    org = repository.get_org(session, org_id)
    return org is not None and org.tipo_conta == TIPO_CONTA_ESTADO


def assert_ente_in_scope(session: Session, principal: Principal, ente: str) -> None:
    """Valida ``ente`` contra o escopo do principal. Lança 403 se fora."""
    if principal.org_id is None:
        raise ScopeForbiddenError(ente)
    # Restrição por subconjunto da carteira (op.membership_escopo).
    if principal.escopo_ibges is not None and ente not in principal.escopo_ibges:
        raise ScopeForbiddenError(ente)
    # Pertence à carteira da organização? (consulta já filtrada por RLS/org_id)
    no_escopo = (
        repository.get_carteira_ente(session, org_id=principal.org_id, cod_ibge=ente) is not None
        # Conta estadual: qualquer município da UF monitorada está no escopo.
        or (
            _is_estado(session, principal.org_id)
            and len(ente) == 7
            and ente[:2] in _estado_prefixes(session, principal.org_id)
        )
    )
    if not no_escopo:
        raise ScopeForbiddenError(ente)
    # A licença é conferida **depois** do escopo, de propósito. Invertendo a ordem,
    # qualquer ente que a organização nunca contratou responderia "não licenciado" e a
    # mensagem de escopo viraria letra morta. Assim, ``ente-nao-licenciado`` aparece só
    # no caso que a criou: o ente **está** na carteira, mas fora da licença.
    if not cobertura_licenca(session, principal.org_id).cobre(ente):
        raise EnteNaoLicenciadoError(ente)


def carteira_scope_ibges(session: Session, principal: Principal) -> set[str]:
    """Conjunto completo de entes no escopo (para as visões agregadas de carteira).

    = carteira da organização ∪ (municípios da UF, se conta estadual), depois
    **interseção** com o subconjunto do usuário (``membership_escopo``) quando houver.
    Mesma regra do :func:`assert_ente_in_scope`, para drill ente↔carteira coerente.
    """
    if principal.org_id is None:
        return set()
    scope = {c.cod_ibge for c in repository.list_carteira(session, principal.org_id)}
    if _is_estado(session, principal.org_id):
        prefixes = _estado_prefixes(session, principal.org_id)
        scope |= set(catalog_repo.list_ibges_by_prefixes(session, prefixes))
    if principal.escopo_ibges is not None:
        scope &= principal.escopo_ibges
    # Mesma regra do gate por ente: sem licença, o ente não entra em nenhuma visão
    # agregada — senão a carteira mostraria totais que o drill devolveria como 403.
    return cobertura_licenca(session, principal.org_id).filtrar(scope)


def require_ente_scope(
    ente: str = Query(..., description="Código IBGE do ente."),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
) -> str:
    """Dependência de escopo para rotas com ``?ente={ibge}`` (§6.1). Retorna o ente validado."""
    assert_ente_in_scope(session, principal, ente)
    return ente
