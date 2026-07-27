"""Escopo administrativo de organizações, usuários e papéis."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.core.db import admin_session
from app.modules.tenancy.models import (
    Membership,
    Organizacao,
    PapelPermissao,
    Usuario,
)
from tests.conftest import auth_header, login


@pytest.fixture
def usuarios_cleanup() -> Iterator[list[uuid.UUID]]:
    ids: list[uuid.UUID] = []
    yield ids
    with admin_session() as session:
        session.execute(delete(Usuario).where(Usuario.id.in_(ids)))


def test_admin_lista_somente_org_ativa_e_usuarios_vinculados(
    client: TestClient, make_org
) -> None:
    org_a = make_org(capacidades=["ver", "administrar"])
    org_b = make_org(capacidades=["ver", "administrar"])
    token = login(client, org_a.email, org_a.senha)
    headers = auth_header(token)

    orgs = client.get("/orgs", headers=headers)
    assert orgs.status_code == 200, orgs.text
    assert [row["id"] for row in orgs.json()] == [str(org_a.org_id)]

    users = client.get("/users", headers=headers)
    assert users.status_code == 200, users.text
    assert [row["id"] for row in users.json()] == [str(org_a.usuario_id)]
    assert all(row["email"] != org_b.email for row in users.json())
    assert users.json()[0]["papel_id"] is not None
    assert users.json()[0]["papel_nome"] == "Papel"


def test_usuario_sem_administrar_nao_consulta_matriz_de_papeis(
    client: TestClient, make_org
) -> None:
    org = make_org(capacidades=["ver"])
    token = login(client, org.email, org.senha)

    response = client.get("/papeis", headers=auth_header(token))

    assert response.status_code == 403, response.text
    assert response.json()["type"].endswith("missing-capability")


def test_criar_usuario_vincula_org_ativa_e_papel_menos_privilegiado(
    client: TestClient, make_org, usuarios_cleanup: list[uuid.UUID]
) -> None:
    org = make_org(capacidades=["ver", "exportar", "administrar"])
    token = login(client, org.email, org.senha)
    headers = auth_header(token)
    papel = client.post(
        "/papeis",
        headers=headers,
        json={"nome": "Leitura", "capacidades": ["ver"]},
    )
    assert papel.status_code == 201, papel.text

    email = f"novo-{uuid.uuid4().hex}@teste.gov.br"
    response = client.post(
        "/users",
        headers=headers,
        json={"email": email, "nome": "Novo usuário", "senha": "senha1234"},
    )
    assert response.status_code == 201, response.text
    usuario_id = uuid.UUID(response.json()["id"])
    assert response.json()["papel_id"] == papel.json()["id"]
    assert response.json()["papel_nome"] == "Leitura"
    usuarios_cleanup.append(usuario_id)

    with admin_session() as session:
        membership = session.scalar(
            select(Membership).where(Membership.usuario_id == usuario_id)
        )
        assert membership is not None
        assert membership.org_id == org.org_id
        assert membership.papel_id == uuid.UUID(papel.json()["id"])

    users = client.get("/users", headers=headers)
    assert users.status_code == 200
    assert email in {row["email"] for row in users.json()}


def test_criar_usuario_rejeita_papel_de_outra_org_sem_persistir_usuario(
    client: TestClient, make_org
) -> None:
    org_a = make_org(capacidades=["ver", "administrar"])
    org_b = make_org(capacidades=["ver", "administrar"])
    with admin_session() as session:
        papel_b = session.scalar(
            select(Membership.papel_id).where(Membership.id == org_b.membership_id)
        )
    assert papel_b is not None

    token = login(client, org_a.email, org_a.senha)
    email = f"cross-{uuid.uuid4().hex}@teste.gov.br"
    response = client.post(
        "/users",
        headers=auth_header(token),
        json={
            "email": email,
            "nome": "Usuário fora",
            "senha": "senha1234",
            "papel_id": str(papel_b),
        },
    )
    assert response.status_code == 404, response.text

    with admin_session() as session:
        assert session.scalar(select(Usuario).where(Usuario.email == email)) is None


def test_patch_papel_substitui_e_remove_capacidades_sem_duplicar_pk(
    client: TestClient, make_org
) -> None:
    org = make_org(capacidades=["ver", "administrar"])
    token = login(client, org.email, org.senha)
    headers = auth_header(token)
    created = client.post(
        "/papeis",
        headers=headers,
        json={"nome": "Operador", "capacidades": ["ver", "exportar"]},
    )
    assert created.status_code == 201, created.text
    papel_id = uuid.UUID(created.json()["id"])

    first = client.patch(
        f"/papeis/{papel_id}",
        headers=headers,
        json={"capacidades": ["ver"]},
    )
    assert first.status_code == 200, first.text
    assert first.json()["capacidades"] == ["ver"]

    second = client.patch(
        f"/papeis/{papel_id}",
        headers=headers,
        json={"capacidades": ["ver", "usar_ia"]},
    )
    assert second.status_code == 200, second.text
    assert second.json()["capacidades"] == ["usar_ia", "ver"]

    empty = client.patch(
        f"/papeis/{papel_id}",
        headers=headers,
        json={"capacidades": []},
    )
    assert empty.status_code == 200, empty.text
    assert empty.json()["capacidades"] == []


def test_tenant_admin_nao_provisiona_outra_organizacao(
    client: TestClient, make_org
) -> None:
    org = make_org(capacidades=["ver", "administrar"])
    token = login(client, org.email, org.senha)
    nome = f"Tenant órfão {uuid.uuid4().hex}"
    with admin_session() as session:
        antes = int(
            session.scalar(
                select(func.count())
                .select_from(Organizacao)
                .where(Organizacao.nome == nome)
            )
            or 0
        )
    response = client.post(
        "/orgs",
        headers=auth_header(token),
        json={"nome": nome, "tipo_conta": "consultoria"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["type"].endswith("platform-admin-required")
    with admin_session() as session:
        depois = int(
            session.scalar(
                select(func.count())
                .select_from(Organizacao)
                .where(Organizacao.nome == nome)
            )
            or 0
        )
    assert antes == depois == 0


def test_patch_protege_proprio_papel_administrador(
    client: TestClient, make_org
) -> None:
    org = make_org(capacidades=["ver", "administrar"])
    token = login(client, org.email, org.senha)
    with admin_session() as session:
        papel_id = session.scalar(
            select(Membership.papel_id).where(Membership.id == org.membership_id)
        )
    assert papel_id is not None

    response = client.patch(
        f"/papeis/{papel_id}",
        headers=auth_header(token),
        json={"capacidades": ["ver"]},
    )
    assert response.status_code == 409, response.text
    assert response.json()["type"].endswith("last-admin-protected")

    with admin_session() as session:
        capacidades = list(
            session.scalars(
                select(PapelPermissao.capacidade).where(
                    PapelPermissao.papel_id == papel_id
                )
            )
        )
    assert set(capacidades) == {"ver", "administrar"}


def test_papel_admin_nao_atribuido_nao_conta_como_substituto(
    client: TestClient, make_org
) -> None:
    """Um papel cadastrado, mas sem membro, não evita a proteção do admin efetivo."""
    org = make_org(capacidades=["ver", "administrar"])
    token = login(client, org.email, org.senha)
    headers = auth_header(token)
    papel_reserva = client.post(
        "/papeis",
        headers=headers,
        json={"nome": "Reserva sem membro", "capacidades": ["administrar"]},
    )
    assert papel_reserva.status_code == 201, papel_reserva.text

    with admin_session() as session:
        papel_efetivo = session.scalar(
            select(Membership.papel_id).where(Membership.id == org.membership_id)
        )
        membros_reserva = int(
            session.scalar(
                select(func.count())
                .select_from(Membership)
                .where(Membership.papel_id == uuid.UUID(papel_reserva.json()["id"]))
            )
            or 0
        )
    assert papel_efetivo is not None
    assert membros_reserva == 0

    response = client.patch(
        f"/papeis/{papel_efetivo}",
        headers=headers,
        json={"capacidades": ["ver"]},
    )
    assert response.status_code == 409, response.text


def test_remove_admin_de_outro_papel_quando_resta_admin_efetivo(
    client: TestClient, make_org, usuarios_cleanup: list[uuid.UUID]
) -> None:
    """A proteção não congela todos os papéis quando outro membro admin continua."""
    org = make_org(capacidades=["ver", "administrar"])
    token = login(client, org.email, org.senha)
    headers = auth_header(token)
    alvo = client.post(
        "/papeis",
        headers=headers,
        json={"nome": "Administrador secundário", "capacidades": ["ver", "administrar"]},
    )
    assert alvo.status_code == 201, alvo.text
    email = f"admin-sec-{uuid.uuid4().hex}@teste.gov.br"
    usuario = client.post(
        "/users",
        headers=headers,
        json={
            "email": email,
            "nome": "Admin secundário",
            "senha": "senha1234",
            "papel_id": alvo.json()["id"],
        },
    )
    assert usuario.status_code == 201, usuario.text
    usuarios_cleanup.append(uuid.UUID(usuario.json()["id"]))

    response = client.patch(
        f"/papeis/{alvo.json()['id']}",
        headers=headers,
        json={"capacidades": ["ver"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["capacidades"] == ["ver"]
