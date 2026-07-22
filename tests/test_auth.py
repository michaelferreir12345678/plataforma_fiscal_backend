"""Testes de autenticação/JWT e /me (critério de aceite: login JWT)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_header, login


def test_login_emite_jwt_e_me_retorna_usuario(client: TestClient, make_org) -> None:
    fx = make_org(tipo_conta="prefeitura", entes=["2304400"])

    resp = client.post("/auth/login", data={"username": fx.email, "password": fx.senha})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    token = body["access_token"]
    assert token

    me = client.get("/me", headers=auth_header(token))
    assert me.status_code == 200, me.text
    data = me.json()
    assert data["email"] == fx.email
    assert data["org_ativa"] is not None
    assert data["org_ativa"]["org_id"] == str(fx.org_id)
    assert data["org_ativa"]["tipo_conta"] == "prefeitura"


def test_login_senha_invalida_retorna_401(client: TestClient, make_org) -> None:
    fx = make_org()
    resp = client.post("/auth/login", data={"username": fx.email, "password": "errada000"})
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_me_sem_token_retorna_401(client: TestClient) -> None:
    assert client.get("/me").status_code == 401


def test_token_carrega_capacidades(client: TestClient, make_org) -> None:
    fx = make_org(capacidades=["ver", "exportar"])
    token = login(client, fx.email, fx.senha)
    # Endpoint que exige 'administrar' deve negar (403) para quem não a possui.
    resp = client.get("/orgs", headers=auth_header(token))
    assert resp.status_code == 403
