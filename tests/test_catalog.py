"""Testes do catálogo (dim_ente conformada + dim_periodo hierárquica)."""

from __future__ import annotations

import random
from collections.abc import Iterator

import pytest
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.modules.catalog import service as catalog_service
from app.modules.catalog.models import DimEnte
from app.modules.ingestion.models import IbgePib, IbgePopulacao, SilverEnte
from tests.conftest import auth_header, login


def _ente() -> str:
    return "8" + "".join(random.choices("0123456789", k=6))


@pytest.fixture
def limpar_entes() -> Iterator[list[str]]:
    usados: list[str] = []
    yield usados
    with SessionLocal() as s:
        for cod in usados:
            for model in (DimEnte, SilverEnte, IbgePopulacao, IbgePib):
                s.execute(delete(model).where(model.cod_ibge == cod))
        s.commit()


def _seed_ente(cod: str, *, esfera: str = "M", nome: str = "Ente Exemplo", uf: str = "CE") -> None:
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome=nome, uf=uf, esfera=esfera))
        s.commit()


def _seed_pop(cod: str, ano_ref: int, populacao: int) -> None:
    with SessionLocal() as s:
        s.add(IbgePopulacao(cod_ibge=cod, ano_ref=ano_ref, populacao=populacao, versao_entrega="1"))
        s.commit()


def test_dim_ente_atualiza_com_novo_ano_ibge(limpar_entes) -> None:
    cod = _ente()
    limpar_entes.append(cod)
    _seed_ente(cod, esfera="M")
    _seed_pop(cod, 2022, 100_000)

    with SessionLocal() as s:
        ente = catalog_service.refresh_dim_ente(s, cod)
        s.commit()
    assert ente is not None
    assert ente.esfera == "municipal"
    assert ente.populacao == 100_000
    assert ente.pop_ano_ref == 2022

    # Chega um novo ano de estimativa do IBGE ⇒ dim_ente reflete o mais recente.
    _seed_pop(cod, 2023, 110_000)
    with SessionLocal() as s:
        ente = catalog_service.refresh_dim_ente(s, cod)
        s.commit()
    assert ente is not None
    assert ente.populacao == 110_000
    assert ente.pop_ano_ref == 2023


def test_get_ente_endpoint_valida_escopo(client, make_org, limpar_entes) -> None:
    cod = _ente()
    limpar_entes.append(cod)
    _seed_ente(cod, esfera="E", nome="Estado Exemplo")
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    ok = client.get(f"/entes/{cod}", headers=auth_header(token))
    assert ok.status_code == 200, ok.text
    assert ok.json()["esfera"] == "estadual"

    fora = client.get("/entes/9999999", headers=auth_header(token))
    assert fora.status_code == 403  # fora da carteira/escopo (§6.4)


def test_periodos_drill(client, make_org) -> None:
    fx = make_org(capacidades=["ver"])
    token = login(client, fx.email, fx.senha)

    raiz = client.get("/periodos", headers=auth_header(token)).json()
    assert raiz["node"] is None
    anos = {c["codigo"] for c in raiz["children"]}
    assert {"2023", "2024", "2025"} <= anos

    ano = client.get("/periodos", params={"node": "2024"}, headers=auth_header(token)).json()
    filhos = {c["codigo"] for c in ano["children"]}
    assert "2024-B6" in filhos and "2024-Q3" in filhos  # bimestres + quadrimestres

    bim = client.get("/periodos", params={"node": "2024-B6"}, headers=auth_header(token)).json()
    assert [b["codigo"] for b in bim["breadcrumb"]] == ["2024"]
    assert {c["codigo"] for c in bim["children"]} == {"2024-M11", "2024-M12"}
