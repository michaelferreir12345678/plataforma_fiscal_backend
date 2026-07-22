"""Regras de negócio do módulo tenancy (§7: regra só no service)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.db import admin_session
from app.core.errors import AppError
from app.core.security import create_access_token, hash_password, verify_password
from app.modules.tenancy import repository
from app.modules.tenancy.repository import MembershipView
from app.modules.tenancy.schemas import (
    CarteiraEnteCreate,
    CarteiraEnteOut,
    MembershipInfo,
    MeResponse,
    OrgCreate,
    OrgOut,
    PapelCreate,
    PapelOut,
    TokenResponse,
    UserCreate,
    UserOut,
)


def _to_membership_info(view: MembershipView) -> MembershipInfo:
    return MembershipInfo(
        org_id=view.org_id,
        org_nome=view.org_nome,
        tipo_conta=view.tipo_conta,
        papel=view.papel_nome,
        capacidades=sorted(view.capacidades),
        escopo_ibges=sorted(view.escopo_ibges) if view.escopo_ibges is not None else None,
    )


# --- Autenticação ---
def authenticate(email: str, senha: str) -> TokenResponse:
    """Valida credenciais e emite o JWT com a organização ativa e suas capacidades."""
    with admin_session() as session:
        usuario = repository.get_usuario_by_email(session, email)
        if usuario is None or not verify_password(senha, usuario.senha_hash):
            raise AppError(
                status=401, title="Não autenticado", detail="E-mail ou senha inválidos."
            )
        views = repository.membership_views_for_user(session, usuario.id)
        active = views[0] if views else None
        token = create_access_token(
            usuario_id=usuario.id,
            org_id=active.org_id if active else None,
            capacidades=sorted(active.capacidades) if active else [],
        )
    return TokenResponse(access_token=token)


def build_me(usuario_id: uuid.UUID, org_ativa_id: uuid.UUID | None) -> MeResponse:
    """Monta a resposta de ``GET /me`` (usuário + vínculos + org ativa)."""
    with admin_session() as session:
        usuario = repository.get_usuario(session, usuario_id)
        if usuario is None:
            raise AppError(status=404, title="Não encontrado", detail="Usuário inexistente.")
        views = repository.membership_views_for_user(session, usuario_id)
        infos = [_to_membership_info(v) for v in views]
        org_ativa = next((i for i in infos if i.org_id == org_ativa_id), None)
        return MeResponse(
            usuario_id=usuario.id,
            email=usuario.email,
            nome=usuario.nome,
            org_ativa=org_ativa,
            memberships=infos,
        )


# --- Organização (plano de controle) ---
def create_org(data: OrgCreate) -> OrgOut:
    with admin_session() as session:
        org = repository.create_org(
            session,
            nome=data.nome,
            tipo_conta=data.tipo_conta,
            metrica_cobranca=data.metrica_cobranca,
        )
        return OrgOut.model_validate(org)


def list_orgs() -> list[OrgOut]:
    with admin_session() as session:
        return [OrgOut.model_validate(o) for o in repository.list_orgs(session)]


# --- Usuário (plano de controle) ---
def create_user(data: UserCreate) -> UserOut:
    with admin_session() as session:
        if repository.get_usuario_by_email(session, data.email) is not None:
            raise AppError(status=409, title="Conflito", detail="E-mail já cadastrado.")
        usuario = repository.create_usuario(
            session,
            email=data.email,
            nome=data.nome,
            senha_hash=hash_password(data.senha),
            mfa_ativo=data.mfa_ativo,
        )
        return UserOut.model_validate(usuario)


def list_users() -> list[UserOut]:
    with admin_session() as session:
        return [UserOut.model_validate(u) for u in repository.list_usuarios(session)]


# --- Papel / RBAC (plano de dados, org do principal) ---
def create_papel(session: Session, org_id: uuid.UUID, data: PapelCreate) -> PapelOut:
    papel = repository.create_papel(session, org_id=org_id, nome=data.nome)
    repository.set_papel_capacidades(
        session, papel_id=papel.id, capacidades=list(data.capacidades)
    )
    return PapelOut(
        id=papel.id,
        org_id=papel.org_id,
        nome=papel.nome,
        capacidades=sorted(data.capacidades),
    )


def list_papeis(session: Session, org_id: uuid.UUID) -> list[PapelOut]:
    result: list[PapelOut] = []
    for papel in repository.list_papeis(session, org_id):
        caps = repository.capacidades_do_papel(session, papel.id)
        result.append(
            PapelOut(
                id=papel.id,
                org_id=papel.org_id,
                nome=papel.nome,
                capacidades=caps,
            )
        )
    return result


# --- Carteira (plano de dados, org do principal) ---
def add_carteira_ente(
    session: Session, org_id: uuid.UUID, data: CarteiraEnteCreate
) -> CarteiraEnteOut:
    ente = repository.add_carteira_ente(
        session, org_id=org_id, cod_ibge=data.cod_ibge, grupo=data.grupo, tag=data.tag
    )
    return CarteiraEnteOut.model_validate(ente)


def list_carteira(session: Session, org_id: uuid.UUID) -> list[CarteiraEnteOut]:
    return [
        CarteiraEnteOut.model_validate(e) for e in repository.list_carteira(session, org_id)
    ]
