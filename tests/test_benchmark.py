"""Contrato da Sprint 13: coortes explícitas e benchmark auditável.

Os dados abaixo são deliberadamente sintéticos e vivem em um período futuro exclusivo.
Cada execução escolhe códigos próprios e o cleanup filtra estritamente esses códigos;
assim a suíte nunca trunca nem apaga as cargas reais SICONFI/IBGE do banco local.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.modules.benchmark.models import DimCoorte, MartBenchmark
from app.modules.catalog.models import DimEnte
from app.modules.indicators.models import MartIndicador
from app.modules.ingestion.models import DimEntrega, IbgePib, IbgePopulacao, SilverEnte
from app.shared.source_ref import SourceRef, composite_version_key
from tests.conftest import auth_header, login

PERIODO = "2099-B6"
PERIODO_RGF = "2099-Q3"
INDICADOR = "pessoal_executivo"
AS_OF_V1 = datetime(2025, 3, 1, tzinfo=UTC)
AS_OF_V2 = datetime(2025, 7, 1, tzinfo=UTC)
AS_OF_V3 = datetime(2025, 9, 1, tzinfo=UTC)
HOMOLOGADA_V1 = datetime(2025, 1, 10, tzinfo=UTC)
HOMOLOGADA_V2 = datetime(2025, 6, 10, tzinfo=UTC)
HOMOLOGADA_V3 = datetime(2025, 8, 10, tzinfo=UTC)
AS_OF_ANTES_DA_PRIMEIRA_ENTREGA = datetime(2024, 12, 31, tzinfo=UTC)


@dataclass(frozen=True)
class BenchmarkData:
    selecionado: str
    empate: str
    singleton: str
    outro_escopo: str
    codigos: tuple[str, ...]


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _personnel_refs(versao_rgf: str, versao_rreo: str) -> tuple[SourceRef, SourceRef]:
    return (
        SourceRef(
            relatorio="RGF",
            anexo="Anexo 01",
            periodo=PERIODO_RGF,
            versao_entrega=versao_rgf,
        ),
        SourceRef(
            relatorio="RREO",
            anexo="Anexo 03",
            periodo=PERIODO,
            versao_entrega=versao_rreo,
        ),
    )


def _cleanup(codigos: tuple[str, ...]) -> None:
    """Remove somente fatos criados por este módulo, preservando toda carga preexistente."""
    with SessionLocal() as session:
        session.execute(delete(MartBenchmark).where(MartBenchmark.cod_ibge.in_(codigos)))
        session.execute(delete(MartIndicador).where(MartIndicador.cod_ibge.in_(codigos)))
        session.execute(delete(IbgePib).where(IbgePib.cod_ibge.in_(codigos)))
        session.execute(delete(IbgePopulacao).where(IbgePopulacao.cod_ibge.in_(codigos)))
        session.execute(delete(DimEntrega).where(DimEntrega.cod_ibge.in_(codigos)))
        session.execute(delete(DimEnte).where(DimEnte.cod_ibge.in_(codigos)))
        session.execute(delete(SilverEnte).where(SilverEnte.cod_ibge.in_(codigos)))
        session.commit()


def _novos_codigos(quantidade: int) -> tuple[str, ...]:
    """Reserva uma faixa de códigos sintéticos que não colida com o banco compartilhado."""
    while True:
        inicio = 9_000_000 + uuid.uuid4().int % 800_000
        codigos = tuple(str(inicio + indice) for indice in range(quantidade))
        with SessionLocal() as session:
            existente = session.scalar(
                select(DimEnte.cod_ibge).where(DimEnte.cod_ibge.in_(codigos)).limit(1)
            )
        if existente is None:
            return codigos


def _add_ente(
    session: Any,
    *,
    cod_ibge: str,
    nome: str,
    populacao: int,
    regiao: str,
    pib: int,
    esfera: str = "municipal",
) -> None:
    ibge_version = "benchmark-ibge-v1"
    session.add(
        SilverEnte(
            cod_ibge=cod_ibge,
            nome=nome,
            uf="CE" if regiao == "NE" else "SP",
            esfera="E" if esfera == "estadual" else "M",
            populacao=populacao,
            regiao=regiao,
            capital=False,
            versao_entrega="benchmark-test",
        )
    )
    session.add(
        IbgePopulacao(
            cod_ibge=cod_ibge,
            ano_ref=2025,
            populacao=populacao,
            fonte="fixture isolada",
            valid_time=date(2025, 12, 31),
            versao_entrega=ibge_version,
        )
    )
    session.add(
        IbgePib(
            cod_ibge=cod_ibge,
            ano_ref=2023,
            pib_nominal=Decimal(pib),
            pib_per_capita=None,
            valid_time=date(2023, 12, 31),
            versao_entrega=ibge_version,
        )
    )
    for relatorio, periodo_fonte in (("IBGE-POP", "2025"), ("IBGE-PIB", "2023")):
        session.add(
            DimEntrega(
                cod_ibge=cod_ibge,
                relatorio=relatorio,
                periodo=periodo_fonte,
                versao_entrega=ibge_version,
                homologada_em=datetime(2024, 1, 1, tzinfo=UTC),
                vigente=True,
                hash_payload=f"benchmark-{relatorio}-{cod_ibge}",
            )
        )
    session.add(
        DimEnte(
            cod_ibge=cod_ibge,
            nome=nome,
            esfera=esfera,
            populacao=populacao,
            rpps=False,
            possui_tcm=False,
            uf="CE" if regiao == "NE" else "SP",
            regiao=regiao,
            pib=Decimal(pib),
            pop_ano_ref=2025,
            pib_ano_ref=2023,
            pop_source_ref={
                "relatorio": "IBGE-POP",
                "anexo": "Agregado 6579 - variavel 9324",
                "periodo": "2025",
                "versao_entrega": ibge_version,
            },
            pib_source_ref={
                "relatorio": "IBGE-PIB",
                "anexo": "Agregado 5938 - variavel 37 (mil reais)",
                "periodo": "2023",
                "versao_entrega": ibge_version,
            },
        )
    )


def _add_indicador(
    session: Any,
    *,
    cod_ibge: str,
    valor_pct: int,
    versao: str = "v1",
    vigente: bool = True,
    homologada_em: datetime = HOMOLOGADA_V1,
) -> None:
    component_refs = _personnel_refs(versao, versao)
    composite_version = composite_version_key(component_refs)
    source_ref = {
        "relatorio": "RGF/RREO",
        "anexo": "RGF Anexo 01 / RREO Anexo 03",
        "periodo": f"{PERIODO_RGF} / {PERIODO}",
        "versao_entrega": f"RGF:{versao};RREO:{versao}",
        "source_refs_componentes": [
            ref.model_dump(mode="json", exclude_none=True) for ref in component_refs
        ],
        "chave_versao_composta": composite_version,
        "tipo_registro": "historico_composto",
    }
    session.add(
        DimEntrega(
            cod_ibge=cod_ibge,
            relatorio="RREO",
            periodo=PERIODO,
            versao_entrega=versao,
            homologada_em=homologada_em,
            vigente=vigente,
            hash_payload=f"benchmark-{cod_ibge}-{versao}",
        )
    )
    session.add(
        DimEntrega(
            cod_ibge=cod_ibge,
            relatorio="RGF",
            periodo=PERIODO_RGF,
            versao_entrega=versao,
            homologada_em=homologada_em,
            vigente=vigente,
            hash_payload=f"benchmark-rgf-{cod_ibge}-{versao}",
        )
    )
    session.add(
        MartIndicador(
            cod_ibge=cod_ibge,
            periodo=PERIODO,
            indicador=INDICADOR,
            valor_rs=Decimal(valor_pct) * Decimal("1000000"),
            valor_pct_rcl=Decimal(valor_pct),
            faixa="normal",
            teto_pct=Decimal("54"),
            source_ref=source_ref,
            versao_entrega=composite_version,
        )
    )
    session.add(
        MartIndicador(
            cod_ibge=cod_ibge,
            periodo=PERIODO,
            indicador=INDICADOR,
            valor_rs=Decimal(valor_pct) * Decimal("1000000"),
            valor_pct_rcl=Decimal(valor_pct),
            faixa="normal",
            teto_pct=Decimal("54"),
            source_ref={**source_ref, "tipo_registro": "projecao_vigente"},
            versao_entrega=versao,
        )
    )


def _add_rgf_revision(
    session: Any,
    *,
    cod_ibge: str,
    valor_pct: int,
    versao_rgf: str,
    versao_rreo: str,
    homologada_em: datetime,
) -> None:
    component_refs = _personnel_refs(versao_rgf, versao_rreo)
    composite_version = composite_version_key(component_refs)
    session.add(
        DimEntrega(
            cod_ibge=cod_ibge,
            relatorio="RGF",
            periodo=PERIODO_RGF,
            versao_entrega=versao_rgf,
            homologada_em=homologada_em,
            vigente=True,
            hash_payload=f"benchmark-rgf-{cod_ibge}-{versao_rgf}",
        )
    )
    session.add(
        MartIndicador(
            cod_ibge=cod_ibge,
            periodo=PERIODO,
            indicador=INDICADOR,
            valor_rs=Decimal(valor_pct) * Decimal("1000000"),
            valor_pct_rcl=Decimal(valor_pct),
            faixa="normal",
            teto_pct=Decimal("54"),
            source_ref={
                "relatorio": "RGF/RREO",
                "anexo": "RGF Anexo 01 / RREO Anexo 03",
                "periodo": f"{PERIODO_RGF} / {PERIODO}",
                "versao_entrega": f"RGF:{versao_rgf};RREO:{versao_rreo}",
                "source_refs_componentes": [
                    ref.model_dump(mode="json", exclude_none=True)
                    for ref in component_refs
                ],
                "chave_versao_composta": composite_version,
                "tipo_registro": "historico_composto",
            },
            versao_entrega=composite_version,
        )
    )


@pytest.fixture(scope="module")
def benchmark_data() -> BenchmarkData:
    codigos = _novos_codigos(10)
    selecionado, pequeno_ne, empate, pequeno_su, medio_ne = codigos[:5]
    grande_ne, pequeno_ne_pib_alto, grande_se, estadual_ne, singleton = codigos[5:]
    _cleanup(codigos)
    try:
        with SessionLocal() as session:
            # O selecionado pertence simultaneamente a porte:pequeno, regiao:NE e pib:1a5bi.
            _add_ente(
                session,
                cod_ibge=selecionado,
                nome="Alfa Selecionado",
                populacao=30_000,
                regiao="NE",
                pib=2_000_000,
            )
            _add_ente(
                session,
                cod_ibge=pequeno_ne,
                nome="Beta Pequeno NE",
                populacao=40_000,
                regiao="NE",
                pib=2_500_000,
            )
            _add_ente(
                session,
                cod_ibge=empate,
                nome="Gama Empate",
                populacao=45_000,
                regiao="SE",
                pib=2_500_000,
            )
            _add_ente(
                session,
                cod_ibge=pequeno_su,
                nome="Delta Pequeno SU",
                populacao=49_000,
                regiao="SU",
                pib=500_000,
            )
            _add_ente(
                session,
                cod_ibge=medio_ne,
                nome="Épsilon Médio NE",
                populacao=100_000,
                regiao="NE",
                pib=2_200_000,
            )
            _add_ente(
                session,
                cod_ibge=grande_ne,
                nome="Zeta Grande NE",
                populacao=300_000,
                regiao="NE",
                pib=2_300_000,
            )
            _add_ente(
                session,
                cod_ibge=pequeno_ne_pib_alto,
                nome="Eta Pequeno PIB Alto",
                populacao=35_000,
                regiao="NE",
                pib=10_000_000,
            )
            _add_ente(
                session,
                cod_ibge=grande_se,
                nome="Teta Grande SE",
                populacao=300_000,
                regiao="SE",
                pib=3_000_000,
            )
            # Mesmas faixas, mas outra esfera: nunca pode contaminar a coorte municipal.
            _add_ente(
                session,
                cod_ibge=estadual_ne,
                nome="Estado Sintético",
                populacao=30_000,
                regiao="NE",
                pib=2_000_000,
                esfera="estadual",
            )
            # Único ente CO com indicador neste período: cobre n=1.
            _add_ente(
                session,
                cod_ibge=singleton,
                nome="Único Centro-Oeste",
                populacao=2_000_000,
                regiao="CO",
                pib=30_000_000,
            )

            valores = {
                selecionado: 20,
                pequeno_ne: 10,
                empate: 20,
                pequeno_su: 40,
                medio_ne: 5,
                grande_ne: 15,
                pequeno_ne_pib_alto: 60,
                grande_se: 12,
                estadual_ne: 99,
                singleton: 77,
            }
            for codigo, valor in valores.items():
                _add_indicador(
                    session,
                    cod_ibge=codigo,
                    valor_pct=valor,
                    vigente=codigo != selecionado,
                )

            # Retificação do selecionado para validar reprodução bitemporal.
            _add_indicador(
                session,
                cod_ibge=selecionado,
                valor_pct=35,
                versao="v2",
                vigente=True,
                homologada_em=HOMOLOGADA_V2,
            )
            # Retificacao apenas do RGF: o RREO v2 permanece estavel.
            _add_rgf_revision(
                session,
                cod_ibge=selecionado,
                valor_pct=45,
                versao_rgf="v3",
                versao_rreo="v2",
                homologada_em=HOMOLOGADA_V3,
            )
            session.commit()

        yield BenchmarkData(
            selecionado=selecionado,
            empate=empate,
            singleton=singleton,
            outro_escopo=pequeno_ne,
            codigos=codigos,
        )
    finally:
        _cleanup(codigos)


def _token(client: Any, make_org: Any, ente: str) -> str:
    fx = make_org(capacidades=["ver"], entes=[ente])
    return login(client, fx.email, fx.senha)


def _params(data: BenchmarkData, coorte: str, *, ente: str | None = None) -> dict[str, str]:
    return {
        "ente": ente or data.selecionado,
        "indicador": INDICADOR,
        "periodo": PERIODO,
        "coorte": coorte,
        "as_of": AS_OF_V1.isoformat(),
    }


def test_percent_rank_empates_quantis_e_rastreabilidade(
    client, make_org, benchmark_data: BenchmarkData
) -> None:
    token = _token(client, make_org, benchmark_data.selecionado)
    params = _params(benchmark_data, "porte:pequeno")

    response = client.get("/benchmark", params=params, headers=auth_header(token))
    assert response.status_code == 200, response.text
    body = response.json()

    # Valores ordenados: [10, 20, 20, 40, 60]. PERCENT_RANK dos dois 20 é 25%.
    assert body["coorte"]["codigo"] == "porte:pequeno"
    assert body["quantidade"] == 5  # o ente estadual de mesmo porte foi excluído
    assert _decimal(body["ente"]["valor"]) == Decimal("20")
    assert _decimal(body["ente"]["percentil"]) == Decimal("25")
    assert body["ente"]["posicao"] == 2
    assert body["ente"]["destaque"] is True

    distribuicao = body["distribuicao"]
    esperado = {
        "minimo": "10",
        "p10": "14",
        "p25": "20",
        "mediana": "20",
        "p75": "40",
        "p90": "52",
        "maximo": "60",
    }
    assert {campo: _decimal(distribuicao[campo]) for campo in esperado} == {
        campo: Decimal(valor) for campo, valor in esperado.items()
    }

    codigos_coorte = {item["codigo"] for item in body["coortes_disponiveis"]}
    assert {"porte:pequeno", "regiao:NE", "pib:1a5bi"} <= codigos_coorte
    assert any(item["codigo"] == INDICADOR for item in body["indicadores_disponiveis"])
    assert body["memoria"]
    assert body["source_refs"]
    assert body["ente"]["memoria"]
    assert body["ente"]["memoria"]["calculo_percentil"] == {
        "formula": "100 * (RANK(valor ASC) - 1) / (N - 1); N=1 => 0",
        "rank": 2,
        "N": 5,
        "resultado": "25.00",
    }
    assert body["ente"]["memoria"]["coorte"]["codigo"] == "porte:pequeno"
    assert body["ente"]["memoria"]["atributos_ente_na_coorte"] == {
        "populacao": 30_000,
        "pop_ano_ref": 2025,
        "pop_source_ref": {
            "relatorio": "IBGE-POP",
            "anexo": "Agregado 6579 - variavel 9324",
            "periodo": "2025",
            "versao_entrega": "benchmark-ibge-v1",
        },
        "regiao": "NE",
        "pib_mil_brl": "2000000",
        "pib_ano_ref": 2023,
        "pib_source_ref": {
            "relatorio": "IBGE-PIB",
            "anexo": "Agregado 5938 - variavel 37 (mil reais)",
            "periodo": "2023",
            "versao_entrega": "benchmark-ibge-v1",
        },
    }
    assert body["ente"]["source_ref"]["relatorio"] == "RGF/RREO"
    assert body["ente"]["source_ref"]["periodo"] == f"{PERIODO_RGF} / {PERIODO}"
    assert body["ente"]["source_ref"]["versao_entrega"] == "RGF:v1;RREO:v1"
    assert {
        item["relatorio"]
        for item in body["ente"]["memoria"]["source_refs_componentes"]
    } == {"RGF", "RREO"}
    assert body["ente"]["as_of"].startswith("2025-03-01")

    ranking = client.get(
        "/benchmark/ranking", params=params, headers=auth_header(token)
    ).json()
    por_codigo = {item["cod_ibge"]: item for item in ranking["itens"]}
    assert _decimal(por_codigo[benchmark_data.empate]["percentil"]) == Decimal("25")
    assert por_codigo[benchmark_data.empate]["posicao"] == 2


def test_percent_rank_singleton_e_quantis_de_um_elemento(
    client, make_org, benchmark_data: BenchmarkData
) -> None:
    token = _token(client, make_org, benchmark_data.singleton)
    params = _params(benchmark_data, "regiao:CO", ente=benchmark_data.singleton)

    response = client.get("/benchmark", params=params, headers=auth_header(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quantidade"] == 1
    assert _decimal(body["ente"]["percentil"]) == Decimal("0")
    assert body["ente"]["posicao"] == 1
    assert body["ente"]["destaque"] is True
    assert {
        _decimal(body["distribuicao"][campo])
        for campo in ("minimo", "p10", "p25", "mediana", "p75", "p90", "maximo")
    } == {Decimal("77")}


def test_troca_de_coorte_altera_grupo_e_percentil(
    client, make_org, benchmark_data: BenchmarkData
) -> None:
    token = _token(client, make_org, benchmark_data.selecionado)
    resultados: dict[str, tuple[int, Decimal]] = {}
    for coorte in ("porte:pequeno", "regiao:NE", "pib:1a5bi"):
        response = client.get(
            "/benchmark",
            params=_params(benchmark_data, coorte),
            headers=auth_header(token),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["coorte"]["codigo"] == coorte
        resultados[coorte] = (body["quantidade"], _decimal(body["ente"]["percentil"]))

    assert resultados == {
        "porte:pequeno": (5, Decimal("25")),
        "regiao:NE": (5, Decimal("75")),
        "pib:1a5bi": (6, Decimal("80")),
    }


def test_coorte_incompativel_retorna_422(
    client, make_org, benchmark_data: BenchmarkData
) -> None:
    token = _token(client, make_org, benchmark_data.selecionado)
    response = client.get(
        "/benchmark",
        params=_params(benchmark_data, "porte:medio"),
        headers=auth_header(token),
    )
    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["status"] == 422
    assert "coorte" in problem["title"].lower() or "coorte" in problem["detail"].lower()


def test_ranking_ordenavel_mantem_ente_nos_itens_e_ancorado(
    client, make_org, benchmark_data: BenchmarkData
) -> None:
    token = _token(client, make_org, benchmark_data.selecionado)
    params = {
        **_params(benchmark_data, "porte:pequeno"),
        "ordenar_por": "valor",
        "ordem": "desc",
    }
    response = client.get(
        "/benchmark/ranking", params=params, headers=auth_header(token)
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["ordenar"] == "valor"
    assert body["ordem"] == "desc"
    assert body["total"] == 5
    valores = [_decimal(item["valor"]) for item in body["itens"]]
    assert valores == sorted(valores, reverse=True)
    assert sum(item["cod_ibge"] == benchmark_data.selecionado for item in body["itens"]) == 1
    selecionado = next(
        item for item in body["itens"] if item["cod_ibge"] == benchmark_data.selecionado
    )
    assert selecionado["destaque"] is True
    assert all(
        item["destaque"] is False
        for item in body["itens"]
        if item["cod_ibge"] != benchmark_data.selecionado
    )
    assert body["ente_ancora"]["cod_ibge"] == benchmark_data.selecionado
    assert body["ente_ancora"]["destaque"] is True

    por_codigo = client.get(
        "/benchmark/ranking",
        params={
            **_params(benchmark_data, "porte:pequeno"),
            "ordenar_por": "cod_ibge",
            "ordem": "desc",
        },
        headers=auth_header(token),
    )
    assert por_codigo.status_code == 200, por_codigo.text
    codigos = [item["cod_ibge"] for item in por_codigo.json()["itens"]]
    assert codigos == sorted(codigos, reverse=True)


def test_benchmark_valida_escopo_do_ente(
    client, make_org, benchmark_data: BenchmarkData
) -> None:
    token = _token(client, make_org, benchmark_data.outro_escopo)
    response = client.get(
        "/benchmark",
        params=_params(benchmark_data, "porte:pequeno"),
        headers=auth_header(token),
    )
    assert response.status_code == 403, response.text


def test_as_of_reproduz_versao_source_ref_memoria_e_percentil(
    client, make_org, benchmark_data: BenchmarkData
) -> None:
    token = _token(client, make_org, benchmark_data.selecionado)
    cedo = client.get(
        "/benchmark",
        params=_params(benchmark_data, "porte:pequeno"),
        headers=auth_header(token),
    )
    tarde = client.get(
        "/benchmark",
        params={
            **_params(benchmark_data, "porte:pequeno"),
            "as_of": AS_OF_V2.isoformat(),
        },
        headers=auth_header(token),
    )
    assert cedo.status_code == 200, cedo.text
    assert tarde.status_code == 200, tarde.text
    anterior, retificado = cedo.json(), tarde.json()

    assert _decimal(anterior["ente"]["valor"]) == Decimal("20")
    assert _decimal(anterior["ente"]["percentil"]) == Decimal("25")
    assert anterior["ente"]["source_ref"]["versao_entrega"] == "RGF:v1;RREO:v1"

    # Após a retificação: [10, 20, 35, 40, 60] => rank 3 de 5 => 50%.
    assert _decimal(retificado["ente"]["valor"]) == Decimal("35")
    assert _decimal(retificado["ente"]["percentil"]) == Decimal("50")
    assert retificado["ente"]["source_ref"]["versao_entrega"] == "RGF:v2;RREO:v2"
    assert retificado["as_of"].startswith("2025-07-01")
    assert retificado["ente"]["as_of"].startswith("2025-07-01")
    assert retificado["memoria"]
    assert retificado["ente"]["memoria"]
    assert retificado["source_refs"]


def test_as_of_seleciona_par_composto_apos_retificacao_independente_do_rgf(
    client, make_org, benchmark_data: BenchmarkData
) -> None:
    token = _token(client, make_org, benchmark_data.selecionado)
    base = _params(benchmark_data, "porte:pequeno")

    # Ordem inversa evita um falso positivo causado por projecao mutavel em cache/mart.
    requests = (
        (AS_OF_V3, Decimal("45"), "v3", "v2"),
        (AS_OF_V1, Decimal("20"), "v1", "v1"),
        (AS_OF_V2, Decimal("35"), "v2", "v2"),
        (AS_OF_V3, Decimal("45"), "v3", "v2"),
    )
    for cutoff, expected_value, expected_rgf, expected_rreo in requests:
        response = client.get(
            "/benchmark",
            params={**base, "as_of": cutoff.isoformat()},
            headers=auth_header(token),
        )
        assert response.status_code == 200, response.text
        point = response.json()["ente"]
        assert _decimal(point["valor"]) == expected_value
        components = {
            item["relatorio"]: item["versao_entrega"]
            for item in point["memoria"]["source_refs_componentes"]
        }
        assert components == {"RGF": expected_rgf, "RREO": expected_rreo}


def test_as_of_anterior_a_primeira_entrega_nao_vaza_mart_futuro(
    client, make_org, benchmark_data: BenchmarkData
) -> None:
    token = _token(client, make_org, benchmark_data.selecionado)
    response = client.get(
        "/benchmark",
        params={
            **_params(benchmark_data, "porte:pequeno"),
            "as_of": AS_OF_ANTES_DA_PRIMEIRA_ENTREGA.isoformat(),
        },
        headers=auth_header(token),
    )

    assert response.status_code == 404, response.text
    assert "reproduz" in response.json()["detail"].lower()


def test_snapshot_e_idempotente_entre_distribuicao_e_ranking(
    client, make_org, benchmark_data: BenchmarkData
) -> None:
    token = _token(client, make_org, benchmark_data.selecionado)
    params = {
        "ente": benchmark_data.selecionado,
        "indicador": INDICADOR,
        "periodo": PERIODO,
        "coorte": "porte:pequeno",
    }
    primeira = client.get("/benchmark", params=params, headers=auth_header(token))
    assert primeira.status_code == 200, primeira.text
    body = primeira.json()
    digest = body["memoria"]["snapshot_hash"]

    with SessionLocal() as session:
        before = list(
            session.scalars(
                select(MartBenchmark).where(MartBenchmark.snapshot_hash == digest)
            )
        )
        audit_before = {
            item.cod_ibge: (item.as_of, item.calculado_em) for item in before
        }

    segunda = client.get(
        "/benchmark/ranking",
        params={**params, "as_of": body["as_of"]},
        headers=auth_header(token),
    )
    assert segunda.status_code == 200, segunda.text
    assert segunda.json()["memoria"]["snapshot_hash"] == digest
    assert segunda.json()["memoria"]["snapshot_reutilizado"] is True

    with SessionLocal() as session:
        after = list(
            session.scalars(
                select(MartBenchmark).where(MartBenchmark.snapshot_hash == digest)
            )
        )
        audit_after = {
            item.cod_ibge: (item.as_of, item.calculado_em) for item in after
        }

    assert len(after) == len(before) == body["quantidade"]
    assert audit_after == audit_before


def test_periodo_implicito_ignora_periodo_mais_novo_ainda_futuro_no_as_of(
    client, make_org, benchmark_data: BenchmarkData
) -> None:
    token = _token(client, make_org, benchmark_data.selecionado)
    future_period = "2100-B6"
    future_rgf_period = "2100-Q3"
    future_version = "future-v1"
    components = (
        SourceRef(
            relatorio="RGF",
            anexo="Anexo 01",
            periodo=future_rgf_period,
            versao_entrega=future_version,
        ),
        SourceRef(
            relatorio="RREO",
            anexo="Anexo 03",
            periodo=future_period,
            versao_entrega=future_version,
        ),
    )
    composite = composite_version_key(components)
    try:
        with SessionLocal() as session:
            for relatorio, source_period in (
                ("RGF", future_rgf_period),
                ("RREO", future_period),
            ):
                session.add(
                    DimEntrega(
                        cod_ibge=benchmark_data.selecionado,
                        relatorio=relatorio,
                        periodo=source_period,
                        versao_entrega=future_version,
                        homologada_em=datetime(2026, 1, 10, tzinfo=UTC),
                        vigente=True,
                        hash_payload=f"future-{relatorio}",
                    )
                )
            session.add(
                MartIndicador(
                    cod_ibge=benchmark_data.selecionado,
                    periodo=future_period,
                    indicador=INDICADOR,
                    valor_rs=Decimal("99000000"),
                    valor_pct_rcl=Decimal("99"),
                    faixa="excedido",
                    teto_pct=Decimal("54"),
                    versao_entrega=composite,
                    source_ref={
                        "relatorio": "RGF/RREO",
                        "anexo": "RGF Anexo 01 / RREO Anexo 03",
                        "periodo": f"{future_rgf_period} / {future_period}",
                        "versao_entrega": "RGF:future-v1;RREO:future-v1",
                        "source_refs_componentes": [
                            item.model_dump(mode="json", exclude_none=True)
                            for item in components
                        ],
                        "chave_versao_composta": composite,
                        "tipo_registro": "historico_composto",
                    },
                )
            )
            session.commit()

        response = client.get(
            "/benchmark",
            params={
                "ente": benchmark_data.selecionado,
                "indicador": INDICADOR,
                "coorte": "porte:pequeno",
                "as_of": AS_OF_V1.isoformat(),
            },
            headers=auth_header(token),
        )
        assert response.status_code == 200, response.text
        assert response.json()["periodo"] == PERIODO
    finally:
        with SessionLocal() as session:
            session.execute(
                delete(MartIndicador).where(
                    MartIndicador.cod_ibge == benchmark_data.selecionado,
                    MartIndicador.periodo == future_period,
                )
            )
            session.execute(
                delete(DimEntrega).where(
                    DimEntrega.cod_ibge == benchmark_data.selecionado,
                    DimEntrega.versao_entrega == future_version,
                )
            )
            session.commit()


def test_membership_de_porte_resolve_ibge_disponivel_no_as_of(
    client, make_org, benchmark_data: BenchmarkData
) -> None:
    token = _token(client, make_org, benchmark_data.selecionado)
    peer = benchmark_data.outro_escopo
    future_version = "benchmark-ibge-future"
    future_cutoff = datetime(2026, 2, 1, tzinfo=UTC)
    try:
        with SessionLocal() as session:
            session.add(
                IbgePopulacao(
                    cod_ibge=peer,
                    ano_ref=2025,
                    populacao=250_000,
                    fonte="fixture isolada",
                    valid_time=date(2025, 12, 31),
                    versao_entrega=future_version,
                )
            )
            session.add(
                DimEntrega(
                    cod_ibge=peer,
                    relatorio="IBGE-POP",
                    periodo="2025",
                    versao_entrega=future_version,
                    homologada_em=datetime(2026, 1, 10, tzinfo=UTC),
                    vigente=True,
                    hash_payload="benchmark-ibge-pop-future",
                )
            )
            current = session.get(DimEnte, peer)
            assert current is not None
            current.populacao = 250_000
            current.pop_source_ref = {
                "relatorio": "IBGE-POP",
                "anexo": "Agregado 6579 - variavel 9324",
                "periodo": "2025",
                "versao_entrega": future_version,
            }
            session.commit()

        def ranking_at(cutoff: datetime) -> dict[str, Any]:
            response = client.get(
                "/benchmark/ranking",
                params={
                    "ente": benchmark_data.selecionado,
                    "indicador": INDICADOR,
                    "periodo": PERIODO,
                    "coorte": "porte:pequeno",
                    "as_of": cutoff.isoformat(),
                },
                headers=auth_header(token),
            )
            assert response.status_code == 200, response.text
            return response.json()

        historical = ranking_at(AS_OF_V1)
        after_update = ranking_at(future_cutoff)
        assert peer in {item["cod_ibge"] for item in historical["itens"]}
        assert peer not in {item["cod_ibge"] for item in after_update["itens"]}
        assert historical["total"] == 5
        assert after_update["total"] == 4
    finally:
        with SessionLocal() as session:
            session.execute(
                delete(IbgePopulacao).where(
                    IbgePopulacao.cod_ibge == peer,
                    IbgePopulacao.versao_entrega == future_version,
                )
            )
            session.execute(
                delete(DimEntrega).where(
                    DimEntrega.cod_ibge == peer,
                    DimEntrega.relatorio == "IBGE-POP",
                    DimEntrega.versao_entrega == future_version,
                )
            )
            current = session.get(DimEnte, peer)
            if current is not None:
                current.populacao = 40_000
                current.pop_source_ref = {
                    "relatorio": "IBGE-POP",
                    "anexo": "Agregado 6579 - variavel 9324",
                    "periodo": "2025",
                    "versao_entrega": "benchmark-ibge-v1",
                }
            session.commit()


def test_definicao_ajustavel_da_coorte_e_reproduzida_no_as_of(
    client, make_org, benchmark_data: BenchmarkData
) -> None:
    token = _token(client, make_org, benchmark_data.selecionado)
    coorte_id = uuid.uuid4()
    codigo = f"porte:test-{coorte_id.hex[:10]}"
    initial_cutoff: datetime | None = None
    try:
        with SessionLocal() as session:
            coorte = DimCoorte(
                id=coorte_id,
                codigo=codigo,
                criterio="porte",
                faixa=f"test-{coorte_id.hex[:10]}",
                rotulo="Coorte temporal de teste",
                unidade_criterio="habitantes",
                limite_inferior=Decimal(0),
                limite_superior=Decimal(50_000),
                inclusivo_superior=False,
                ordem=999,
                ativo=True,
                source_ref={
                    "relatorio": "CONFIGURACAO-COORTE",
                    "anexo": "porte",
                    "versao_entrega": "test-v1",
                },
            )
            session.add(coorte)
            session.commit()
            session.refresh(coorte)
            initial_cutoff = coorte.atualizado_em + timedelta(microseconds=1)

        before = client.get(
            "/benchmark",
            params={
                "ente": benchmark_data.selecionado,
                "indicador": INDICADOR,
                "periodo": PERIODO,
                "coorte": codigo,
                "as_of": initial_cutoff.isoformat(),
            },
            headers=auth_header(token),
        )
        assert before.status_code == 200, before.text
        assert before.json()["coorte"]["limite_superior"] == "50000"

        with SessionLocal() as session:
            coorte = session.get(DimCoorte, coorte_id)
            assert coorte is not None
            coorte.limite_superior = Decimal(10_000)
            coorte.source_ref = {
                "relatorio": "CONFIGURACAO-COORTE",
                "anexo": "porte",
                "versao_entrega": "test-v2",
            }
            session.commit()

        reproduced = client.get(
            "/benchmark",
            params={
                "ente": benchmark_data.selecionado,
                "indicador": INDICADOR,
                "periodo": PERIODO,
                "coorte": codigo,
                "as_of": initial_cutoff.isoformat(),
            },
            headers=auth_header(token),
        )
        assert reproduced.status_code == 200, reproduced.text
        assert reproduced.json()["coorte"]["limite_superior"] == "50000"
    finally:
        with SessionLocal() as session:
            session.execute(
                delete(MartBenchmark).where(MartBenchmark.coorte_id == coorte_id)
            )
            coorte = session.get(DimCoorte, coorte_id)
            if coorte is not None:
                session.delete(coorte)
            session.commit()
