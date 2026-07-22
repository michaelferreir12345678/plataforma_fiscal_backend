"""Testes da Sprint 1B — arquivos de FPM/CAPAG e integração SIOPS.

Cobrem: parse de XLSX, explosão por ente, checksum como versão + idempotência,
falha explícita do CAPAG em layout inválido e SIOPS em long format. Sem rede.
"""

from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.main import app
from app.modules.ingestion.models import (
    DimEntrega,
    IngestionLog,
    RawPayload,
    SiopsSaude,
    TesouroCapag,
    TesouroFpm,
)
from app.modules.ingestion.router import get_client_resolver
from tests.conftest import auth_header, login

TEST_FPM_YEAR = 2097
TEST_CAPAG_YEAR = 2098
TEST_SIOPS_YEAR = 2099
TEST_FPM_CODS = ("9900001", "9900002")
TEST_CAPAG_COD = "9900003"
TEST_SIOPS_COD = "9900004"
TEST_JOBS = (
    ("tesouro_fpm", "FPM", f"{TEST_FPM_YEAR}-M03"),
    ("tesouro_capag", "CAPAG", str(TEST_CAPAG_YEAR)),
    ("siops_saude", "SIOPS", f"{TEST_SIOPS_YEAR}-B3"),
)


def make_xlsx(headers: list[str], rows: list[list[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_capag_oficial_xlsx(headers: list[str], rows: list[list[Any]]) -> bytes:
    """Fixture mínima do arquivo oficial: aba fixa e cabeçalho na terceira linha."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Prévia da CAPAG"
    ws.append(["Prévia da CAPAG dos Municípios"])
    ws.append(["Dados publicados pelo Tesouro Nacional"])
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


class FakeFileClient:
    """Cliente/resolver falso de arquivos (sem rede)."""

    def __init__(self) -> None:
        self.content = b""
        self.records: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def get(self, fonte: str) -> FakeFileClient:
        return self

    def fetch(self, params: dict[str, Any]) -> bytes:
        self.calls.append(dict(params))
        return self.content

    def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append({"path": path, **params})
        return self.records


@pytest.fixture
def fake_file() -> Iterator[FakeFileClient]:
    fake = FakeFileClient()
    app.dependency_overrides[get_client_resolver] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_client_resolver, None)


@pytest.fixture(autouse=True)
def _cleanup_lake() -> Iterator[None]:
    _cleanup_test_records()
    try:
        yield
    finally:
        _cleanup_test_records()


def _cleanup_test_records() -> None:
    """Remove somente os registros reservados para as fixtures deste módulo."""
    with SessionLocal() as s:
        s.execute(
            delete(TesouroFpm).where(
                TesouroFpm.ano == TEST_FPM_YEAR,
                TesouroFpm.cod_ibge.in_(TEST_FPM_CODS),
            )
        )
        s.execute(
            delete(TesouroCapag).where(
                TesouroCapag.ano_ref == TEST_CAPAG_YEAR,
                TesouroCapag.cod_ibge == TEST_CAPAG_COD,
            )
        )
        s.execute(
            delete(SiopsSaude).where(
                SiopsSaude.ano == TEST_SIOPS_YEAR,
                SiopsSaude.cod_ibge == TEST_SIOPS_COD,
            )
        )
        for fonte, relatorio, periodo in TEST_JOBS:
            s.execute(
                delete(RawPayload).where(
                    RawPayload.fonte == fonte,
                    RawPayload.cod_ibge == "BR",
                    RawPayload.periodo == periodo,
                )
            )
            s.execute(
                delete(IngestionLog).where(
                    IngestionLog.fonte == fonte,
                    IngestionLog.cod_ibge == "BR",
                    IngestionLog.periodo == periodo,
                )
            )
            s.execute(
                delete(DimEntrega).where(
                    DimEntrega.cod_ibge == "BR",
                    DimEntrega.relatorio == relatorio,
                    DimEntrega.periodo == periodo,
                )
            )
        s.commit()


def _admin_token(client: TestClient, make_org) -> str:
    fx = make_org()
    return login(client, fx.email, fx.senha)


def _run(client: TestClient, token: str, body: dict[str, Any]) -> dict[str, Any]:
    resp = client.post("/admin/ingestion/run", json=body, headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _count(model: type, *conditions: Any) -> int:
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def _count_bronze(fonte: str, periodo: str, versao: str) -> int:
    """Conta o bronze de UMA fonte (o ``raw_payload`` é partilhado — não contar global)."""
    with SessionLocal() as s:
        return (
            s.scalar(
                select(func.count())
                .select_from(RawPayload)
                .where(
                    RawPayload.fonte == fonte,
                    RawPayload.cod_ibge == "BR",
                    RawPayload.periodo == periodo,
                    RawPayload.versao == versao,
                )
            )
            or 0
        )


def test_fpm_explode_por_ente_checksum_e_idempotente(client, make_org, fake_file) -> None:
    fake_file.content = make_xlsx(
        ["cod_ibge", "valor_bruto", "deducoes", "valor_liquido"],
        [
            [TEST_FPM_CODS[0], "1000.50", "100.00", "900.50"],
            [TEST_FPM_CODS[1], "2000", "0", "2000"],
        ],
    )
    token = _admin_token(client, make_org)
    body = {"fonte": "tesouro_fpm", "anos": [TEST_FPM_YEAR], "periodos": [3]}

    res = _run(client, token, body)
    assert res["silver_rows"] == 2
    versao = res["versoes_vigentes"][0]
    assert len(versao) == 16 and all(c in "0123456789abcdef" for c in versao)  # checksum

    with SessionLocal() as s:
        linhas = list(
            s.scalars(
                select(TesouroFpm).where(
                    TesouroFpm.ano == TEST_FPM_YEAR,
                    TesouroFpm.mes == 3,
                    TesouroFpm.versao_entrega == versao,
                )
            )
        )
    assert {r.cod_ibge for r in linhas} == set(TEST_FPM_CODS)
    assert float(next(r.valor_liquido for r in linhas if r.cod_ibge == TEST_FPM_CODS[0])) == 900.50

    # Mesmo arquivo (mesmo checksum) ⇒ não duplica.
    res2 = _run(client, token, body)
    assert res2["pulados"] == 1
    assert (
        _count(
            TesouroFpm,
            TesouroFpm.ano == TEST_FPM_YEAR,
            TesouroFpm.mes == 3,
            TesouroFpm.versao_entrega == versao,
        )
        == 2
    )
    assert _count_bronze("tesouro_fpm", f"{TEST_FPM_YEAR}-M03", versao) == 1


def test_capag_falha_explicita_em_layout_invalido(client, make_org, fake_file) -> None:
    # Faltam colunas obrigatórias (ind_endividamento, ind_poupanca, ind_liquidez).
    fake_file.content = make_xlsx(["cod_ibge", "nota"], [[TEST_CAPAG_COD, "B"]])
    token = _admin_token(client, make_org)
    resp = client.post(
        "/admin/ingestion/run",
        json={"fonte": "tesouro_capag", "anos": [TEST_CAPAG_YEAR]},
        headers=auth_header(token),
    )
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert (
        _count(
            TesouroCapag,
            TesouroCapag.cod_ibge == TEST_CAPAG_COD,
            TesouroCapag.ano_ref == TEST_CAPAG_YEAR,
        )
        == 0
    )  # nada da fixture foi gravado (falhou explicitamente)


def test_capag_layout_valido_materializa(client, make_org, fake_file) -> None:
    fake_file.content = make_xlsx(
        ["cod_ibge", "nota_final", "ind_endividamento", "ind_poupanca", "ind_liquidez"],
        [[TEST_CAPAG_COD, "B", "0.55", "0.10", "1.20"]],
    )
    token = _admin_token(client, make_org)
    res = _run(client, token, {"fonte": "tesouro_capag", "anos": [TEST_CAPAG_YEAR]})
    assert res["silver_rows"] == 1
    versao = res["versoes_vigentes"][0]

    with SessionLocal() as s:
        row = s.scalar(
            select(TesouroCapag).where(
                TesouroCapag.cod_ibge == TEST_CAPAG_COD,
                TesouroCapag.ano_ref == TEST_CAPAG_YEAR,
                TesouroCapag.versao_entrega == versao,
            )
        )
    assert row is not None
    assert row.nota_final == "B"
    assert row.ano_ref == TEST_CAPAG_YEAR
    assert float(row.ind_endividamento) == 0.55


def test_capag_layout_oficial_linha_3_materializa(client, make_org, fake_file) -> None:
    fake_file.content = make_capag_oficial_xlsx(
        [
            "Código Município Completo",
            "Nome_Município",
            "UF",
            "CAPAG",
            "Indicador 1",
            "Nota 1",
            "Indicador 2",
            "Nota 2",
            "Indicador 3",
            "Nota 3",
            "Metodologia",
        ],
        [
            [
                int(TEST_CAPAG_COD),
                "Município Fixture",
                "ZZ",
                "B",
                0.55,
                "A",
                0.10,
                "A",
                1.20,
                "C",
                "STN-fixture",
            ]
        ],
    )
    token = _admin_token(client, make_org)
    res = _run(client, token, {"fonte": "tesouro_capag", "anos": [TEST_CAPAG_YEAR]})
    assert res["silver_rows"] == 1
    versao = res["versoes_vigentes"][0]

    with SessionLocal() as s:
        row = s.scalar(
            select(TesouroCapag).where(
                TesouroCapag.cod_ibge == TEST_CAPAG_COD,
                TesouroCapag.ano_ref == TEST_CAPAG_YEAR,
                TesouroCapag.versao_entrega == versao,
            )
        )
    assert row is not None
    assert row.nota_final == "B"
    assert float(row.ind_endividamento) == 0.55
    assert float(row.ind_poupanca) == 0.10
    assert float(row.ind_liquidez) == 1.20
    assert row.metodologia_versao == "STN-fixture"


def test_capag_layout_oficial_incompleto_falha_explicita(client, make_org, fake_file) -> None:
    fake_file.content = make_capag_oficial_xlsx(
        [
            "Código Município Completo",
            "Nome_Município",
            "UF",
            "CAPAG",
            "Indicador 1",
            "Indicador 2",
        ],
        [[int(TEST_CAPAG_COD), "Município Fixture", "ZZ", "B", 0.55, 0.10]],
    )
    token = _admin_token(client, make_org)
    resp = client.post(
        "/admin/ingestion/run",
        json={"fonte": "tesouro_capag", "anos": [TEST_CAPAG_YEAR]},
        headers=auth_header(token),
    )
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert "Indicador 3" in resp.json()["detail"]
    assert (
        _count(
            TesouroCapag,
            TesouroCapag.cod_ibge == TEST_CAPAG_COD,
            TesouroCapag.ano_ref == TEST_CAPAG_YEAR,
        )
        == 0
    )


def test_siops_long_format(client, make_org, fake_file) -> None:
    fake_file.records = [
        {"numero_indicador": "1.1", "indicador_calculado": "15,5 %"},
        {"numero_indicador": "2.1", "indicador_calculado": "300,0"},
    ]
    token = _admin_token(client, make_org)
    res = _run(
        client,
        token,
        {
            "fonte": "siops_saude",
            "entes": [TEST_SIOPS_COD],
            "anos": [TEST_SIOPS_YEAR],
            "periodos": [3],
        },
    )
    assert res["silver_rows"] == 2  # duas colunas de indicador viraram duas linhas
    versao = res["versoes_vigentes"][0]

    with SessionLocal() as s:
        linhas = list(
            s.scalars(
                select(SiopsSaude).where(
                    SiopsSaude.cod_ibge == TEST_SIOPS_COD,
                    SiopsSaude.ano == TEST_SIOPS_YEAR,
                    SiopsSaude.bimestre == 3,
                    SiopsSaude.versao_entrega == versao,
                )
            )
        )
    assert {r.indicador_codigo: float(r.valor) for r in linhas} == {"1.1": 15.5, "2.1": 300.0}
