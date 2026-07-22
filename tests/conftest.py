"""Fixtures de teste. Usa o Postgres local; garante schema (bootstrap + migrate) uma vez."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.db import admin_session
from app.core.security import hash_password
from app.main import app
from app.modules.catalog import service as catalog_service
from app.modules.limits import service as limits_service
from app.modules.tenancy import repository
from app.modules.tenancy.models import CAPACIDADES, AuditLog, Organizacao, Usuario
from scripts.bootstrap_db import ensure_role_and_database

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema() -> None:
    """Garante role/banco e migra até head (idempotente) antes da suíte."""
    ensure_role_and_database()
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    # Dimensões de referência (limites + períodos + providências) — idempotente.
    with admin_session() as session:
        catalog_service.seed_dimensoes(session)
        limits_service.seed_providencias(session)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@dataclass
class OrgFixture:
    org_id: uuid.UUID
    tipo_conta: str
    email: str
    senha: str
    usuario_id: uuid.UUID
    membership_id: uuid.UUID
    capacidades: list[str] = field(default_factory=list)
    entes: list[str] = field(default_factory=list)
    escopo: list[str] | None = None


def _rand_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex}@teste.gov.br"


@pytest.fixture
def make_org():
    """Fábrica de organização + admin/usuário + carteira + escopo (via contexto admin)."""
    created: list[OrgFixture] = []

    def _factory(
        *,
        tipo_conta: str = "consultoria",
        capacidades: list[str] | None = None,
        entes: list[str] | None = None,
        escopo: list[str] | None = None,
        senha: str = "senha1234",
        email: str | None = None,
    ) -> OrgFixture:
        caps = capacidades if capacidades is not None else list(CAPACIDADES)
        entes = entes or []
        email = email or _rand_email()
        with admin_session() as s:
            org = repository.create_org(
                s, nome=f"Org {uuid.uuid4().hex[:8]}", tipo_conta=tipo_conta, metrica_cobranca=None
            )
            papel = repository.create_papel(s, org_id=org.id, nome="Papel")
            repository.set_papel_capacidades(s, papel_id=papel.id, capacidades=caps)
            for cod in entes:
                repository.add_carteira_ente(
                    s, org_id=org.id, cod_ibge=cod, grupo=None, tag=None
                )
            usuario = repository.create_usuario(
                s, email=email, nome="Usuário", senha_hash=hash_password(senha), mfa_ativo=False
            )
            membership = repository.create_membership(
                s, org_id=org.id, usuario_id=usuario.id, papel_id=papel.id
            )
            if escopo is not None:
                repository.set_membership_escopo(
                    s, membership_id=membership.id, cods_ibge=escopo
                )
            fx = OrgFixture(
                org_id=org.id,
                tipo_conta=tipo_conta,
                email=email,
                senha=senha,
                usuario_id=usuario.id,
                membership_id=membership.id,
                capacidades=caps,
                entes=entes,
                escopo=escopo,
            )
        created.append(fx)
        return fx

    yield _factory

    # Limpeza: remove orgs criadas (cascata apaga papéis, memberships, carteira, etc.).
    with admin_session() as s:
        for fx in created:
            s.execute(delete(AuditLog).where(AuditLog.org_id == fx.org_id))
            s.execute(delete(Organizacao).where(Organizacao.id == fx.org_id))
            s.execute(delete(Usuario).where(Usuario.id == fx.usuario_id))


def login(client: TestClient, email: str, senha: str) -> str:
    """Faz login (OAuth2 password) e retorna o access_token."""
    resp = client.post("/auth/login", data={"username": email, "password": senha})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
