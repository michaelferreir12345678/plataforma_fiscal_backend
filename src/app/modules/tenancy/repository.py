"""Acesso a dados (SQL/ORM) do módulo tenancy. Sem regra de negócio (§7)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.tenancy.models import (
    AuditLog,
    CarteiraEnte,
    Membership,
    MembershipEscopo,
    Organizacao,
    Papel,
    PapelPermissao,
    Usuario,
)


@dataclass(frozen=True)
class MembershipView:
    """Projeção de um vínculo do usuário (para login/me e para o principal)."""

    membership_id: uuid.UUID
    org_id: uuid.UUID
    org_nome: str
    tipo_conta: str
    papel_nome: str
    capacidades: frozenset[str]
    escopo_ibges: frozenset[str] | None  # None = carteira inteira


# --- Usuário (plano de controle) ---
def get_usuario_by_email(session: Session, email: str) -> Usuario | None:
    return session.scalar(select(Usuario).where(Usuario.email == email))


def get_usuario(session: Session, usuario_id: uuid.UUID) -> Usuario | None:
    return session.get(Usuario, usuario_id)


def create_usuario(
    session: Session, *, email: str, nome: str, senha_hash: str, mfa_ativo: bool
) -> Usuario:
    usuario = Usuario(email=email, nome=nome, senha_hash=senha_hash, mfa_ativo=mfa_ativo)
    session.add(usuario)
    session.flush()
    return usuario


def list_usuarios(session: Session) -> list[Usuario]:
    return list(session.scalars(select(Usuario).order_by(Usuario.email)))


# --- Organização (plano de controle) ---
def create_org(
    session: Session, *, nome: str, tipo_conta: str, metrica_cobranca: str | None
) -> Organizacao:
    org = Organizacao(nome=nome, tipo_conta=tipo_conta, metrica_cobranca=metrica_cobranca)
    session.add(org)
    session.flush()
    return org


def get_org(session: Session, org_id: uuid.UUID) -> Organizacao | None:
    return session.get(Organizacao, org_id)


def list_orgs(session: Session) -> list[Organizacao]:
    return list(session.scalars(select(Organizacao).order_by(Organizacao.nome)))


# --- Papel / RBAC (plano de dados, isolado por RLS) ---
def create_papel(session: Session, *, org_id: uuid.UUID, nome: str) -> Papel:
    papel = Papel(org_id=org_id, nome=nome)
    session.add(papel)
    session.flush()
    return papel


def set_papel_capacidades(session: Session, *, papel_id: uuid.UUID, capacidades: list[str]) -> None:
    for cap in dict.fromkeys(capacidades):  # dedup preservando ordem
        session.add(PapelPermissao(papel_id=papel_id, capacidade=cap))
    session.flush()


def get_papel(session: Session, papel_id: uuid.UUID) -> Papel | None:
    return session.get(Papel, papel_id)


def list_papeis(session: Session, org_id: uuid.UUID) -> list[Papel]:
    return list(
        session.scalars(select(Papel).where(Papel.org_id == org_id).order_by(Papel.nome))
    )


def capacidades_do_papel(session: Session, papel_id: uuid.UUID) -> list[str]:
    return list(
        session.scalars(
            select(PapelPermissao.capacidade)
            .where(PapelPermissao.papel_id == papel_id)
            .order_by(PapelPermissao.capacidade)
        )
    )


# --- Membership / escopo ---
def create_membership(
    session: Session, *, org_id: uuid.UUID, usuario_id: uuid.UUID, papel_id: uuid.UUID
) -> Membership:
    membership = Membership(org_id=org_id, usuario_id=usuario_id, papel_id=papel_id)
    session.add(membership)
    session.flush()
    return membership


def set_membership_escopo(
    session: Session, *, membership_id: uuid.UUID, cods_ibge: list[str]
) -> None:
    for cod in dict.fromkeys(cods_ibge):
        session.add(MembershipEscopo(membership_id=membership_id, cod_ibge=cod))
    session.flush()


def _escopo_ibges(session: Session, membership_id: uuid.UUID) -> frozenset[str] | None:
    rows = list(
        session.scalars(
            select(MembershipEscopo.cod_ibge).where(
                MembershipEscopo.membership_id == membership_id
            )
        )
    )
    return frozenset(rows) if rows else None


def membership_views_for_user(session: Session, usuario_id: uuid.UUID) -> list[MembershipView]:
    """Todos os vínculos do usuário (usa contexto admin para ler entre orgs)."""
    rows = session.execute(
        select(Membership, Organizacao, Papel)
        .join(Organizacao, Organizacao.id == Membership.org_id)
        .join(Papel, Papel.id == Membership.papel_id)
        .where(Membership.usuario_id == usuario_id)
        .order_by(Organizacao.nome)
    ).all()
    views: list[MembershipView] = []
    for membership, org, papel in rows:
        views.append(
            MembershipView(
                membership_id=membership.id,
                org_id=org.id,
                org_nome=org.nome,
                tipo_conta=org.tipo_conta,
                papel_nome=papel.nome,
                capacidades=frozenset(capacidades_do_papel(session, papel.id)),
                escopo_ibges=_escopo_ibges(session, membership.id),
            )
        )
    return views


def membership_view(
    session: Session, *, org_id: uuid.UUID, usuario_id: uuid.UUID
) -> MembershipView | None:
    """Vínculo do usuário na org ativa (usado para montar o principal, sob RLS da org)."""
    row = session.execute(
        select(Membership, Organizacao, Papel)
        .join(Organizacao, Organizacao.id == Membership.org_id)
        .join(Papel, Papel.id == Membership.papel_id)
        .where(Membership.usuario_id == usuario_id, Membership.org_id == org_id)
    ).first()
    if row is None:
        return None
    membership, org, papel = row
    return MembershipView(
        membership_id=membership.id,
        org_id=org.id,
        org_nome=org.nome,
        tipo_conta=org.tipo_conta,
        papel_nome=papel.nome,
        capacidades=frozenset(capacidades_do_papel(session, papel.id)),
        escopo_ibges=_escopo_ibges(session, membership.id),
    )


# --- Carteira (plano de dados, isolado por RLS) ---
def add_carteira_ente(
    session: Session,
    *,
    org_id: uuid.UUID,
    cod_ibge: str,
    grupo: str | None,
    tag: str | None,
) -> CarteiraEnte:
    ente = CarteiraEnte(org_id=org_id, cod_ibge=cod_ibge, grupo=grupo, tag=tag)
    session.add(ente)
    session.flush()
    return ente


def list_carteira(session: Session, org_id: uuid.UUID) -> list[CarteiraEnte]:
    return list(
        session.scalars(
            select(CarteiraEnte)
            .where(CarteiraEnte.org_id == org_id)
            .order_by(CarteiraEnte.cod_ibge)
        )
    )


def get_carteira_ente(
    session: Session, *, org_id: uuid.UUID, cod_ibge: str
) -> CarteiraEnte | None:
    return session.scalar(
        select(CarteiraEnte).where(
            CarteiraEnte.org_id == org_id, CarteiraEnte.cod_ibge == cod_ibge
        )
    )


# --- Auditoria ---
def insert_audit_log(
    session: Session,
    *,
    org_id: uuid.UUID | None,
    usuario_id: uuid.UUID | None,
    acao: str,
    recurso: str,
) -> None:
    session.add(
        AuditLog(org_id=org_id, usuario_id=usuario_id, acao=acao, recurso=recurso)
    )
    session.flush()
