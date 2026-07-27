"""Sprint 23 — Visão Estadual & Consolidação Territorial da UF.

Prova, com uma **2ª UF sintética** ('99') de dados controlados (genericidade) e com o
**Ceará real** já materializado:

- consolidado = Σnumerador/Σdenominador ≠ média dos percentuais municipais;
- cobertura honesta (n/total, ausentes, períodos mistos);
- o ente estadual nunca entra no consolidado dos municípios (rotulagem/contrato);
- drill §6.1 UF→região→município preserva o período e linka o cockpit;
- escopo: conta estadual vê a UF; consultoria/prefeitura de outra UF ⇒ 403.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, text

from app.core.db import admin_session
from app.modules.catalog.models import DimEnte
from app.modules.dashboard.estadual_models import DimRegiaoUf, MartConsolidadoUf
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega

from .conftest import auth_header, login

UF = "99"
M1, M2, M3 = "9900001", "9900002", "9900003"  # municípios sintéticos
PERIODO = "2024-B6"
_VERSAO = "1"


def _entrega(cod: str, periodo: str) -> DimEntrega:
    return DimEntrega(
        cod_ibge=cod,
        relatorio="RREO",
        periodo=periodo,
        versao_entrega=_VERSAO,
        homologada_em=datetime(2025, 1, 15, tzinfo=UTC),
        vigente=True,
    )


@pytest.fixture
def uf_sintetica():
    """Semeia a UF '99': 3 municípios (2 com dado, 1 sem) + o ente estadual '99'.

    Números escolhidos para que o consolidado (Σnum/Σden, ponderado pela RCL) **difira** da
    média simples dos percentuais:
      - M1: RCL 1.000, pessoal 500  ⇒ 50%
      - M2: RCL 3.000, pessoal 600  ⇒ 20%
      consolidado = 1.100/4.000 = 27,5%   ;   média simples = (50+20)/2 = 35%
    """
    with admin_session() as s:
        s.add_all(
            [
                DimEnte(
                    cod_ibge=UF, nome="Estado 99", esfera="estadual", uf="99", populacao=5_000_000
                ),
                DimEnte(
                    cod_ibge=M1, nome="Municipio Um", esfera="municipal", uf="99", populacao=40_000
                ),
                DimEnte(
                    cod_ibge=M2,
                    nome="Municipio Dois",
                    esfera="municipal",
                    uf="99",
                    populacao=300_000,
                ),
                DimEnte(
                    cod_ibge=M3,
                    nome="Municipio Tres",
                    esfera="municipal",
                    uf="99",
                    populacao=20_000,
                ),
                DimRegiaoUf(
                    uf=UF, regiao_codigo="990001", nome="Região Alfa", municipios=[M1, M2, M3]
                ),
                # Entregas vigentes (inclui o ente estadual: a exclusão é estrutural).
                _entrega(UF, PERIODO),
                _entrega(M1, PERIODO),
                _entrega(M2, PERIODO),
                _entrega(M3, PERIODO),
                _entrega(M1, "2024-B4"),  # período extra p/ acionar "períodos mistos"
                # RCL (denominador) dos municípios com dado + do estado.
                FatoRcl(
                    cod_ibge=M1, periodo_ref=PERIODO, rcl_12m=Decimal(1000), versao_entrega=_VERSAO
                ),
                FatoRcl(
                    cod_ibge=M2, periodo_ref=PERIODO, rcl_12m=Decimal(3000), versao_entrega=_VERSAO
                ),
                FatoRcl(
                    cod_ibge=UF, periodo_ref=PERIODO, rcl_12m=Decimal(90000), versao_entrega=_VERSAO
                ),
                # Pessoal (numerador).
                MartIndicador(
                    cod_ibge=M1,
                    periodo=PERIODO,
                    indicador="pessoal_executivo",
                    valor_rs=Decimal(500),
                    valor_pct_rcl=Decimal(50),
                    versao_entrega=_VERSAO,
                ),
                MartIndicador(
                    cod_ibge=M2,
                    periodo=PERIODO,
                    indicador="pessoal_executivo",
                    valor_rs=Decimal(600),
                    valor_pct_rcl=Decimal(20),
                    versao_entrega=_VERSAO,
                ),
                MartIndicador(
                    cod_ibge=M1,
                    periodo="2024-B4",
                    indicador="pessoal_executivo",
                    valor_rs=Decimal(480),
                    valor_pct_rcl=Decimal(48),
                    versao_entrega=_VERSAO,
                ),
                # O estado tem número próprio — que NÃO pode entrar no consolidado.
                MartIndicador(
                    cod_ibge=UF,
                    periodo=PERIODO,
                    indicador="pessoal_executivo",
                    valor_rs=Decimal(40000),
                    valor_pct_rcl=Decimal("44.4"),
                    versao_entrega=_VERSAO,
                ),
            ]
        )
        s.commit()
    yield
    with admin_session() as s:
        _pref = text("substr(cod_ibge,1,2) = '99'")
        for tbl in (MartIndicador, FatoRcl, DimEntrega, DimEnte):
            s.execute(delete(tbl).where(_pref))
        s.execute(delete(MartConsolidadoUf).where(MartConsolidadoUf.uf == UF))
        s.execute(delete(DimRegiaoUf).where(DimRegiaoUf.uf == UF))
        s.commit()


def _num(v) -> float:
    return float(v) if v is not None else None


# --- Consolidação ≠ média de % (genérico para qualquer UF) ---
def test_consolidado_nao_e_media_de_percentuais(client, make_org, uf_sintetica):
    org = make_org(tipo_conta="estado", entes=[UF])
    tok = login(client, org.email, org.senha)
    r = client.get(f"/uf/{UF}/consolidado", params={"periodo": PERIODO}, headers=auth_header(tok))
    assert r.status_code == 200, r.text
    body = r.json()

    pessoal = next(i for i in body["indicadores"] if i["indicador"] == "pessoal_executivo")
    # Σnum/Σden = 1100/4000 = 27,5% — e NÃO a média (50+20)/2 = 35%.
    assert _num(pessoal["numerador"]) == 1100.0
    assert _num(pessoal["denominador"]) == 4000.0
    assert abs(_num(pessoal["valor_pct"]) - 27.5) < 1e-6
    assert abs(_num(pessoal["valor_pct"]) - 35.0) > 1.0  # difere da média simples


def test_cobertura_honesta_lista_ausentes_e_periodo_misto(client, make_org, uf_sintetica):
    org = make_org(tipo_conta="estado", entes=[UF])
    tok = login(client, org.email, org.senha)
    body = client.get(
        f"/uf/{UF}/consolidado", params={"periodo": PERIODO}, headers=auth_header(tok)
    ).json()

    pessoal = next(i for i in body["indicadores"] if i["indicador"] == "pessoal_executivo")
    assert pessoal["n_entes_total"] == 3
    assert pessoal["n_entes_com_dado"] == 2
    assert M3 in pessoal["entes_ausentes"]  # o município sem dado aparece nominalmente
    # M1 tem dado em B6 e B4 no mesmo ano ⇒ períodos mistos.
    assert pessoal["periodos_mistos"] is True
    assert body["n_municipios"] == 3


def test_estado_nunca_entra_no_consolidado(client, make_org, uf_sintetica):
    """O ente estadual '99' tem RCL 90.000 e pessoal 40.000 — se entrasse, o total explodiria."""
    org = make_org(tipo_conta="estado", entes=[UF])
    tok = login(client, org.email, org.senha)
    body = client.get(
        f"/uf/{UF}/consolidado", params={"periodo": PERIODO}, headers=auth_header(tok)
    ).json()

    rcl = next(i for i in body["indicadores"] if i["indicador"] == "rcl")
    # Só os municípios: 1000 + 3000 = 4000 (NÃO 94000 com o estado).
    assert _num(rcl["numerador"]) == 4000.0
    assert body["ente_estadual"]["cod_ibge"] == UF  # o estado vem referenciado, à parte
    assert body["escopo"] == "municipios_consolidado"
    assert (
        "não entra no consolidado" in body["observacao"]
        or "não é contada em dobro" in body["observacao"]
    )


# --- Drill §6.1 UF→região→município→cockpit ---
def test_drill_regiao_municipio_preserva_periodo(client, make_org, uf_sintetica):
    org = make_org(tipo_conta="estado", entes=[UF])
    tok = login(client, org.email, org.senha)

    raiz = client.get(
        f"/uf/{UF}/arvore",
        params={"indicador": "pessoal_executivo", "periodo": PERIODO, "agrupar": "regiao"},
        headers=auth_header(tok),
    ).json()
    assert raiz["period"] == PERIODO
    assert raiz["node"] is None
    regioes = raiz["children"]
    assert any(c["codigo"] == "regiao:990001" for c in regioes)
    reg = next(c for c in regioes if c["codigo"] == "regiao:990001")
    assert reg["has_children"] is True

    filhos = client.get(
        f"/uf/{UF}/arvore",
        params={"indicador": "pessoal_executivo", "periodo": PERIODO, "node": "regiao:990001"},
        headers=auth_header(tok),
    ).json()
    assert filhos["period"] == PERIODO
    assert filhos["node"]["codigo"] == "regiao:990001"
    cods = {c["codigo"] for c in filhos["children"]}
    assert {M1, M2, M3} <= cods
    m1 = next(c for c in filhos["children"] if c["codigo"] == M1)
    assert m1["measures"]["cockpit"] == f"/entes/{M1}/cockpit"  # folha linka o cockpit

    # Drill até o município: o breadcrumb sobe até a região (drill UP), preservando o período.
    folha = client.get(
        f"/uf/{UF}/arvore",
        params={"indicador": "pessoal_executivo", "periodo": PERIODO, "node": M1},
        headers=auth_header(tok),
    ).json()
    assert folha["period"] == PERIODO
    assert folha["node"]["codigo"] == M1
    assert any(b["codigo"] == "regiao:990001" for b in folha["breadcrumb"])


# --- Escopo: estadual × consultoria × prefeitura de outra UF ---
def test_escopo_consultoria_ve_so_carteira_no_ranking(client, make_org, uf_sintetica):
    # Consultoria com apenas M1 na carteira: o ranking traz só M1 (não M2).
    org = make_org(tipo_conta="consultoria", entes=[M1])
    tok = login(client, org.email, org.senha)
    body = client.get(
        f"/uf/{UF}/ranking",
        params={"indicador": "pessoal_executivo", "periodo": PERIODO},
        headers=auth_header(tok),
    ).json()
    cods = {i["cod_ibge"] for i in body["itens"]}
    assert cods == {M1}
    assert M2 not in cods


def test_escopo_estadual_ve_todos_os_municipios(client, make_org, uf_sintetica):
    org = make_org(tipo_conta="estado", entes=[UF])
    tok = login(client, org.email, org.senha)
    body = client.get(
        f"/uf/{UF}/ranking",
        params={"indicador": "pessoal_executivo", "periodo": PERIODO},
        headers=auth_header(tok),
    ).json()
    cods = {i["cod_ibge"] for i in body["itens"]}
    assert {M1, M2, M3} <= cods  # a conta estadual enxerga todos os municípios da UF


def test_escopo_outra_uf_403(client, make_org, uf_sintetica):
    # Consultoria com ente da UF '99' não pode ver o consolidado do Ceará (23).
    org = make_org(tipo_conta="consultoria", entes=[M1])
    tok = login(client, org.email, org.senha)
    r = client.get("/uf/23/consolidado", params={"periodo": PERIODO}, headers=auth_header(tok))
    assert r.status_code == 403, r.text


def test_mapa_referencia_a_malha_e_todos_os_poligonos(client, make_org, uf_sintetica):
    org = make_org(tipo_conta="estado", entes=[UF])
    tok = login(client, org.email, org.senha)
    body = client.get(
        f"/uf/{UF}/mapa",
        params={"indicador": "pessoal_executivo", "periodo": PERIODO},
        headers=auth_header(tok),
    ).json()
    assert body["malha_ref"] == f"/geo/malha/{UF}"
    # o mapa cobre todos os municípios (mesmo os sem dado, em cinza)
    assert {e["cod_ibge"] for e in body["entes"]} == {M1, M2, M3}


# --- Ceará real (âncora) ---
def test_ceara_real_consolidado_e_malha(client, make_org):
    org = make_org(tipo_conta="estado", entes=["23"])
    tok = login(client, org.email, org.senha)

    cons = client.get("/uf/23/consolidado", params={"periodo": "2024-B6"}, headers=auth_header(tok))
    assert cons.status_code == 200, cons.text
    body = cons.json()
    assert body["uf"] == "23"
    assert body["uf_nome"] == "Ceará"
    assert body["ente_estadual"]["cod_ibge"] == "23"
    assert body["n_municipios"] == 184  # os 184 municípios do CE
    pessoal = next(i for i in body["indicadores"] if i["indicador"] == "pessoal_executivo")
    assert pessoal["valor_pct"] is not None
    assert pessoal["n_entes_com_dado"] < 184  # cobertura honesta (< 100%)

    # a malha real do IBGE tem os 184 polígonos
    malha = client.get("/geo/malha/23", headers=auth_header(tok))
    assert malha.status_code == 200, malha.text
    mb = malha.json()
    assert mb["n_areas"] == 184
    assert mb["malha"]["type"] == "FeatureCollection"
    assert len(mb["malha"]["features"]) == 184
