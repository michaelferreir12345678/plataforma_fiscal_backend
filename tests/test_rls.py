"""Testes de RLS por org_id (critério de aceite: RLS isolando orgs)."""

from __future__ import annotations

from sqlalchemy import func, select

from app.core.db import SessionLocal, admin_session, apply_context, tenant_session
from app.modules.tenancy.models import CarteiraEnte


def test_rls_isola_carteira_por_org(make_org) -> None:
    org_a = make_org(entes=["1100015", "1100023"])
    org_b = make_org(entes=["3550308"])

    # Contexto = org A: enxerga apenas os entes de A (sem filtro na query — só RLS).
    with tenant_session(org_a.org_id) as s:
        cods_a = set(s.scalars(select(CarteiraEnte.cod_ibge)))
    assert cods_a == {"1100015", "1100023"}

    # Contexto = org B: enxerga apenas o ente de B.
    with tenant_session(org_b.org_id) as s:
        cods_b = set(s.scalars(select(CarteiraEnte.cod_ibge)))
    assert cods_b == {"3550308"}


def test_rls_default_deny_sem_contexto(make_org) -> None:
    make_org(entes=["4106902"])
    # Sessão sem org e sem is_admin ⇒ RLS nega tudo.
    session = SessionLocal()
    try:
        apply_context(session)  # default deny
        total = session.scalar(select(func.count()).select_from(CarteiraEnte))
    finally:
        session.close()
    assert total == 0


def test_admin_context_enxerga_atraves_dos_tenants(make_org) -> None:
    make_org(entes=["2611606"])
    make_org(entes=["5300108"])
    with admin_session() as s:
        cods = set(s.scalars(select(CarteiraEnte.cod_ibge)))
    assert {"2611606", "5300108"} <= cods
