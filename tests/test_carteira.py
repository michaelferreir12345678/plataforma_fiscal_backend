"""Testes da visão agregada de carteira / visão estadual (Módulo 2) — Sprint 4.

Cobre os critérios de aceite: consolidação respeita escopo; estado vê todos os
municípios da UF; consultoria vê só sua carteira; drill ente↔carteira coerente;
ranking por risco; mapa; ações em lote.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.modules.catalog import repository as catalog_repo
from app.modules.catalog.models import DimEnte
from app.modules.dashboard import carteira_service
from app.modules.dashboard.models import MartCarteira
from app.modules.indicators import service as indicators_service
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega, SilverEnte, SilverRreo
from app.workers import carteira_tasks
from tests.conftest import auth_header, login

PERIODO = "2024-B6"


def _prefix() -> str:
    """Prefixo de UF (2 dígitos) aleatório para isolar os entes do teste."""
    return f"{random.randint(11, 53)}"


def _muni(prefix: str) -> str:
    return prefix + "".join(random.choices("0123456789", k=5))


@pytest.fixture
def limpar() -> Iterator[list[str]]:
    usados: list[str] = []
    yield usados
    with SessionLocal() as s:
        for cod in usados:
            s.execute(delete(MartCarteira).where(MartCarteira.cod_ibge == cod))
            s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
            s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
            s.execute(delete(SilverRreo).where(SilverRreo.cod_ibge == cod))
            s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
            s.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
            s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
        s.commit()


def _setup(
    cod: str,
    valor_pessoal: int,
    *,
    esfera: str = "M",
    uf: str = "CE",
    populacao: int | None = None,
    regiao: str | None = None,
    materializar: bool = True,
) -> None:
    """Ente com RREO (RCL=10.000.000), mart_indicador de pessoal e (opcional) mart_carteira."""
    with SessionLocal() as s:
        s.merge(
            SilverEnte(
                cod_ibge=cod, nome=f"Ente {cod}", uf=uf, esfera=esfera, populacao=populacao
            )
        )
        s.add(
            DimEntrega(
                cod_ibge=cod, relatorio="RREO", periodo=PERIODO, versao_entrega="1",
                homologada_em=datetime(2025, 1, 10, tzinfo=UTC), vigente=True,
            )
        )
        s.add(
            SilverRreo(
                cod_ibge=cod, periodo=PERIODO, anexo="RREO-Anexo 03",
                conta="Receitas Correntes (I)", coluna="12M",
                valor=Decimal("10000000"), versao_entrega="1",
            )
        )
        s.commit()
    with SessionLocal() as s:
        indicators_service.classificar_limite(
            s, cod, PERIODO, "pessoal_executivo", Decimal(valor_pessoal), poder="Executivo"
        )
        s.commit()
    if regiao is not None:
        with SessionLocal() as s:
            catalog_repo.upsert_dim_ente(s, cod_ibge=cod, valores={"regiao": regiao})
            s.commit()
    if materializar:
        with SessionLocal() as s:
            carteira_service.refresh_mart_carteira(s, cod, PERIODO)
            s.commit()


def test_estado_ve_municipios_da_uf_ranking_e_drill(client, make_org, limpar) -> None:
    prefix = _prefix()
    critico, alerta = _muni(prefix), _muni(prefix)
    limpar.extend([critico, alerta])
    _setup(critico, 5_500_000)  # 55% ⇒ excedido ⇒ crítico
    _setup(alerta, 5_000_000)  # 50% ⇒ alerta

    # Carteira estadual tem só o código da UF (2 dígitos); os municípios entram por expansão.
    fx = make_org(tipo_conta="estado", capacidades=["ver"], entes=[prefix])
    token = login(client, fx.email, fx.senha)

    body = client.get(
        "/carteira/entes", params={"periodo": PERIODO, "ordenar": "risco"},
        headers=auth_header(token),
    ).json()
    rows = {r["cod_ibge"]: r for r in body["data"]}
    # Estado enxerga ambos os municípios da UF, mesmo sem estarem na carteira_ente.
    assert critico in rows and alerta in rows
    # Ranking por risco: crítico antes do alerta.
    ordenados = [c for c in (r["cod_ibge"] for r in body["data"]) if c in {critico, alerta}]
    assert ordenados == [critico, alerta]
    assert rows[critico]["conformidade"] == "critico"
    assert rows[critico]["cor"] == "vermelho"
    assert rows[critico]["risco_score"] > rows[alerta]["risco_score"]

    # Drill DOWN: dashboard de um município fora da carteira_ente é acessível ao estado.
    dash = client.get(
        f"/entes/{critico}/dashboard", params={"periodo": PERIODO}, headers=auth_header(token)
    )
    assert dash.status_code == 200, dash.text
    semaforo = {s["indicador"]: s for s in dash.json()["semaforo"]}
    assert semaforo["pessoal_executivo"]["faixa"] == "excedido"


def test_consultoria_ve_so_carteira(client, make_org, limpar) -> None:
    prefix = _prefix()
    dentro, fora = _muni(prefix), _muni(prefix)
    limpar.extend([dentro, fora])
    _setup(dentro, 5_000_000)
    _setup(fora, 5_500_000)

    # Consultoria com só 'dentro' na carteira — sem ampliação por UF.
    fx = make_org(tipo_conta="consultoria", capacidades=["ver"], entes=[dentro])
    token = login(client, fx.email, fx.senha)

    body = client.get(
        "/carteira/entes", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    cods = {r["cod_ibge"] for r in body["data"]}
    assert dentro in cods
    assert fora not in cods  # mesmo sendo da mesma UF, consultoria não expande

    # Drill para um ente fora do escopo ⇒ 403.
    resp = client.get(
        f"/entes/{fora}/dashboard", params={"periodo": PERIODO}, headers=auth_header(token)
    )
    assert resp.status_code == 403


def test_resumo_consolidacao_respeita_escopo(client, make_org, limpar) -> None:
    prefix = _prefix()
    a, b = _muni(prefix), _muni(prefix)
    limpar.extend([a, b])
    _setup(a, 5_500_000)  # crítico
    _setup(b, 5_000_000)  # alerta

    fx = make_org(tipo_conta="consultoria", capacidades=["ver"], entes=[a, b])
    token = login(client, fx.email, fx.senha)

    body = client.get(
        "/carteira/resumo", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert body["total_entes"] == 2
    assert body["entes_com_dados"] == 2
    assert body["por_conformidade"]["critico"] == 1
    assert body["por_conformidade"]["alerta"] == 1
    pessoal = next(i for i in body["por_indicador"] if i["indicador"] == "pessoal_executivo")
    assert pessoal["total"] == 2
    assert pessoal["por_conformidade"]["critico"] == 1
    assert body["source_ref"]["relatorio"] == "RREO"


def test_mapa_cor_por_ente(client, make_org, limpar) -> None:
    prefix = _prefix()
    vermelho, verde = _muni(prefix), _muni(prefix)
    limpar.extend([vermelho, verde])
    _setup(vermelho, 5_500_000)  # excedido ⇒ vermelho
    _setup(verde, 4_000_000)  # 40% ⇒ normal ⇒ verde

    fx = make_org(tipo_conta="consultoria", capacidades=["ver"], entes=[vermelho, verde])
    token = login(client, fx.email, fx.senha)

    # Sem indicador ⇒ cor pela pior conformidade.
    body = client.get(
        "/carteira/mapa", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    mapa = {e["cod_ibge"]: e for e in body["entes"]}
    assert mapa[vermelho]["cor"] == "vermelho"
    assert mapa[verde]["cor"] == "verde"
    assert body["legenda"]["critico"] == "vermelho"

    # Com indicador ⇒ cor pela faixa daquele indicador.
    body2 = client.get(
        "/carteira/mapa", params={"periodo": PERIODO, "indicador": "pessoal_executivo"},
        headers=auth_header(token),
    ).json()
    mapa2 = {e["cod_ibge"]: e for e in body2["entes"]}
    assert mapa2[vermelho]["faixa"] == "excedido"
    assert mapa2[vermelho]["cor"] == "vermelho"
    assert mapa2[verde]["faixa"] == "normal"


def test_entes_filtro_por_porte(client, make_org, limpar) -> None:
    prefix = _prefix()
    pequeno, grande = _muni(prefix), _muni(prefix)
    limpar.extend([pequeno, grande])
    _setup(pequeno, 5_000_000, populacao=30_000)  # < 50k ⇒ pequeno
    _setup(grande, 5_000_000, populacao=300_000)  # 200k–1M ⇒ grande

    fx = make_org(tipo_conta="consultoria", capacidades=["ver"], entes=[pequeno, grande])
    token = login(client, fx.email, fx.senha)

    body = client.get(
        "/carteira/entes", params={"periodo": PERIODO, "porte": "pequeno"},
        headers=auth_header(token),
    ).json()
    cods = {r["cod_ibge"] for r in body["data"]}
    assert body["total"] == 1
    assert pequeno in cods
    assert grande not in cods


def test_refresh_enfileira_job_e_o_job_materializa_o_escopo(
    client, make_org, limpar
) -> None:
    """Sprint E1: o refresh deixou de percorrer o escopo dentro da requisição.

    O contrato mudou de ``200 {linhas_materializadas}`` para ``202 {job}``: para uma
    licença global o laço antigo tinha 5.598 iterações num handler HTTP. O que **não**
    mudou é o resultado — o job materializa exatamente o mesmo que o laço síncrono
    materializava, e é isto que este teste prende.
    """
    prefix = _prefix()
    a, b = _muni(prefix), _muni(prefix)
    limpar.extend([a, b])
    # mart_indicador existe, mas mart_carteira ainda não foi materializado.
    _setup(a, 5_500_000, materializar=False)
    _setup(b, 5_000_000, materializar=False)

    fx = make_org(tipo_conta="consultoria", capacidades=["ver", "administrar"], entes=[a, b])
    token = login(client, fx.email, fx.senha)

    # Antes do refresh: entes aparecem, mas sem dados de conformidade.
    antes = client.get(
        "/carteira/entes", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert {r["cod_ibge"]: r["conformidade"] for r in antes["data"]} == {
        a: "sem_dados", b: "sem_dados"
    }

    resp = client.post(
        "/carteira/refresh", params={"periodo": PERIODO}, headers=auth_header(token)
    )
    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert job["acao"] == "refresh"
    assert job["status"] == "enfileirado"
    assert job["periodo"] == PERIODO
    assert sorted(job["entes"]) == sorted([a, b])
    assert job["total_entes"] == 2

    # A requisição não materializou nada — quem materializa é o worker.
    ainda = client.get(
        "/carteira/entes", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert {r["conformidade"] for r in ainda["data"]} == {"sem_dados"}

    resumo = carteira_tasks.executar_pendentes()
    assert resumo["falhas"] == 0
    assert resumo["linhas"] >= 2, resumo

    depois = client.get(
        "/carteira/entes", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    conf = {r["cod_ibge"]: r["conformidade"] for r in depois["data"]}
    assert conf == {a: "critico", b: "alerta"}


def test_refresh_com_escopo_vazio_recusa_em_vez_de_enfileirar_nada(
    client, make_org
) -> None:
    """202 para um lote sem ente esconderia erro de cadastro atrás de um "aceito"."""
    fx = make_org(tipo_conta="consultoria", capacidades=["ver", "administrar"], entes=[])
    token = login(client, fx.email, fx.senha)
    resp = client.post(
        "/carteira/refresh", params={"periodo": PERIODO}, headers=auth_header(token)
    )
    assert resp.status_code == 422, resp.text


def test_lote_enfileira_e_valida_escopo(client, make_org, limpar) -> None:
    prefix = _prefix()
    a, b = _muni(prefix), _muni(prefix)
    limpar.extend([a, b])
    _setup(a, 5_000_000)
    _setup(b, 5_500_000)

    fx = make_org(
        tipo_conta="consultoria", capacidades=["ver", "gerar_relatorio"], entes=[a, b]
    )
    token = login(client, fx.email, fx.senha)

    # Enfileira relatório para todo o escopo.
    resp = client.post(
        "/carteira/lote/relatorio", json={"periodo": PERIODO}, headers=auth_header(token)
    )
    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert job["acao"] == "relatorio"
    assert job["status"] == "enfileirado"
    assert job["total_entes"] == 2
    assert set(job["entes"]) == {a, b}

    # Ente fora do escopo ⇒ 403.
    fora = _muni(_prefix())
    resp2 = client.post(
        "/carteira/lote/relatorio", json={"entes": [a, fora]}, headers=auth_header(token)
    )
    assert resp2.status_code == 403

    # Ação desconhecida ⇒ 404.
    resp3 = client.post("/carteira/lote/foobar", json={}, headers=auth_header(token))
    assert resp3.status_code == 404


def test_lote_exige_capacidade(client, make_org, limpar) -> None:
    prefix = _prefix()
    a = _muni(prefix)
    limpar.append(a)
    _setup(a, 5_000_000)

    # Só 'ver' — sem 'gerar_relatorio'.
    fx = make_org(tipo_conta="consultoria", capacidades=["ver"], entes=[a])
    token = login(client, fx.email, fx.senha)

    resp = client.post(
        "/carteira/lote/relatorio", json={"periodo": PERIODO}, headers=auth_header(token)
    )
    assert resp.status_code == 403
