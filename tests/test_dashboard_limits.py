"""Testes do Dashboard (Módulo 1) e do Monitor de Limites (Módulo 3) — Sprint 3."""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.modules.catalog.models import DimEnte
from app.modules.indicators import service as indicators_service
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega, SilverEnte, SilverRreo
from tests.conftest import auth_header, login

PERIODO = "2024-B6"


def _ente() -> str:
    return "6" + "".join(random.choices("0123456789", k=6))


@pytest.fixture
def limpar() -> Iterator[list[str]]:
    usados: list[str] = []
    yield usados
    with SessionLocal() as s:
        for cod in usados:
            s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
            s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
            s.execute(delete(SilverRreo).where(SilverRreo.cod_ibge == cod))
            s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
            s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
            s.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
        s.commit()


def _setup_pessoal(cod: str, esfera: str, valor_pessoal: int) -> None:
    """Ente com RREO (RCL=10.000.000) e mart_indicador de pessoal calculado."""
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente", uf="CE", esfera=esfera))
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


def test_limites_lista_faixa_e_distancia(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _setup_pessoal(cod, "M", 5_000_000)  # 50% da RCL ⇒ alerta (teto 54)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    body = client.get(
        f"/entes/{cod}/limites", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    item = next(i for i in body["itens"] if i["indicador"] == "pessoal_executivo")
    assert item["faixa"] == "alerta"
    assert float(item["valor_pct_rcl"]) == 50.0
    assert float(item["teto_pct"]) == 54.0
    assert float(item["distancia_teto"]) == 4.0  # 54 − 50


def test_limite_detail_providencias_da_faixa(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _setup_pessoal(cod, "M", 5_300_000)  # 53% ⇒ prudencial (entre 51,3 e 54)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    det = client.get(
        f"/entes/{cod}/limites/pessoal_executivo",
        params={"periodo": PERIODO}, headers=auth_header(token),
    ).json()
    assert det["faixa"] == "prudencial"
    # Providências aparecem só na faixa corrente.
    assert det["providencias"], "esperava providências para a faixa prudencial"
    assert {p["faixa"] for p in det["providencias"]} == {"prudencial"}
    assert det["memoria"]["formula"].startswith("valor_pct_rcl")
    assert len(det["serie_historica"]) == 1
    assert [b["codigo"] for b in det["periodo_breadcrumb"]] == ["2024"]  # drill UP
    assert det["source_ref"]["anexo"] == "Anexo 03"


def test_simular_nao_persiste(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _setup_pessoal(cod, "M", 5_000_000)  # atual: alerta
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    sim = client.post(
        f"/entes/{cod}/limites/pessoal_executivo/simular",
        params={"periodo": PERIODO}, json={"novo_valor_rs": "5500000"},
        headers=auth_header(token),
    ).json()
    assert sim["faixa_atual"] == "alerta"
    assert sim["faixa_simulada"] == "excedido"  # 55% ≥ teto 54
    assert sim["persistido"] is False

    # Persistência intacta: o mart continua em 'alerta'.
    det = client.get(
        f"/entes/{cod}/limites/pessoal_executivo",
        params={"periodo": PERIODO}, headers=auth_header(token),
    ).json()
    assert det["faixa"] == "alerta"


def test_dashboard_semaforo_coerente(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _setup_pessoal(cod, "M", 5_500_000)  # 55% ⇒ excedido (vermelho)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    dash = client.get(
        f"/entes/{cod}/dashboard", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    semaforo = {s["indicador"]: s for s in dash["semaforo"]}
    assert semaforo["pessoal_executivo"]["faixa"] == "excedido"
    assert semaforo["pessoal_executivo"]["cor"] == "vermelho"
    # Indicadores sem dado ficam cinza.
    assert semaforo["divida_consolidada_liquida"]["cor"] == "cinza"
    assert dash["conformidade"] == "critico"
    assert any(d["indicador"] == "pessoal_executivo" for d in dash["destaques"])
    kpi = {k["chave"]: k for k in dash["kpis"]}
    assert float(kpi["rcl_12m"]["valor"]) == 10_000_000.0
    assert kpi["despesa_total"]["disponivel"] is False
