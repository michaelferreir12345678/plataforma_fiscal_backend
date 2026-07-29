"""Acesso a dados (SQL/ORM) do módulo tenancy. Sem regra de negócio (§7)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.modules.assistant.models import ConversaUso
from app.modules.catalog.models import DimEnte
from app.modules.tenancy.models import (
    LICENCA_ATIVA,
    Assinatura,
    AuditLog,
    CarteiraEnte,
    Fatura,
    Licenca,
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


def emails_por_usuario(
    session: Session, *, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """E-mail de cada id informado — para exibir quem assinou uma ação sem N queries."""
    unicos = [i for i in dict.fromkeys(ids) if i is not None]
    if not unicos:
        return {}
    linhas = session.execute(
        select(Usuario.id, Usuario.email).where(Usuario.id.in_(unicos))
    ).all()
    return dict(linhas)  # type: ignore[arg-type]


def create_usuario(
    session: Session, *, email: str, nome: str, senha_hash: str, mfa_ativo: bool
) -> Usuario:
    usuario = Usuario(email=email, nome=nome, senha_hash=senha_hash, mfa_ativo=mfa_ativo)
    session.add(usuario)
    session.flush()
    return usuario


def list_usuarios(session: Session) -> list[Usuario]:
    return list(session.scalars(select(Usuario).order_by(Usuario.email)))


def list_usuarios_org(session: Session, org_id: uuid.UUID) -> list[tuple[Usuario, uuid.UUID, str]]:
    """Lista somente usuários vinculados à organização informada.

    ``usuario`` pertence ao plano de controle e não tem RLS próprio. Portanto, o
    filtro explícito pelo vínculo é parte da barreira de isolamento e não deve ser
    substituído por uma consulta global seguida de filtragem em memória.
    """
    rows = session.execute(
        select(Usuario)
        .add_columns(Membership.papel_id, Papel.nome)
        .join(Membership, Membership.usuario_id == Usuario.id)
        .join(
            Papel,
            and_(
                Papel.id == Membership.papel_id,
                Papel.org_id == Membership.org_id,
            ),
        )
        .where(Membership.org_id == org_id)
        .order_by(Usuario.email)
    ).all()
    return [(row[0], row[1], row[2]) for row in rows]


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


def lock_org(session: Session, org_id: uuid.UUID) -> bool:
    """Serializa alterações de RBAC que podem remover o último administrador.

    O lock na organização — em vez de somente no papel alterado — impede que duas
    transações removam ``administrar`` de papéis diferentes depois de ambas
    observarem a outra como proteção.
    """
    return (
        session.scalar(
            select(Organizacao.id)
            .where(Organizacao.id == org_id)
            .with_for_update()
        )
        is not None
    )


def list_orgs(session: Session) -> list[Organizacao]:
    return list(session.scalars(select(Organizacao).order_by(Organizacao.nome)))


# --- Papel / RBAC (plano de dados, isolado por RLS) ---
def create_papel(session: Session, *, org_id: uuid.UUID, nome: str) -> Papel:
    papel = Papel(org_id=org_id, nome=nome)
    session.add(papel)
    session.flush()
    return papel


def set_papel_capacidades(session: Session, *, papel_id: uuid.UUID, capacidades: list[str]) -> None:
    """Substitui integralmente as capacidades do papel."""
    session.execute(delete(PapelPermissao).where(PapelPermissao.papel_id == papel_id))
    for cap in dict.fromkeys(capacidades):  # dedup preservando ordem
        session.add(PapelPermissao(papel_id=papel_id, capacidade=cap))
    session.flush()


def get_papel(session: Session, papel_id: uuid.UUID) -> Papel | None:
    return session.get(Papel, papel_id)


def list_papeis(session: Session, org_id: uuid.UUID) -> list[Papel]:
    return list(session.scalars(select(Papel).where(Papel.org_id == org_id).order_by(Papel.nome)))


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


def get_membership(
    session: Session, *, org_id: uuid.UUID, usuario_id: uuid.UUID
) -> Membership | None:
    return session.scalar(
        select(Membership).where(
            Membership.org_id == org_id,
            Membership.usuario_id == usuario_id,
        )
    )


def count_admin_members_excluding_papel(
    session: Session, *, org_id: uuid.UUID, papel_id: uuid.UUID
) -> int:
    """Administradores que continuariam existindo em outros papéis."""
    return int(
        session.scalar(
            select(func.count(Membership.id))
            .join(
                PapelPermissao,
                PapelPermissao.papel_id == Membership.papel_id,
            )
            .where(
                Membership.org_id == org_id,
                Membership.papel_id != papel_id,
                PapelPermissao.capacidade == "administrar",
            )
        )
        or 0
    )


def set_membership_escopo(
    session: Session, *, membership_id: uuid.UUID, cods_ibge: list[str]
) -> None:
    for cod in dict.fromkeys(cods_ibge):
        session.add(MembershipEscopo(membership_id=membership_id, cod_ibge=cod))
    session.flush()


def _escopo_ibges(session: Session, membership_id: uuid.UUID) -> frozenset[str] | None:
    rows = list(
        session.scalars(
            select(MembershipEscopo.cod_ibge).where(MembershipEscopo.membership_id == membership_id)
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


def get_carteira_ente(session: Session, *, org_id: uuid.UUID, cod_ibge: str) -> CarteiraEnte | None:
    return session.scalar(
        select(CarteiraEnte).where(CarteiraEnte.org_id == org_id, CarteiraEnte.cod_ibge == cod_ibge)
    )


def remove_carteira_ente(session: Session, *, org_id: uuid.UUID, cod_ibge: str) -> int:
    """Remove um ente da carteira. Retorna quantas linhas foram afetadas."""
    result = session.execute(
        delete(CarteiraEnte).where(CarteiraEnte.org_id == org_id, CarteiraEnte.cod_ibge == cod_ibge)
    )
    session.flush()
    return int(result.rowcount or 0)


def carteira_populacao(
    session: Session, *, org_id: uuid.UUID
) -> list[tuple[str, str | None, int | None, int | None, dict | None]]:
    """(cod_ibge, nome, populacao, pop_ano_ref, pop_source_ref) da carteira — base de cobrança."""
    rows = session.execute(
        select(
            CarteiraEnte.cod_ibge,
            DimEnte.nome,
            DimEnte.populacao,
            DimEnte.pop_ano_ref,
            DimEnte.pop_source_ref,
        )
        .select_from(CarteiraEnte)
        .outerjoin(DimEnte, DimEnte.cod_ibge == CarteiraEnte.cod_ibge)
        .where(CarteiraEnte.org_id == org_id)
        .order_by(CarteiraEnte.cod_ibge)
    ).all()
    return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]


# --- Assinatura / Fatura (billing; RLS por org_id) ---
def get_assinatura(session: Session, *, org_id: uuid.UUID) -> Assinatura | None:
    return session.scalar(select(Assinatura).where(Assinatura.org_id == org_id))


def upsert_assinatura(
    session: Session,
    *,
    org_id: uuid.UUID,
    plano: str,
    metrica_cobranca: str,
    preco_unitario: Decimal,
    moeda: str,
    ciclo: str,
    status: str,
    inicio_vigencia: object | None,
    fim_vigencia: object | None,
) -> Assinatura:
    row = get_assinatura(session, org_id=org_id)
    if row is None:
        row = Assinatura(org_id=org_id)
        session.add(row)
    row.plano = plano
    row.metrica_cobranca = metrica_cobranca
    row.preco_unitario = preco_unitario
    row.moeda = moeda
    row.ciclo = ciclo
    row.status = status
    row.inicio_vigencia = inicio_vigencia  # type: ignore[assignment]
    row.fim_vigencia = fim_vigencia  # type: ignore[assignment]
    row.atualizada_em = datetime.now(UTC)
    session.flush()
    return row


def get_fatura_by_competencia(
    session: Session, *, org_id: uuid.UUID, competencia: str
) -> Fatura | None:
    return session.scalar(
        select(Fatura).where(Fatura.org_id == org_id, Fatura.competencia == competencia)
    )


def insert_fatura(session: Session, **kwargs: object) -> Fatura:
    row = Fatura(**kwargs)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def list_faturas(session: Session, *, org_id: uuid.UUID, limit: int = 24) -> list[Fatura]:
    return list(
        session.scalars(
            select(Fatura)
            .where(Fatura.org_id == org_id)
            .order_by(Fatura.competencia.desc(), Fatura.emitida_em.desc())
            .limit(limit)
        )
    )


def count_conversa_uso_competencia(
    session: Session, *, org_id: uuid.UUID, inicio: datetime, fim: datetime
) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(ConversaUso)
            .where(
                ConversaUso.org_id == org_id,
                ConversaUso.ts >= inicio,
                ConversaUso.ts < fim,
            )
        )
        or 0
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
    session.add(AuditLog(org_id=org_id, usuario_id=usuario_id, acao=acao, recurso=recurso))
    session.flush()


def query_auditoria(
    session: Session,
    *,
    org_id: uuid.UUID,
    acao: str | None = None,
    recurso: str | None = None,
    usuario_id: uuid.UUID | None = None,
    texto: str | None = None,
    de: datetime | None = None,
    ate: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    """Trilha de auditoria filtrável (RLS por org, além do filtro explícito por org_id)."""
    conds = [AuditLog.org_id == org_id]
    if acao:
        conds.append(AuditLog.acao.ilike(f"%{acao}%"))
    if recurso:
        conds.append(AuditLog.recurso.ilike(f"%{recurso}%"))
    if usuario_id is not None:
        conds.append(AuditLog.usuario_id == usuario_id)
    if texto:
        conds.append(AuditLog.acao.ilike(f"%{texto}%") | AuditLog.recurso.ilike(f"%{texto}%"))
    if de is not None:
        conds.append(AuditLog.ts >= de)
    if ate is not None:
        conds.append(AuditLog.ts < ate)
    total = int(session.scalar(select(func.count()).select_from(AuditLog).where(*conds)) or 0)
    rows = list(
        session.scalars(
            select(AuditLog).where(*conds).order_by(AuditLog.ts.desc()).limit(limit).offset(offset)
        )
    )
    return rows, total


# --- Licenças (Sprint 19) ---------------------------------------------------


def list_licencas(
    session: Session, org_id: uuid.UUID, *, somente_ativas: bool = False
) -> list[Licenca]:
    """Licenças da organização, mais recentes primeiro.

    ``somente_ativas`` filtra pelo **status**; a vigência por data é decidida em
    :meth:`Licenca.vigente_em`, para que uma licença expirada por prazo valha como
    expirada sem depender de job que atualize o status.
    """
    conds = [Licenca.org_id == org_id]
    if somente_ativas:
        conds.append(Licenca.status == LICENCA_ATIVA)
    return list(
        session.scalars(select(Licenca).where(*conds).order_by(Licenca.criada_em.desc()))
    )


def get_licenca(session: Session, licenca_id: uuid.UUID) -> Licenca | None:
    return session.get(Licenca, licenca_id)


def add_licenca(session: Session, licenca: Licenca) -> Licenca:
    session.add(licenca)
    session.flush()
    return licenca


def count_licencas_por_org(session: Session) -> dict[uuid.UUID, int]:
    """Licenças ativas por organização — para o painel de uso do superuser."""
    rows = session.execute(
        select(Licenca.org_id, func.count())
        .where(Licenca.status == LICENCA_ATIVA)
        .group_by(Licenca.org_id)
    )
    return {row[0]: int(row[1]) for row in rows}
