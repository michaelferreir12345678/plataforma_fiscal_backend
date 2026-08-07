"""Testes da Sprint 1B — conectores complementares (SADIPEM, BCB/SGS, IBGE).

Reusa o framework da Sprint 1: idempotência, versão por data de captura, long format
(BCB), flatten dos agregados (IBGE) e mapeamento silver de cada fonte. Sem rede.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import date
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.main import app
from app.modules.catalog.models import DimEnte
from app.modules.dashboard.estadual_models import GeoMalhaUf
from app.modules.ingestion import cobertura as cobertura_mod
from app.modules.ingestion.connectors.ibge import (
    IbgeMalhaConnector,
    IbgePibConnector,
    IbgePopulacaoConnector,
)
from app.modules.ingestion.models import (
    FONTE_IBGE_MALHA,
    BcbIndice,
    DimEntrega,
    IbgePib,
    IbgePopulacao,
    IngestionLog,
    MartCoberturaFonte,
    RawPayload,
    SadipemCronogramaPgto,
    SadipemOpContratada,
    SadipemPvl,
)
from app.modules.ingestion.router import get_client_resolver
from app.shared.ingestion.client import (
    SADIPEM_BASE_URL,
    IbgeAgregadosClient,
    SadipemClient,
)
from app.workers import ingest_jobs
from tests.conftest import auth_header, login


class FakeRecordsClient:
    """Cliente/resolver falso (sem rede)."""

    def __init__(self) -> None:
        self.records: dict[str, list[dict[str, Any]]] = {}
        self.records_by_request: dict[
            tuple[str, tuple[tuple[str, str], ...]], list[dict[str, Any]]
        ] = {}
        self.documents_by_request: dict[tuple[str, tuple[tuple[str, str], ...]], Any] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, fonte: str) -> FakeRecordsClient:
        return self

    @staticmethod
    def _request_key(path: str, params: dict[str, Any]) -> tuple[str, tuple[tuple[str, str], ...]]:
        return path.removeprefix("tt/"), tuple(
            sorted((key, str(value)) for key, value in params.items())
        )

    def set_records(self, path: str, params: dict[str, Any], records: list[dict[str, Any]]) -> None:
        """Registra resposta exata; necessário para cronogramas por ``id_pleito``."""
        self.records_by_request[self._request_key(path, params)] = records

    def set_document(self, path: str, params: dict[str, Any], document: Any) -> None:
        """Registra um documento cru, sem o flatten usado pelas tabelas do IBGE."""
        self.documents_by_request[self._request_key(path, params)] = document

    def get_document(self, path: str, params: dict[str, Any]) -> Any:
        self.calls.append((path, dict(params)))
        return self.documents_by_request[self._request_key(path, params)]

    def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((path, dict(params)))
        exact = self.records_by_request.get(self._request_key(path, params))
        if exact is not None:
            return list(exact)
        # Suporta match por prefixo de path (BCB/IBGE têm código no caminho).
        # ``tt/`` mantém compatibilidade com fixtures da URL SADIPEM antiga.
        path_aliases = {path, path.removeprefix("tt/"), f"tt/{path.removeprefix('tt/')}"}
        for key, data in self.records.items():
            if any(alias.startswith(key) for alias in path_aliases):
                return list(data)
        return []


def _ente() -> str:
    return "9" + "".join(random.choices("0123456789", k=6))


@pytest.fixture
def fake_client() -> Iterator[FakeRecordsClient]:
    fake = FakeRecordsClient()
    ingest_jobs.set_eager(True)
    ingest_jobs.set_recalcular(False)
    app.dependency_overrides[get_client_resolver] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_client_resolver, None)
    ingest_jobs.set_eager(False)
    ingest_jobs.set_recalcular(True)


@pytest.fixture
def cleanup() -> Iterator[list[str]]:
    used: list[str] = []
    yield used
    with SessionLocal() as s:
        for cod in used:
            if len(cod) == 2:
                s.execute(
                    delete(IngestionLog).where(
                        IngestionLog.fonte == FONTE_IBGE_MALHA,
                        IngestionLog.cod_ibge == cod,
                    )
                )
                s.execute(
                    delete(DimEntrega).where(
                        DimEntrega.relatorio == "IBGE-MALHA",
                        DimEntrega.cod_ibge == cod,
                    )
                )
                s.execute(
                    delete(MartCoberturaFonte).where(
                        MartCoberturaFonte.fonte == FONTE_IBGE_MALHA,
                        MartCoberturaFonte.cod_ibge == cod,
                    )
                )
                s.execute(
                    delete(RawPayload).where(
                        RawPayload.fonte == FONTE_IBGE_MALHA,
                        RawPayload.cod_ibge == cod,
                    )
                )
                s.execute(delete(GeoMalhaUf).where(GeoMalhaUf.uf == cod))
                continue
            s.execute(delete(IngestionLog).where(IngestionLog.cod_ibge == cod))
            s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
            for model in (
                RawPayload,
                SadipemPvl,
                SadipemOpContratada,
                SadipemCronogramaPgto,
                IbgePopulacao,
                IbgePib,
                DimEnte,
            ):
                s.execute(delete(model).where(model.cod_ibge == cod))
        s.commit()


def _admin_token(client: TestClient, make_org, *entes: str) -> str:
    fx = make_org(entes=list(entes))
    return login(client, fx.email, fx.senha)


def _run(client: TestClient, token: str, body: dict[str, Any]) -> dict[str, Any]:
    resp = client.post("/admin/ingestion/run", json=body, headers=auth_header(token))
    assert resp.status_code == 202, resp.text
    job = resp.json()["job"]
    assert job["status"] == "concluido", job
    return job["resultado"]["resumo_execucao"]


def _count(model: type, **filtros: Any) -> int:
    with SessionLocal() as s:
        stmt = select(func.count()).select_from(model)
        for col, val in filtros.items():
            stmt = stmt.where(getattr(model, col) == val)
        return s.scalar(stmt) or 0


def _malha_geojson(uf: str = "99", *, n_areas: int = 2) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "codarea": f"{uf}{indice:05d}",
                    "nomearea": f"Município {indice}",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [float(indice), 0.0],
                            [float(indice), 1.0],
                            [float(indice + 1), 1.0],
                            [float(indice), 0.0],
                        ]
                    ],
                },
            }
            for indice in range(1, n_areas + 1)
        ],
    }


# ---------------- SADIPEM ----------------
def test_sadipem_cliente_usa_url_oficial_e_limite_de_uma_requisicao() -> None:
    client = SadipemClient()
    try:
        assert SADIPEM_BASE_URL == ("https://apidatalake.tesouro.gov.br/ords/cdwhprd/sadipem/tt/")
        assert str(client._client.base_url) == SADIPEM_BASE_URL
        assert client._page_size == 5_000
        assert client._limiter._min_interval == 1.0
    finally:
        client.close()


def test_sadipem_pvl_versao_por_data_de_captura(client, make_org, fake_client, cleanup) -> None:
    ente = _ente()
    cleanup.append(ente)
    fake_client.records["tt/pvl"] = [
        # O payload espelha o que a API do SADIPEM devolve de fato: há ``status``, não
        # ``decisao`` — este último era mapeado e vinha nulo em 606 de 606 linhas reais.
        {
            "id_pvl": "1",
            "num_pvl": "PVL02.000883/2023-32",
            "num_processo": "17944.101691/2023-13",
            "tipo_operacao": "Interna",
            "finalidade": "Amortização de dívida",
            "credor": "Banco do Brasil S/A",
            "tipo_credor": "Instituição Financeira Nacional",
            "valor": "1000000.00",
            "status": "Deferido",
            "data_protocolo": "2023-04-27",
            "data_analise": "2024-05-10",
        },
        {
            "id_pvl": "2",
            "tipo_operacao": "Externa",
            "finalidade": "Infraestrutura",
            "credor": "Banco Interamericano de Desenvolvimento",
            "tipo_credor": "Instituição Financeira Internacional",
            "valor": "2000000.00",
            "status": "Em análise",
            "data_analise": "31/05/2024",
        },
    ]
    token = _admin_token(client, make_org, ente)
    body = {"fonte": "sadipem_pvl", "entes": [ente], "anos": [2024], "versao": "20260704"}

    res = _run(client, token, body)
    assert res["silver_rows"] == 2
    assert res["versoes_vigentes"] == ["20260704"]
    assert _count(SadipemPvl, cod_ibge=ente, versao_entrega="20260704") == 2

    # Rodar 2x com a mesma versão não duplica.
    res2 = _run(client, token, body)
    assert res2["pulados"] == 1
    assert _count(RawPayload, cod_ibge=ente, fonte="sadipem_pvl") == 1
    assert _count(SadipemPvl, cod_ibge=ente) == 2
    assert fake_client.calls[0] == ("pvl", {"id_ente": ente})


def test_sadipem_preserva_anos_da_mesma_captura(
    client, make_org, fake_client, cleanup
) -> None:
    ente = _ente()
    cleanup.append(ente)
    fake_client.records["pvl"] = [
        {
            "id_pleito": 77,
            "tipo_operacao": "Operação contratual interna",
            "valor": "500000.00",
            "status": "Deferido",
        }
    ]
    token = _admin_token(client, make_org, ente)

    res = _run(
        client,
        token,
        {
            "fonte": "sadipem_pvl",
            "entes": [ente],
            "anos": [2024, 2025],
            "versao": "20260704",
        },
    )

    assert res["silver_rows"] == 2
    with SessionLocal() as session:
        valid_times = set(
            session.scalars(
                select(SadipemPvl.valid_time).where(
                    SadipemPvl.cod_ibge == ente,
                    SadipemPvl.versao_entrega == "20260704",
                )
            )
        )
    assert valid_times == {date(2024, 12, 31), date(2025, 12, 31)}


def test_sadipem_operacoes_contratadas_vem_do_pvl_e_filtra_flag(
    client, make_org, fake_client, cleanup
) -> None:
    ente = _ente()
    cleanup.append(ente)
    fake_client.records["pvl"] = [
        {
            "id_pleito": 101,
            "tipo_operacao": "Operação contratual interna",
            "credor": "Caixa Econômica Federal",
            "moeda": "Real",
            "valor": 50_000_000,
            "pvl_contratado_credor": 1,
            "data_status": "13/06/2025",
        },
        {
            "id_pleito": 102,
            "tipo_operacao": "Operação contratual externa",
            "credor": "BID",
            "moeda": "Dólar dos EUA",
            "valor": "90000000.50",
            # Grafia encontrada no payload real do Tesouro.
            "pvl_contradado_credor": "1",
            "data_status": "2025-07-14",
        },
        {
            "id_pleito": 103,
            "credor": "Não deve materializar",
            "valor": 1,
            "pvl_contratado_credor": 0,
        },
    ]
    token = _admin_token(client, make_org, ente)
    result = _run(
        client,
        token,
        {
            "fonte": "sadipem_op_contratada",
            "entes": [ente],
            "anos": [2025],
            "versao": "20260704",
        },
    )
    assert result["silver_rows"] == 2

    with SessionLocal() as s:
        rows = list(
            s.scalars(
                select(SadipemOpContratada)
                .where(SadipemOpContratada.cod_ibge == ente)
                .order_by(SadipemOpContratada.id_operacao)
            )
        )
    assert [row.id_operacao for row in rows] == ["101", "102"]
    assert rows[0].tipo_operacao == "Operação contratual interna"
    assert rows[0].credor == "Caixa Econômica Federal"
    assert float(rows[0].valor_contratado or 0) == 50_000_000.0
    assert rows[0].data_contratacao is not None
    assert rows[1].moeda == "Dólar dos EUA"
    assert float(rows[1].valor_contratado or 0) == 90_000_000.5
    assert fake_client.calls == [("pvl", {"id_ente": ente})]


def test_sadipem_cronograma_busca_somente_pleitos_contratados_e_aceita_grafias(
    client, make_org, fake_client, cleanup
) -> None:
    ente = _ente()
    cleanup.append(ente)
    fake_client.records["pvl"] = [
        {"id_pleito": 201, "pvl_contratado_credor": 1},
        {"id_pleito": 202, "pvl_contratado_credor": 0},
        {"id_pleito": 203, "pvl_contradado_credor": 1},
    ]
    fake_client.set_records(
        "opc-cronograma-pagamentos",
        {"id_pleito": 201},
        [
            {
                "id_pleito": 201,
                "ano": "2026",
                "total_amorizacao": "1000000.25",
                "total_encargos": "200000.50",
            }
        ],
    )
    fake_client.set_records(
        "opc-cronograma-pagamentos",
        {"id_pleito": 203},
        [
            {
                "id_pleito": 203,
                "ano": 2027,
                "total_amortizacao": 800_000,
                "total_encargos": 150_000,
            },
            # Layout legado (``principal``/``encargos``) continua aceito, agora com o
            # corte que a API real publica: dívida consolidada × operações contratadas.
            {
                "id_operacao": "203",
                "ano": 2028,
                "principal": 700_000,
                "encargos": 50_000,
                "divida_consolidada_amortizacao": 650_000,
                "divida_consolidada_encargos": 45_000,
                "operacoes_contratadas_amortizacao": 50_000,
                "operacoes_contratadas_encargos": 5_000,
                "indicador_div_moeda_estrang": "1  ",
            },
        ],
    )
    token = _admin_token(client, make_org, ente)
    result = _run(
        client,
        token,
        {
            "fonte": "sadipem_cronograma_pgto",
            "entes": [ente],
            "anos": [2025],
            "versao": "20260704",
        },
    )
    assert result["silver_rows"] == 3

    with SessionLocal() as s:
        rows = list(
            s.scalars(
                select(SadipemCronogramaPgto)
                .where(SadipemCronogramaPgto.cod_ibge == ente)
                .order_by(SadipemCronogramaPgto.ano)
            )
        )
    assert [(row.id_operacao, row.ano) for row in rows] == [
        ("201", 2026),
        ("203", 2027),
        ("203", 2028),
    ]
    assert float(rows[0].principal or 0) == 1_000_000.25
    assert float(rows[0].encargos or 0) == 200_000.5
    assert float(rows[1].principal or 0) == 800_000.0
    # O corte que estava sendo descartado, e a bandeira com o espaço que a API manda.
    assert float(rows[2].dc_amortizacao or 0) == 650_000.0
    assert float(rows[2].oc_amortizacao or 0) == 50_000.0
    assert rows[2].moeda_estrangeira is True
    assert [(path, params.get("id_pleito")) for path, params in fake_client.calls] == [
        ("pvl", None),
        ("opc-cronograma-pagamentos", 201),
        ("opc-cronograma-pagamentos", 203),
    ]


# ---------------- BCB / SGS ----------------
def test_bcb_long_format_uma_linha_por_data(client, make_org, fake_client, cleanup) -> None:
    codigo = 433  # IPCA
    cleanup.append(str(codigo))
    fake_client.records["dados/serie/bcdata.sgs.433/dados"] = [
        {"data": "01/01/2024", "valor": "0.42"},
        {"data": "01/02/2024", "valor": "0.83"},
        {"data": "01/03/2024", "valor": "0.16"},
    ]
    token = _admin_token(client, make_org)
    body = {
        "fonte": "bcb",
        "series": [433],
        "data_inicial": "2024-01-01",
        "data_final": "2024-03-31",
        "versao": "20260704",
    }
    res = _run(client, token, body)
    assert res["silver_rows"] == 3
    assert _count(BcbIndice, codigo_serie=433, versao_entrega="20260704") == 3

    # A asserção é escopada a ESTA versão de entrega: a série 433 (IPCA) real pode estar
    # populada pelo backfill da Sprint 21 (2019→hoje) em outra versão, e as duas coexistem
    # no long format bitemporal sem que este teste veja o dado real.
    with SessionLocal() as s:
        valores = sorted(
            float(v)
            for v in s.scalars(
                select(BcbIndice.valor).where(
                    BcbIndice.codigo_serie == 433,
                    BcbIndice.versao_entrega == "20260704",
                )
            )
        )
    assert valores == [0.16, 0.42, 0.83]

    # Limpeza escopada à versão do teste — nunca apaga a série 433 real de outra versão.
    with SessionLocal() as s:
        s.execute(
            delete(BcbIndice).where(
                BcbIndice.codigo_serie == 433,
                BcbIndice.versao_entrega == "20260704",
            )
        )
        s.commit()


# ---------------- IBGE ----------------
def test_ibge_flatten_agregados() -> None:
    """O cliente IBGE achata a estrutura v3 aninhada em registros planos."""
    payload = [
        {
            "id": "9324",
            "resultados": [
                {
                    "series": [
                        {"localidade": {"id": "2304400"}, "serie": {"2022": "2428708"}},
                    ]
                }
            ],
        }
    ]
    flat = IbgeAgregadosClient.flatten(payload)
    assert flat == [{"variavel": "9324", "cod_ibge": "2304400", "ano": "2022", "valor": "2428708"}]


def test_ibge_flatten_pesquisa_leaf_pib_per_capita() -> None:
    """A Pesquisa 38/47001 usa ``res`` em vez da estrutura dos agregados v3."""
    payload = [
        {
            "id": 47001,
            "res": [
                {
                    "localidade": "230440",
                    "res": {"2021": "27165.05"},
                    "notas": {"2021": None},
                }
            ],
        }
    ]
    flat = IbgeAgregadosClient.flatten(payload)
    assert flat == [
        {
            "variavel": "47001",
            "cod_ibge": "230440",
            "ano": "2021",
            "valor": "27165.05",
        }
    ]


def test_ibge_cliente_preserva_documento_geojson(monkeypatch) -> None:
    payload = _malha_geojson("21")
    client = IbgeAgregadosClient()
    monkeypatch.setattr(client, "_get_json", lambda path, params: payload)
    try:
        assert client.get_document("v3/malhas/estados/21", {}) is payload
    finally:
        client.close()


def test_ibge_malha_deduplica_uf_e_usa_endpoint_geojson_exato(
    fake_client: FakeRecordsClient,
) -> None:
    connector = IbgeMalhaConnector(fake_client, cast(Any, None))
    jobs = connector.discover(
        {
            "entes": ["21", "2111300", "2111201", "23", "2304400"],
            "anos": [2021, 2022, 2026],
            "versao": "teste",
        }
    )

    assert [(job.cod_ibge, job.ano, job.periodo) for job in jobs] == [
        ("21", 2022, "2022"),
        ("23", 2022, "2022"),
    ]

    payload = _malha_geojson("21")
    params = {
        "periodo": "2022",
        "intrarregiao": "municipio",
        "formato": "application/vnd.geo+json",
        "qualidade": "minima",
    }
    fake_client.set_document("v3/malhas/estados/21", params, payload)

    assert connector.extract(jobs[0]) == {"geojson": payload, "qualidade": "minima"}
    assert fake_client.calls == [("v3/malhas/estados/21", params)]


@pytest.mark.parametrize(
    ("payload", "mensagem"),
    [
        ({"type": "FeatureCollection", "features": []}, "não contém polígonos"),
        (
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"codarea": "2300001"},
                        "geometry": {"type": "Polygon", "coordinates": [[[0, 0]]]},
                    }
                ],
            },
            "código municipal incompatível",
        ),
        (
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"codarea": "2100001"},
                        "geometry": None,
                    }
                ],
            },
            "geometria municipal inválida",
        ),
    ],
)
def test_ibge_malha_rejeita_geojson_invalido(
    fake_client: FakeRecordsClient, payload: dict[str, Any], mensagem: str
) -> None:
    connector = IbgeMalhaConnector(fake_client, cast(Any, None))
    params = {
        "periodo": "2022",
        "intrarregiao": "municipio",
        "formato": "application/vnd.geo+json",
        "qualidade": "minima",
    }
    fake_client.set_document("v3/malhas/estados/21", params, payload)
    job = connector.discover({"entes": ["21"], "anos": [2022], "versao": "teste"})[0]

    with pytest.raises(ValueError, match=mensagem):
        connector.extract(job)


def test_ibge_malha_job_normaliza_persiste_e_protege_vigencia(
    client, make_org, fake_client, cleanup
) -> None:
    uf = "98"
    municipios = ["9800001", "9800002"]
    cleanup.extend([uf, *municipios])
    payload_vigente = _malha_geojson(uf, n_areas=2)
    params = {
        "periodo": "2022",
        "intrarregiao": "municipio",
        "formato": "application/vnd.geo+json",
        "qualidade": "minima",
    }

    with SessionLocal() as s:
        for cod_ibge in municipios:
            s.merge(
                DimEnte(
                    cod_ibge=cod_ibge,
                    nome=f"Município {cod_ibge}",
                    esfera="municipal",
                    uf=uf,
                )
            )
        s.commit()
    path = f"v3/malhas/estados/{uf}"
    fake_client.set_document(path, params, payload_vigente)
    org = make_org(tipo_conta="estado", entes=[uf, "9800001", "9800002"])
    token = login(client, org.email, org.senha)
    body = {
        "fonte": FONTE_IBGE_MALHA,
        "entes": ["9800001", "9800002"],
        "anos": [2021, 2022, 2026],
        "versao": "malha-vigente",
    }

    primeira = client.post("/admin/ingestion/run", json=body, headers=auth_header(token))

    assert primeira.status_code == 202, primeira.text
    primeiro_job = primeira.json()["job"]
    assert primeiro_job["status"] == "concluido"
    assert primeiro_job["entes"] == [uf]
    assert primeiro_job["periodos"] == ["2022"]
    assert primeiro_job["itens_total"] == 1
    assert primeiro_job["resultado"]["resumo_execucao"]["silver_rows"] == 1
    assert fake_client.calls == [(path, params)]

    # Mesma chave é idempotente: consulta novamente a origem, mas não duplica bronze/gold.
    repetida = client.post("/admin/ingestion/run", json=body, headers=auth_header(token))
    assert repetida.status_code == 202, repetida.text
    assert repetida.json()["job"]["resultado"]["resumo_execucao"]["pulados"] == 1

    # Uma entrega histórica pode ser preservada no bronze sem rebaixar o mapa servido.
    fake_client.set_document(path, params, _malha_geojson(uf, n_areas=1))
    historica = client.post(
        "/admin/ingestion/run",
        json={
            **body,
            "versao": "malha-historica",
            "homologada_em": "2000-01-01T00:00:00Z",
        },
        headers=auth_header(token),
    )
    assert historica.status_code == 202, historica.text
    resumo_historico = historica.json()["job"]["resultado"]["resumo_execucao"]
    assert resumo_historico["silver_rows"] == 0
    assert resumo_historico["versoes_vigentes"] == []

    replay = client.post(
        "/admin/ingestion/replay",
        params={"ente": uf, "periodo": "2022", "fonte": FONTE_IBGE_MALHA},
        headers=auth_header(token),
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["job"]["status"] == "concluido"
    resumo_replay = replay.json()["job"]["resultado"]["resumo_execucao"]
    assert resumo_replay["silver_rows"] == 1
    assert resumo_replay["versoes_vigentes"] == ["malha-vigente"]

    # ``force`` reprocessa o bronze já guardado; uma resposta nova sob a mesma versão
    # não pode sobrescrever gold e criar divergência com um replay posterior.
    fake_client.set_document(path, params, _malha_geojson(uf, n_areas=3))
    forcada = client.post(
        "/admin/ingestion/run",
        json={**body, "force": True},
        headers=auth_header(token),
    )
    assert forcada.status_code == 202, forcada.text
    job_forcado = forcada.json()["job"]
    assert job_forcado["status"] == "concluido"
    assert job_forcado["resultado"]["resumo_execucao"]["silver_rows"] == 1

    # Uma versão realmente nova e incompleta continua sendo recusada, sem rebaixar a
    # entrega vigente que já passou pela validação de catálogo.
    fake_client.set_document(path, params, _malha_geojson(uf, n_areas=1))
    incompleta = client.post(
        "/admin/ingestion/run",
        json={**body, "versao": "malha-incompleta"},
        headers=auth_header(token),
    )
    assert incompleta.status_code == 202, incompleta.text
    job_incompleto = incompleta.json()["job"]
    assert job_incompleto["status"] == "falhou"
    assert "faltam 1 município" in job_incompleto["resultado"]["itens"][0]["erro"]

    resposta_malha = client.get(f"/geo/malha/{uf}", headers=auth_header(token))
    assert resposta_malha.status_code == 200, resposta_malha.text
    assert resposta_malha.json() == {
        "uf": uf,
        "formato": "geojson",
        "fonte": "IBGE — API de malhas v3",
        "ano": 2022,
        "n_areas": 2,
        "simplificacao": "minima",
        "malha": payload_vigente,
    }

    status = client.get(
        "/admin/ingestion/status",
        params={"fonte": FONTE_IBGE_MALHA},
        headers=auth_header(token),
    )
    assert status.status_code == 200, status.text
    versoes = [row for row in status.json() if row["cod_ibge"] == uf]
    assert {row["versao_entrega"] for row in versoes} == {
        "malha-vigente",
        "malha-historica",
    }
    assert [row["versao_entrega"] for row in versoes if row["vigente"]] == ["malha-vigente"]

    with SessionLocal() as s:
        malha = s.get(GeoMalhaUf, uf)
        vigente = s.scalar(
            select(DimEntrega).where(
                DimEntrega.cod_ibge == uf,
                DimEntrega.relatorio == "IBGE-MALHA",
                DimEntrega.periodo == "2022",
                DimEntrega.vigente.is_(True),
            )
        )
        cobertura_mod.refresh_cobertura(s)
        cobertura = s.get(MartCoberturaFonte, (FONTE_IBGE_MALHA, uf, "2022"))
        assert malha is not None
        assert malha.formato == "geojson"
        assert malha.simplificacao == "minima"
        assert malha.fonte == "IBGE — API de malhas v3"
        assert malha.ano == 2022
        assert malha.n_areas == 2
        assert malha.malha == payload_vigente
        assert vigente is not None
        assert vigente.versao_entrega == "malha-vigente"
        assert cobertura is not None
        assert cobertura.n_registros == 2
        assert cobertura.versao_entrega_vigente == "malha-vigente"
        assert cobertura.defasagem_periodos == 0
        s.rollback()

    assert _count(RawPayload, fonte=FONTE_IBGE_MALHA, cod_ibge=uf) == 2


@pytest.mark.parametrize(
    ("cod_ibge", "nivel"),
    [("21", "N3"), ("2111300", "N6")],
)
def test_ibge_populacao_usa_nivel_territorial_do_ente(
    fake_client: FakeRecordsClient, cod_ibge: str, nivel: str
) -> None:
    connector = IbgePopulacaoConnector(fake_client, cast(Any, None))
    job = connector.discover({"entes": [cod_ibge], "anos": [2025], "versao": "teste"})[0]

    connector.extract(job)

    assert fake_client.calls == [
        (
            "v3/agregados/6579/periodos/2025/variaveis/9324",
            {"localidades": f"{nivel}[{cod_ibge}]"},
        )
    ]


def test_ibge_pib_uf_usa_n3_sem_consultar_per_capita_municipal(
    fake_client: FakeRecordsClient,
) -> None:
    connector = IbgePibConnector(fake_client, cast(Any, None))
    job = connector.discover({"entes": ["21"], "anos": [2023], "versao": "teste"})[0]
    fake_client.set_records(
        "v3/agregados/5938/periodos/2023/variaveis/37",
        {"localidades": "N3[21]"},
        [{"variavel": "37", "cod_ibge": "21", "ano": "2023", "valor": "149227195"}],
    )

    payload = connector.extract(job)

    assert fake_client.calls == [
        (
            "v3/agregados/5938/periodos/2023/variaveis/37",
            {"localidades": "N3[21]"},
        )
    ]
    assert payload["pib_nominal_agregado_5938_variavel_37"][0]["valor"] == "149227195"
    assert payload["pib_per_capita_pesquisa_38_indicador_47001"] == []


def test_ibge_rejeita_codigo_sem_nivel_territorial(fake_client: FakeRecordsClient) -> None:
    connector = IbgePopulacaoConnector(fake_client, cast(Any, None))
    job = connector.discover({"entes": ["211130"], "anos": [2025], "versao": "teste"})[0]

    with pytest.raises(ValueError, match="Use 2 dígitos para UF ou 7 para município"):
        connector.extract(job)

    assert fake_client.calls == []


def test_ibge_populacao_materializa_silver(client, make_org, fake_client, cleanup) -> None:
    ente = _ente()
    cleanup.append(ente)
    fake_client.records["v3/agregados/6579/periodos/2022/variaveis"] = [
        {"variavel": "9324", "cod_ibge": ente, "ano": "2022", "valor": "150000"}
    ]
    token = _admin_token(client, make_org, ente)
    body = {"fonte": "ibge_populacao", "entes": [ente], "anos": [2022], "versao": "20260704"}
    res = _run(client, token, body)
    assert res["silver_rows"] == 1

    with SessionLocal() as s:
        row = s.scalar(
            select(IbgePopulacao).where(
                IbgePopulacao.cod_ibge == ente, IbgePopulacao.ano_ref == 2022
            )
        )
    assert row is not None
    assert row.populacao == 150000
    assert row.fonte == "estimativa"


def test_ibge_pib_usa_per_capita_oficial_sem_derivar_da_populacao(
    client, make_org, fake_client, cleanup
) -> None:
    ente = _ente()
    cleanup.append(ente)
    fake_client.records["v3/agregados/6579/periodos/2021/variaveis"] = [
        {"variavel": "9324", "cod_ibge": ente, "ano": "2021", "valor": "150000"}
    ]
    fake_client.records["v3/agregados/5938/periodos/2021/variaveis"] = [
        {"variavel": "37", "cod_ibge": ente, "ano": "2021", "valor": "5000000"},
    ]
    fake_client.records[
        "v1/pesquisas/38/periodos/2021/indicadores/47001/resultados/"
    ] = [
        {"variavel": "47001", "cod_ibge": ente[:6], "ano": "2021", "valor": "27165.05"},
    ]
    token = _admin_token(client, make_org, ente)
    _run(
        client,
        token,
        {
            "fonte": "ibge_populacao",
            "entes": [ente],
            "anos": [2021],
            "versao": "20260704",
        },
    )
    body = {"fonte": "ibge_pib", "entes": [ente], "anos": [2021], "versao": "20260704"}
    _run(client, token, body)

    with SessionLocal() as s:
        row = s.scalar(select(IbgePib).where(IbgePib.cod_ibge == ente, IbgePib.ano_ref == 2021))
    assert row is not None
    assert float(row.pib_nominal) == 5000000.0
    # 5.000.000 mil R$ / 150.000 hab. seria R$ 33.333,33; o valor persistido deve
    # ser o indicador oficial 47001, e não uma derivação local.
    assert float(row.pib_per_capita) == pytest.approx(27165.05)
    assert (
        f"v1/pesquisas/38/periodos/2021/indicadores/47001/resultados/{ente}",
        {},
    ) in fake_client.calls


def test_ibge_pib_nao_usa_outro_ano_nem_faz_fallback_por_populacao(
    client, make_org, fake_client, cleanup
) -> None:
    ente = _ente()
    cleanup.append(ente)
    fake_client.records["v3/agregados/6579/periodos/2021/variaveis"] = [
        {"variavel": "9324", "cod_ibge": ente, "ano": "2021", "valor": "100000"}
    ]
    fake_client.records["v3/agregados/5938/periodos/2021/variaveis"] = [
        {"variavel": "37", "cod_ibge": ente, "ano": "2021", "valor": "2000000"},
    ]
    fake_client.records[
        "v1/pesquisas/38/periodos/2021/indicadores/47001/resultados/"
    ] = [
        {"variavel": "47001", "cod_ibge": ente[:6], "ano": "2020", "valor": "19999.99"},
    ]
    token = _admin_token(client, make_org, ente)
    _run(
        client,
        token,
        {
            "fonte": "ibge_populacao",
            "entes": [ente],
            "anos": [2021],
            "versao": "20260704",
        },
    )
    _run(
        client,
        token,
        {"fonte": "ibge_pib", "entes": [ente], "anos": [2021], "versao": "20260704"},
    )

    with SessionLocal() as s:
        row = s.scalar(select(IbgePib).where(IbgePib.cod_ibge == ente, IbgePib.ano_ref == 2021))
    assert row is not None
    assert float(row.pib_nominal) == 2000000.0
    assert row.pib_per_capita is None
