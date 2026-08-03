"""Contrato HTTP da procedência: rota, autorização e forma da resposta.

A declaração já é conferida contra os conectores (`test_procedencia`) e os exemplos contra
as fontes reais (`test_procedencia_rede`). Falta o que liga as duas pontas: a rota existe,
exige a capacidade certa e devolve o que a tela consome.
"""

from __future__ import annotations

from tests.conftest import auth_header, login

ROTA = "/admin/ingestion/fontes/{fonte}/procedencia"


def test_procedencia_traz_endpoints_com_parametros_explicados(client, make_org) -> None:
    fx = make_org()
    token = login(client, fx.email, fx.senha)

    r = client.get(ROTA.format(fonte="siconfi_rgf"), headers=auth_header(token))
    assert r.status_code == 200, r.text
    corpo = r.json()

    assert corpo["acesso_rotulo"] == "API REST (JSON)"
    assert corpo["endpoints"], "fonte sem nenhuma chamada declarada"
    endpoint = corpo["endpoints"][0]
    assert endpoint["url"].startswith("https://apidatalake.tesouro.gov.br/")
    assert endpoint["exemplo"] and endpoint["exemplo"].startswith("https://")
    # O parâmetro que explica a forma da ingestão do RGF: uma chamada por poder.
    poder = next(p for p in endpoint["parametros"] if p["nome"] == "co_poder")
    assert "Executivo" in poder["significado"]


def test_catalogo_expoe_o_tipo_de_acesso_para_o_selo_da_tabela(client, make_org) -> None:
    fx = make_org()
    token = login(client, fx.email, fx.senha)
    r = client.get("/admin/ingestion/fontes", headers=auth_header(token))
    assert r.status_code == 200, r.text
    por_fonte = {row["fonte"]: row for row in r.json()}
    assert por_fonte["siconfi_rreo"]["tipo_acesso"] == "api_rest"
    # A CAPAG não tem endereço fixo de arquivo: sai do catálogo CKAN a cada publicação.
    assert por_fonte["tesouro_capag"]["tipo_acesso"] == "catalogo_ckan"


def test_fonte_inexistente_devolve_404_e_nao_500(client, make_org) -> None:
    fx = make_org()
    token = login(client, fx.email, fx.senha)
    r = client.get(ROTA.format(fonte="fonte_que_nao_existe"), headers=auth_header(token))
    assert r.status_code == 404, r.text


def test_procedencia_exige_autenticacao(client) -> None:
    """Sem sessão não se lê a origem — é rota administrativa como o resto da Central."""
    r = client.get(ROTA.format(fonte="siconfi_rgf"))
    assert r.status_code in (401, 403), r.text
