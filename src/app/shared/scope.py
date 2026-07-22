"""Escopo multi-tenant (§6.4).

Toda rota ligada a um ente recebe ``ente`` (cod_ibge) e valida que ele está no
escopo do usuário. O escopo é a **carteira da organização** (RLS no ``op``),
possivelmente **restrita** ao subconjunto do usuário (``op.membership_escopo``) e,
para contas do tipo ``estado``, **ampliada** a todos os municípios da UF (Módulo 2,
Sprint 4). Sem escopo válido ⇒ 403.

Este módulo é a fonte única da definição de escopo: o gate por ente
(``assert_ente_in_scope``) e a resolução do conjunto agregado
(``carteira_scope_ibges``) compartilham a mesma regra, de modo que o drill
ente↔carteira é sempre coerente.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_current_principal, get_db
from app.core.errors import ScopeForbiddenError
from app.modules.catalog import repository as catalog_repo
from app.modules.catalog.models import ESFERA_ESTADUAL
from app.modules.tenancy import repository

TIPO_CONTA_ESTADO = "estado"


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
    if repository.get_carteira_ente(session, org_id=principal.org_id, cod_ibge=ente) is not None:
        return
    # Conta estadual: qualquer município da UF monitorada está no escopo.
    if (
        _is_estado(session, principal.org_id)
        and len(ente) == 7
        and ente[:2] in _estado_prefixes(session, principal.org_id)
    ):
        return
    raise ScopeForbiddenError(ente)


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
    return scope


def require_ente_scope(
    ente: str = Query(..., description="Código IBGE do ente."),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
) -> str:
    """Dependência de escopo para rotas com ``?ente={ibge}`` (§6.1). Retorna o ente validado."""
    assert_ente_in_scope(session, principal, ente)
    return ente
