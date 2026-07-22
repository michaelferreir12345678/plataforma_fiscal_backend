"""Testes de escopo multi-tenant (§6.4) — critério de aceite: fora de escopo ⇒ 403."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_header, login


def test_ente_dentro_do_escopo_retorna_200(client: TestClient, make_org) -> None:
    fx = make_org(
        tipo_conta="estado",
        capacidades=["ver"],
        entes=["2304400", "2307650"],
        escopo=["2304400"],
    )
    token = login(client, fx.email, fx.senha)
    resp = client.get("/carteira/consulta", params={"ente": "2304400"}, headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["cod_ibge"] == "2304400"


def test_ente_fora_do_escopo_retorna_403(client: TestClient, make_org) -> None:
    # Ente 2307650 está na carteira da org, mas fora do subconjunto do usuário.
    fx = make_org(
        tipo_conta="estado",
        capacidades=["ver"],
        entes=["2304400", "2307650"],
        escopo=["2304400"],
    )
    token = login(client, fx.email, fx.senha)
    resp = client.get("/carteira/consulta", params={"ente": "2307650"}, headers=auth_header(token))
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_ente_fora_da_carteira_retorna_403(client: TestClient, make_org) -> None:
    # Sem restrição de escopo, mas o ente não pertence à carteira da org.
    fx = make_org(tipo_conta="consultoria", capacidades=["ver"], entes=["2304400"])
    token = login(client, fx.email, fx.senha)
    resp = client.get("/carteira/consulta", params={"ente": "9999999"}, headers=auth_header(token))
    assert resp.status_code == 403
