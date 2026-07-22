"""Testes da Sprint 1 — ingestão SICONFI e medallion bitemporal.

Critérios de aceite: rodar 2x não duplica; retificação supera a versão anterior e
mantém histórico; ``dim_entrega.vigente`` correto; consulta ``as_of`` retorna versão
histórica; MSC de 1 ente-ano ingere rápido. Rede não é tocada (cliente falso).
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from time import perf_counter
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.main import app
from app.modules.ingestion.models import (
    DimEntrega,
    IngestionLog,
    RawPayload,
    SilverMsc,
    SilverRreo,
)
from app.modules.ingestion.router import get_client_resolver
from tests.conftest import auth_header, login

RREO_PATH = "tt/rreo"
MSC_PATH = "tt/msc_patrimonial"


class FakeRecordsClient:
    """Cliente/resolver falso: retorna registros configurados por path (sem rede).

    Serve como ``RecordsClient`` (``get_records``) e como ``ClientResolver`` (``get``
    retorna a si mesmo), simplificando o override da dependência nos testes.
    """

    def __init__(self) -> None:
        self.records: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, fonte: str) -> FakeRecordsClient:
        return self

    def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((path, dict(params)))
        return list(self.records.get(path, []))


def _rreo_items(v_correntes: str, v_impostos: str) -> list[dict[str, Any]]:
    return [
        {"no_anexo": "RREO-Anexo 01", "conta": "Receitas Correntes",
         "coluna": "Até o Bimestre", "valor": v_correntes},
        {"no_anexo": "RREO-Anexo 01", "conta": "Impostos",
         "coluna": "Até o Bimestre", "valor": v_impostos},
    ]


def _ente() -> str:
    return "9" + "".join(random.choices("0123456789", k=6))


@pytest.fixture
def fake_client() -> Iterator[FakeRecordsClient]:
    fake = FakeRecordsClient()
    app.dependency_overrides[get_client_resolver] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_client_resolver, None)


@pytest.fixture
def entes_cleanup() -> Iterator[list[str]]:
    used: list[str] = []
    yield used
    with SessionLocal() as s:
        for cod in used:
            for model in (RawPayload, DimEntrega, SilverRreo, SilverMsc, IngestionLog):
                s.execute(delete(model).where(model.cod_ibge == cod))
        s.commit()


def _admin_token(client: TestClient, make_org) -> str:
    fx = make_org()  # caps padrão incluem 'administrar'
    return login(client, fx.email, fx.senha)


def _count(model: type, ente: str) -> int:
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(model).where(model.cod_ibge == ente)) or 0


def _run(client: TestClient, token: str, body: dict[str, Any]) -> dict[str, Any]:
    resp = client.post("/admin/ingestion/run", json=body, headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_rodar_2x_nao_duplica(client, make_org, fake_client, entes_cleanup) -> None:
    ente = _ente()
    entes_cleanup.append(ente)
    fake_client.records[RREO_PATH] = _rreo_items("1000.00", "700.00")
    token = _admin_token(client, make_org)
    body = {
        "fonte": "siconfi_rreo", "entes": [ente], "anos": [2024],
        "periodos": [6], "versao": "1", "homologada_em": "2025-01-10T00:00:00Z",
    }

    r1 = client.post("/admin/ingestion/run", json=body, headers=auth_header(token))
    assert r1.status_code == 200, r1.text
    assert r1.json()["ingeridos"] == 1
    assert r1.json()["silver_rows"] == 2

    r2 = client.post("/admin/ingestion/run", json=body, headers=auth_header(token))
    assert r2.status_code == 200
    assert r2.json()["ingeridos"] == 0
    assert r2.json()["pulados"] == 1

    # Bronze não duplica (1 versão) e silver mantém apenas as 2 linhas da versão.
    assert _count(RawPayload, ente) == 1
    assert _count(SilverRreo, ente) == 2


def test_retificacao_supera_versao_e_mantem_historico(
    client, make_org, fake_client, entes_cleanup
) -> None:
    ente = _ente()
    entes_cleanup.append(ente)
    token = _admin_token(client, make_org)

    # v1 homologada antes; v2 (retificação) homologada depois, com valores diferentes.
    fake_client.records[RREO_PATH] = _rreo_items("1000.00", "700.00")
    _run(client, token, {
        "fonte": "siconfi_rreo", "entes": [ente], "anos": [2024],
        "periodos": [6], "versao": "1", "homologada_em": "2025-01-10T00:00:00Z"})

    fake_client.records[RREO_PATH] = _rreo_items("1100.00", "750.00")
    _run(client, token, {
        "fonte": "siconfi_rreo", "entes": [ente], "anos": [2024],
        "periodos": [6], "versao": "2", "homologada_em": "2025-03-15T00:00:00Z"})

    # Duas entregas; a v2 é vigente, a v1 deixou de ser (sem apagar).
    with SessionLocal() as s:
        entregas = list(
            s.scalars(
                select(DimEntrega)
                .where(DimEntrega.cod_ibge == ente, DimEntrega.relatorio == "RREO")
                .order_by(DimEntrega.versao_entrega)
            )
        )
    assert [(e.versao_entrega, e.vigente) for e in entregas] == [("1", False), ("2", True)]

    # Bronze com 2 versões; silver mantém histórico (2 linhas por versão).
    assert _count(RawPayload, ente) == 2
    assert _count(SilverRreo, ente) == 4


def test_as_of_retorna_versao_historica(client, make_org, fake_client, entes_cleanup) -> None:
    ente = _ente()
    entes_cleanup.append(ente)
    token = _admin_token(client, make_org)

    fake_client.records[RREO_PATH] = _rreo_items("1000.00", "700.00")
    client.post("/admin/ingestion/run", headers=auth_header(token), json={
        "fonte": "siconfi_rreo", "entes": [ente], "anos": [2024],
        "periodos": [6], "versao": "1", "homologada_em": "2025-01-10T00:00:00Z"})
    fake_client.records[RREO_PATH] = _rreo_items("1100.00", "750.00")
    client.post("/admin/ingestion/run", headers=auth_header(token), json={
        "fonte": "siconfi_rreo", "entes": [ente], "anos": [2024],
        "periodos": [6], "versao": "2", "homologada_em": "2025-03-15T00:00:00Z"})

    common = {"fonte": "siconfi_rreo", "ente": ente, "periodo": "2024-B6"}

    # Sem as_of ⇒ versão vigente (v2).
    vigente = client.get("/admin/ingestion/data", params=common, headers=auth_header(token)).json()
    assert vigente["versao_entrega"] == "2"
    assert sorted(float(r["valor"]) for r in vigente["rows"]) == [750.0, 1100.0]
    assert vigente["source_ref"]["relatorio"] == "RREO"

    # as_of entre as homologações ⇒ versão histórica (v1).
    hist = client.get(
        "/admin/ingestion/data",
        params={**common, "as_of": "2025-02-01T00:00:00Z"},
        headers=auth_header(token),
    ).json()
    assert hist["versao_entrega"] == "1"
    assert sorted(float(r["valor"]) for r in hist["rows"]) == [700.0, 1000.0]


def test_replay_reprocessa_do_bronze(client, make_org, fake_client, entes_cleanup) -> None:
    ente = _ente()
    entes_cleanup.append(ente)
    token = _admin_token(client, make_org)
    fake_client.records[RREO_PATH] = _rreo_items("1000.00", "700.00")
    client.post("/admin/ingestion/run", headers=auth_header(token), json={
        "fonte": "siconfi_rreo", "entes": [ente], "anos": [2024], "periodos": [6], "versao": "1"})

    # Apaga o silver e reprocessa a partir do bronze (sem rede).
    with SessionLocal() as s:
        s.execute(delete(SilverRreo).where(SilverRreo.cod_ibge == ente))
        s.commit()
    assert _count(SilverRreo, ente) == 0

    r = client.post(
        "/admin/ingestion/replay",
        params={"ente": ente, "periodo": "2024-B6", "fonte": "siconfi_rreo"},
        headers=auth_header(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["silver_rows"] == 2
    assert _count(SilverRreo, ente) == 2


def test_msc_um_ente_ano_rapido(client, make_org, fake_client, entes_cleanup) -> None:
    ente = _ente()
    entes_cleanup.append(ente)
    token = _admin_token(client, make_org)
    fake_client.records[MSC_PATH] = [
        {
            "conta_contabil": f"1.1.{i:04d}",
            "valor_saldo_inicial": "0",
            "valor_movimento_devedor": "10",
            "valor_movimento_credor": "5",
            "valor_saldo_final": "5",
        }
        for i in range(2000)
    ]
    body = {"fonte": "siconfi_msc", "entes": [ente], "anos": [2024], "periodos": [1], "versao": "1"}

    t0 = perf_counter()
    r = client.post("/admin/ingestion/run", json=body, headers=auth_header(token))
    elapsed = perf_counter() - t0

    assert r.status_code == 200, r.text
    assert r.json()["silver_rows"] == 2000
    assert _count(SilverMsc, ente) == 2000
    assert elapsed < 300  # << 5 min


def test_run_exige_capacidade_administrar(client, make_org, fake_client, entes_cleanup) -> None:
    ente = _ente()
    entes_cleanup.append(ente)
    fake_client.records[RREO_PATH] = _rreo_items("1", "1")
    fx = make_org(capacidades=["ver"])  # sem 'administrar'
    token = login(client, fx.email, fx.senha)
    body = {"fonte": "siconfi_rreo", "entes": [ente], "anos": [2024], "periodos": [6]}
    r = client.post("/admin/ingestion/run", json=body, headers=auth_header(token))
    assert r.status_code == 403
