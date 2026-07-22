"""Contratos unitarios das fontes oficiais de enriquecimento da Sprint 11."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.modules.ingestion import repository
from app.modules.ingestion.connectors.registry import CONNECTOR_REGISTRY
from app.modules.ingestion.connectors.siope import SiopeConnector
from app.modules.ingestion.connectors.siops import SiopsConnector
from app.modules.ingestion.connectors.transferencias import FundebConnector
from app.modules.ingestion.models import (
    FONTE_FUNDEB,
    FONTE_SIOPE,
    FONTE_SIOPS,
    FndeFundebRepasse,
    SiopeEducacao,
    SiopsSaude,
)
from app.shared.ingestion.client import JsonEnvelopeRecordsClient, ODataRecordsClient

FORTALEZA = "2304400"


class FakeRecordsClient:
    def __init__(self, responses: dict[str, list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((path, dict(params)))
        return self.responses.get(path, [])


def _capture_replacements(monkeypatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_replace(session, model, *, keys, rows):
        calls.append({"model": model, "keys": keys, "rows": rows})
        return len(rows)

    monkeypatch.setattr(repository, "replace_silver_rows", fake_replace)
    return calls


def test_registry_contem_as_tres_fontes_de_enriquecimento() -> None:
    assert CONNECTOR_REGISTRY[FONTE_SIOPS] is SiopsConnector
    assert CONNECTOR_REGISTRY[FONTE_SIOPE] is SiopeConnector
    assert CONNECTOR_REGISTRY[FONTE_FUNDEB] is FundebConnector


def test_siops_fortaleza_2024_mapeia_periodo_e_decimal_pt_br(monkeypatch) -> None:
    path = "indicador/municipal/230440/2024/2"
    client = FakeRecordsClient(
        {
            path: [
                {
                    "numero_indicador": "1.1",
                    "ds_indicador": "Participacao da receita de impostos",
                    "indicador_calculado": "24,06 %",
                },
                {
                    "numero_indicador": "2.1",
                    "ds_indicador": "Despesa total com saude por habitante",
                    "indicador_calculado": "1.234,50",
                },
            ]
        }
    )
    connector = SiopsConnector(client, object())
    job = connector.discover(
        {"entes": [FORTALEZA], "anos": [2024], "periodos": [6], "versao": "v1"}
    )[0]

    assert (job.cod_ibge, job.periodo, job.valid_time.isoformat()) == (
        "BR",
        "2024-B6",
        "2024-12-31",
    )
    payload = connector.extract(job)
    assert client.calls == [(path, {})]

    replacements = _capture_replacements(monkeypatch)
    assert connector.to_silver(None, job, payload, "v1") == 2
    assert replacements[0]["model"] is SiopsSaude
    assert replacements[0]["keys"] == {
        "cod_ibge": FORTALEZA,
        "ano": 2024,
        "bimestre": 6,
        "versao_entrega": "v1",
    }
    assert {row["indicador_codigo"]: row["valor"] for row in replacements[0]["rows"]} == {
        "1.1": Decimal("24.06"),
        "2.1": Decimal("1234.50"),
    }
    assert replacements[0]["rows"][0]["unidade"] == "percentual"
    assert replacements[0]["rows"][1]["unidade"] is None


def test_siops_aceita_ente_estadual() -> None:
    path = "indicador/estadual/23/2024/2"
    client = FakeRecordsClient({path: []})
    connector = SiopsConnector(client, object())
    job = connector.discover(
        {"entes": ["23"], "anos": [2024], "periodos": [6], "versao": "v1"}
    )[0]

    assert connector.extract(job) == []
    assert client.calls == [(path, {})]


def test_siope_fortaleza_2024_usa_odata_oficial_e_cod_indicador(monkeypatch) -> None:
    client = FakeRecordsClient(
        {
            "Indicadores_Siope(Ano_Consulta=2024,Num_Peri=6,Sig_UF='CE')": [
                {
                    "TIPO": "Municipal",
                    "NUM_ANO": 2024,
                    "NUM_PERI": 6,
                    "COD_MUNI": 230440,
                    "COD_INDI": 24,
                    "COD_EXIB": "1.1",
                    "NOM_INDI": "Percentual de aplicacao em MDE",
                    "VAL_INDI": "25.28",
                },
                {
                    "COD_INDI": 67,
                    "COD_EXIB": "4.2",
                    "NOM_INDI": "Percentual do FUNDEB aplicado em profissionais",
                    "VAL_INDI": "98.04",
                },
            ]
        }
    )
    connector = SiopeConnector(client, object())
    job = connector.discover(
        {"entes": [FORTALEZA], "anos": [2024], "periodos": [6], "versao": "v1"}
    )[0]

    payload = connector.extract(job)
    path, params = client.calls[0]
    assert path.startswith("Indicadores_Siope(")
    assert path == "Indicadores_Siope(Ano_Consulta=2024,Num_Peri=6,Sig_UF='CE')"
    assert params["$filter"] == "COD_MUNI eq 230440"

    replacements = _capture_replacements(monkeypatch)
    assert connector.to_silver(None, job, payload, "v1") == 2
    assert replacements[0]["model"] is SiopeEducacao
    assert replacements[0]["keys"]["cod_ibge"] == FORTALEZA
    assert {row["indicador_codigo"]: row["valor"] for row in replacements[0]["rows"]} == {
        "24": Decimal("25.28"),
        "67": Decimal("98.04"),
    }
    assert all(row["unidade"] == "percentual" for row in replacements[0]["rows"])


def test_siope_aceita_ente_estadual() -> None:
    client = FakeRecordsClient({})
    connector = SiopeConnector(client, object())
    job = connector.discover(
        {"entes": ["23"], "anos": [2024], "periodos": [6], "versao": "v1"}
    )[0]

    connector.extract(job)
    assert "Sig_UF='CE'" in client.calls[0][0]
    assert client.calls[0][1]["$filter"] == "TIPO eq 'Estadual'"


def test_fundeb_filtra_fortaleza_e_soma_repasse_com_ajuste(monkeypatch) -> None:
    client = FakeRecordsClient(
        {
            "por_estado_municipio": [
                {
                    "TRANSFERENCIA": "AJUSTE FUNDEB",
                    "CO_IBGE": 2304400,
                    "VALOR": "88032100.56",
                },
                {
                    "TRANSFERENCIA": "FUNDEB",
                    "CO_IBGE": 2304400,
                    "VALOR": "149412980.28",
                },
                {"TRANSFERENCIA": "FUNDEB", "CO_IBGE": 2312908, "VALOR": "10.00"},
            ]
        }
    )
    connector = FundebConnector(client, object())
    job = connector.discover(
        {"entes": [FORTALEZA], "anos": [2024], "periodos": [1], "versao": "v1"}
    )[0]

    payload = connector.extract(job)
    assert len(payload) == 2
    assert client.calls == [
        (
            "por_estado_municipio",
            {"p_ano": 2024, "p_mes": 1, "p_transferencia": "10:14", "p_estado": 6},
        )
    ]

    replacements = _capture_replacements(monkeypatch)
    assert connector.to_silver(None, job, payload, "v1") == 1
    assert replacements[0]["model"] is FndeFundebRepasse
    assert replacements[0]["keys"]["cod_ibge"] == FORTALEZA
    row = replacements[0]["rows"][0]
    assert row["valor_repassado"] == Decimal("237445080.84")
    assert row["complementacao_uniao"] is None


def test_substituicao_e_segmentada_por_ente_e_preserva_outros(monkeypatch) -> None:
    client = FakeRecordsClient({})
    connector = SiopeConnector(client, object())
    job = connector.discover(
        {
            "entes": [FORTALEZA, "2312908"],
            "anos": [2024],
            "periodos": [6],
            "versao": "v1",
        }
    )[0]
    payload = [
        {"_cod_ibge": FORTALEZA, "COD_INDI": 24, "VAL_INDI": "25.28"},
    ]
    replacements = _capture_replacements(monkeypatch)

    assert connector.to_silver(None, job, payload, "v1") == 1
    assert [call["keys"]["cod_ibge"] for call in replacements] == [FORTALEZA, "2312908"]
    assert len(replacements[0]["rows"]) == 1
    assert replacements[1]["rows"] == []


def test_clientes_tratam_envelopes_sem_rede(monkeypatch) -> None:
    odata = ODataRecordsClient("https://example.test/", page_size=2)
    odata_calls: list[dict[str, Any]] = []

    def fake_odata(path, params):
        odata_calls.append(dict(params))
        skip = params["$skip"]
        return {"value": [{"id": skip + 1}, {"id": skip + 2}]} if skip == 0 else {
            "value": [{"id": 3}]
        }

    monkeypatch.setattr(odata, "_get_page", fake_odata)
    assert odata.get_records("Indicadores", {"$filter": "x"}) == [
        {"id": 1},
        {"id": 2},
        {"id": 3},
    ]
    assert [call["$skip"] for call in odata_calls] == [0, 2]
    odata.close()

    encoded = ODataRecordsClient("https://example.test/")
    encoded_calls: list[tuple[str, dict[str, Any]]] = []

    def fake_encoded(path, params):
        encoded_calls.append((path, params))
        return {"value": []}

    monkeypatch.setattr(encoded, "_get_json", fake_encoded)
    assert encoded.get_records("Indicadores", {"$filter": "COD_MUNI eq 230440"}) == []
    assert "COD_MUNI%20eq%20230440" in encoded_calls[0][0]
    assert encoded_calls[0][1] is None
    encoded.close()

    envelope = JsonEnvelopeRecordsClient("https://example.test/")
    monkeypatch.setattr(envelope, "_get_json", lambda path, params: {"registros": [{"id": 1}]})
    assert envelope.get_records("por_estado_municipio", {}) == [{"id": 1}]
    envelope.close()
