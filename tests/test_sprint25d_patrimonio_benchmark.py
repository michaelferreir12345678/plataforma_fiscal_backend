"""Aceites da Sprint 25D — patrimônio sem seletor-demo e benchmarking multi-indicador.

Dados sintéticos em exercícios exclusivos; o cleanup filtra estritamente os códigos
criados aqui para não tocar na carga real do banco local.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.modules.accounting.models import FatoBalanco
from app.modules.benchmark.models import MartBenchmark
from app.modules.catalog.models import DimEnte
from app.modules.expense.models import FatoDespesa
from app.modules.indicators import gerenciais
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import (
    DimEntrega,
    IbgePib,
    IbgePopulacao,
    SilverDca,
    SilverEnte,
)
from app.modules.result.models import FatoResultado
from tests.conftest import auth_header, login

ANO = 2088
PERIODOS = (f"{ANO}-B2", f"{ANO}-B4", f"{ANO}-B6", f"{ANO + 1}-B2")
RREO_V = "s25d-rreo-v1"
DCA_V = "s25d-dca-v1"
ANOS_DCA = (ANO - 2, ANO - 1, ANO)
HOMOLOGADA_BASE = datetime(2089, 2, 1, tzinfo=UTC)
HOMOLOGADA_ULTIMO = datetime(2090, 2, 1, tzinfo=UTC)
AS_OF_ANTES_DO_ULTIMO = datetime(2089, 6, 1, tzinfo=UTC)


def _codigos(quantidade: int) -> tuple[str, ...]:
    while True:
        inicio = 9_200_000 + uuid.uuid4().int % 600_000
        codigos = tuple(str(inicio + i) for i in range(quantidade))
        with SessionLocal() as session:
            existe = session.scalar(
                select(DimEnte.cod_ibge).where(DimEnte.cod_ibge.in_(codigos)).limit(1)
            )
        if existe is None:
            return codigos


def _cadastrar_ente(session: Any, cod: str, *, nome: str, populacao: int) -> None:
    ibge_v = "s25d-ibge-v1"
    session.add(
        SilverEnte(
            cod_ibge=cod, nome=nome, uf="CE", esfera="M", populacao=populacao,
            regiao="NE", capital=False, versao_entrega=ibge_v,
        )
    )
    session.add(
        IbgePopulacao(
            cod_ibge=cod, ano_ref=ANO, populacao=populacao, fonte="fixture 25D",
            valid_time=date(ANO, 12, 31), versao_entrega=ibge_v,
        )
    )
    session.add(
        IbgePib(
            cod_ibge=cod, ano_ref=ANO, pib_nominal=Decimal(populacao) * 25,
            pib_per_capita=None, valid_time=date(ANO, 12, 31), versao_entrega=ibge_v,
        )
    )
    for relatorio in ("IBGE-POP", "IBGE-PIB"):
        session.add(
            DimEntrega(
                cod_ibge=cod, relatorio=relatorio, periodo=str(ANO), versao_entrega=ibge_v,
                homologada_em=datetime(2024, 1, 1, tzinfo=UTC), vigente=True,
                hash_payload=f"s25d-{relatorio}-{cod}",
            )
        )
    session.add(
        DimEnte(
            cod_ibge=cod, nome=nome, esfera="municipal", populacao=populacao, rpps=False,
            possui_tcm=False, uf="CE", regiao="NE", pib=Decimal(populacao) * 25,
            pop_ano_ref=ANO, pib_ano_ref=ANO,
        )
    )


def _fatos_rreo(session: Any, cod: str, *, rcl: int, investimento: int, primario: int) -> None:
    for periodo in PERIODOS:
        session.add(
            DimEntrega(
                cod_ibge=cod, relatorio="RREO", periodo=periodo,
                versao_entrega=RREO_V,
                # O último período é homologado um ano depois: permite provar a leitura
                # bitemporal (as_of) de uma janela em que nem todo período existia.
                homologada_em=(
                    HOMOLOGADA_ULTIMO if periodo == PERIODOS[-1] else HOMOLOGADA_BASE
                ),
                vigente=True,
            )
        )
        session.add(
            FatoRcl(
                cod_ibge=cod, periodo_ref=periodo, rcl_12m=Decimal(rcl),
                deducoes=Decimal(0), receita_corrente=Decimal(rcl),
                versao_entrega=RREO_V, memoria={},
            )
        )
        session.add(
            FatoDespesa(
                # Eixo natureza: a função recebe a sentinela '*' (padrão da Sprint 6).
                cod_ibge=cod, periodo=periodo, funcao_codigo="*",
                natureza_codigo="Investimentos",
                dotacao_inicial=Decimal(investimento), dotacao_atualizada=Decimal(investimento),
                empenhado=Decimal(investimento), liquidado=Decimal(investimento),
                pago=Decimal(investimento), inscrito_rap=Decimal(0),
                versao_entrega=RREO_V,
            )
        )
        session.add(
            FatoResultado(
                cod_ibge=cod, periodo=periodo, receita_primaria=Decimal(rcl),
                despesa_primaria=Decimal(rcl - primario),
                resultado_primario=Decimal(primario),
                versao_entrega=RREO_V,
            )
        )


def _balancos(session: Any, cod: str, *, ativo_por_ano: dict[int, int]) -> None:
    """Semeia a **silver** da DCA: a gold é materializada pelo próprio serviço."""
    for ano, ativo in ativo_por_ano.items():
        session.add(
            DimEntrega(
                cod_ibge=cod, relatorio="DCA", periodo=str(ano), versao_entrega=DCA_V,
                homologada_em=datetime(ano + 1, 4, 30, tzinfo=UTC), vigente=True,
            )
        )
        linhas = (
            ("P1.0.0.0.0.00.00", "1.0.0.0.0.00.00 - Ativo", ativo),
            ("P2.0.0.0.0.00.00", "2.0.0.0.0.00.00 - Passivo e Patrimônio Líquido", ativo),
            ("P2.3.0.0.0.00.00", "2.3.0.0.0.00.00 - Patrimônio Líquido", int(ativo * 0.4)),
        )
        for seq, (cod_conta, conta, valor) in enumerate(linhas):
            session.add(
                SilverDca(
                    cod_ibge=cod, periodo=str(ano), anexo="DCA-Anexo I-AB",
                    conta=conta, cod_conta=cod_conta, coluna=f"31/12/{ano}",
                    linha_seq=seq, valor=Decimal(valor), versao_entrega=DCA_V,
                )
            )


@pytest.fixture
def cenario() -> Iterator[tuple[str, ...]]:
    """Quatro municípios do mesmo porte, com RREO em 4 períodos e DCA em 3 exercícios."""
    codigos = _codigos(4)
    perfis = (
        # (rcl, investimento, resultado primário, população)
        (1_000_000, 100_000, 50_000, 400_000),
        (2_000_000, 400_000, -20_000, 500_000),
        (1_500_000, 150_000, 10_000, 300_000),
        (3_000_000, 900_000, 90_000, 600_000),
    )
    with SessionLocal() as session:
        for indice, (cod, (rcl, inv, prim, pop)) in enumerate(
            zip(codigos, perfis, strict=True)
        ):
            _cadastrar_ente(session, cod, nome=f"Município 25D {indice}", populacao=pop)
            _fatos_rreo(session, cod, rcl=rcl, investimento=inv, primario=prim)
        # Só o primeiro ente tem DCA — os demais provam a cobertura honesta.
        _balancos(
            session, codigos[0],
            ativo_por_ano={ANOS_DCA[0]: 1_000_000, ANOS_DCA[1]: 1_200_000, ANOS_DCA[2]: 1_500_000},
        )
        session.commit()
    yield codigos
    with SessionLocal() as session:
        for modelo in (
            MartBenchmark, MartIndicador, FatoBalanco, FatoResultado, FatoDespesa, FatoRcl,
            SilverDca, DimEntrega, IbgePib, IbgePopulacao, DimEnte, SilverEnte,
        ):
            session.execute(delete(modelo).where(modelo.cod_ibge.in_(codigos)))
        session.commit()


def _headers(client, make_org, codigos: tuple[str, ...]) -> dict[str, str]:
    org = make_org(capacidades=["ver"], entes=list(codigos))
    return auth_header(login(client, org.email, org.senha))


def _materializar(codigos: tuple[str, ...]) -> None:
    with SessionLocal() as session:
        for cod in codigos:
            for periodo in PERIODOS:
                gerenciais.materializar_gerenciais(session, cod, periodo)
        session.commit()


# --------------------------------------------------------------------------- #
# Indicadores gerenciais no mart
# --------------------------------------------------------------------------- #
def test_indicadores_gerenciais_entram_no_mart_sem_faixa_nem_teto(cenario) -> None:
    """Sem limite legal não há faixa: inventar 'normal' afirmaria conformidade inexistente."""
    _materializar(cenario)
    with SessionLocal() as session:
        linhas = {
            row.indicador: row
            for row in session.scalars(
                select(MartIndicador).where(
                    MartIndicador.cod_ibge == cenario[0],
                    MartIndicador.periodo == PERIODOS[2],
                )
            )
        }
    assert set(gerenciais.INDICADORES_GERENCIAIS) <= set(linhas)

    rcl_pc = linhas["rcl_per_capita"]
    assert rcl_pc.faixa is None and rcl_pc.teto_pct is None
    assert rcl_pc.denominador == "populacao"
    assert Decimal(rcl_pc.valor_rs) == Decimal(1_000_000) / Decimal(400_000)  # R$ 2,50/hab
    assert rcl_pc.valor_pct_rcl is None  # não é percentual de coisa alguma

    investimento = linhas["investimento_rcl"]
    assert investimento.denominador == "rcl"
    assert Decimal(investimento.valor_pct_rcl) == Decimal(10)  # 100k / 1M
    assert investimento.faixa is None

    primario = linhas["resultado_primario_rcl"]
    assert Decimal(primario.valor_pct_rcl) == Decimal(5)


def test_indicador_sem_insumo_nao_vira_linha_zerada(cenario) -> None:
    """Zero e 'não publicou' não são a mesma coisa num ranking."""
    cod = cenario[1]
    with SessionLocal() as session:
        session.execute(
            delete(FatoDespesa).where(
                FatoDespesa.cod_ibge == cod, FatoDespesa.periodo == PERIODOS[0]
            )
        )
        session.commit()
        gravados = gerenciais.materializar_gerenciais(session, cod, PERIODOS[0])
        session.commit()
    assert "investimento_rcl" not in gravados
    assert "rcl_per_capita" in gravados
    with SessionLocal() as session:
        linha = session.scalar(
            select(MartIndicador).where(
                MartIndicador.cod_ibge == cod,
                MartIndicador.periodo == PERIODOS[0],
                MartIndicador.indicador == "investimento_rcl",
            )
        )
    assert linha is None


# --------------------------------------------------------------------------- #
# Benchmarking: cobertura de indicadores e multi-período
# --------------------------------------------------------------------------- #
def test_benchmark_cobre_os_indicadores_gerenciais_com_unidade_propria(
    client, make_org, cenario
) -> None:
    _materializar(cenario)
    headers = _headers(client, make_org, cenario)

    rcl_pc = client.get(
        "/benchmark",
        params={"ente": cenario[0], "indicador": "rcl_per_capita",
                "periodo": PERIODOS[2], "coorte": "porte"},
        headers=headers,
    )
    assert rcl_pc.status_code == 200, rcl_pc.text
    body = rcl_pc.json()
    # R$/hab não é "brl": o eixo precisa dizer por habitante.
    assert body["unidade"] == "brl_per_capita"
    assert body["cobertura"]["entes_com_valor"] == 4
    # 2,50 (400k hab) é o menor da coorte: 2,50 / 4,00 / 5,00 / 5,00.
    assert float(body["ente"]["valor"]) == 2.5
    assert body["ente"]["posicao"] == 1

    investimento = client.get(
        "/benchmark",
        params={"ente": cenario[0], "indicador": "investimento_rcl",
                "periodo": PERIODOS[2], "coorte": "porte"},
        headers=headers,
    ).json()
    assert investimento["unidade"] == "percentual_rcl"
    assert investimento["sentido"] == "neutro"  # não há limite legal para investimento
    assert float(investimento["distribuicao"]["mediana"]) == 15.0  # 10, 10, 20, 30


def test_valor_per_capita_acompanha_metricas_em_reais(client, make_org, cenario) -> None:
    """Uma métrica em R$ ganha leitura por habitante; um percentual, não."""
    _materializar(cenario)
    headers = _headers(client, make_org, cenario)
    with SessionLocal() as session:
        # Um indicador em R$ puro (sem percentual e sem denominador populacional).
        session.add(
            MartIndicador(
                cod_ibge=cenario[0], periodo=PERIODOS[2], indicador="despesa_total_brl",
                valor_rs=Decimal(800_000), valor_pct_rcl=None, faixa=None, teto_pct=None,
                denominador="nao_aplicavel", base_valor=None, versao_entrega=RREO_V,
                source_ref={"relatorio": "RREO", "periodo": PERIODOS[2], "versao_entrega": RREO_V},
            )
        )
        session.commit()
    resposta = client.get(
        "/benchmark",
        params={"ente": cenario[0], "indicador": "despesa_total_brl",
                "periodo": PERIODOS[2], "coorte": "porte"},
        headers=headers,
    )
    assert resposta.status_code == 200, resposta.text
    ente = resposta.json()["ente"]
    assert float(ente["valor_per_capita"]) == 2.0  # 800.000 / 400.000 hab
    assert ente["populacao"] == 400_000

    percentual = client.get(
        "/benchmark",
        params={"ente": cenario[0], "indicador": "investimento_rcl",
                "periodo": PERIODOS[2], "coorte": "porte"},
        headers=headers,
    ).json()
    assert percentual["ente"]["valor_per_capita"] is None


def test_evolucao_multi_periodo_mantem_a_mesma_coorte(client, make_org, cenario) -> None:
    _materializar(cenario)
    headers = _headers(client, make_org, cenario)
    resposta = client.get(
        "/benchmark/evolucao",
        params={"ente": cenario[0], "indicador": "investimento_rcl",
                "coorte": "porte", "periodos": 6},
        headers=headers,
    )
    assert resposta.status_code == 200, resposta.text
    body = resposta.json()
    assert [p["periodo"] for p in body["pontos"]] == list(PERIODOS)
    assert len({p["periodo"] for p in body["pontos"]}) == 4  # ≥4 períodos: aceite da 25D
    assert body["memoria"]["coorte_fixada_em"] == body["coorte"]["codigo"]
    for ponto in body["pontos"]:
        assert ponto["quantidade"] == 4
        assert ponto["posicao"] >= 1
        assert ponto["cobertura"]["entes_com_valor"] == 4


def test_evolucao_declara_periodo_sem_comparacao_em_vez_de_interpolar(
    client, make_org, cenario
) -> None:
    """Um período que ainda não era reproduzível no ``as_of`` não vira ponto."""
    _materializar(cenario)
    resposta = client.get(
        "/benchmark/evolucao",
        params={
            "ente": cenario[0], "indicador": "investimento_rcl", "coorte": "porte",
            "as_of": AS_OF_ANTES_DO_ULTIMO.isoformat(),
        },
        headers=_headers(client, make_org, cenario),
    )
    assert resposta.status_code == 200, resposta.text
    body = resposta.json()
    # O último período só foi homologado depois do as_of consultado.
    assert PERIODOS[-1] not in [p["periodo"] for p in body["pontos"]]
    assert body["periodos_sem_comparacao"] == [PERIODOS[-1]]
    assert len(body["pontos"]) == 3
    assert "não são interpolados" in (body["observacao"] or "")


def test_evolucao_ignora_periodo_que_o_ente_nao_publicou(client, make_org, cenario) -> None:
    """Sem linha no mart o período nem entra na janela — a série encolhe, não inventa."""
    _materializar(cenario)
    with SessionLocal() as session:
        session.execute(
            delete(MartIndicador).where(
                MartIndicador.cod_ibge.in_(cenario),
                MartIndicador.periodo == PERIODOS[1],
                MartIndicador.indicador == "investimento_rcl",
            )
        )
        session.commit()
    body = client.get(
        "/benchmark/evolucao",
        params={"ente": cenario[0], "indicador": "investimento_rcl", "coorte": "porte"},
        headers=_headers(client, make_org, cenario),
    ).json()
    assert [p["periodo"] for p in body["pontos"]] == [PERIODOS[0], PERIODOS[2], PERIODOS[3]]


# --------------------------------------------------------------------------- #
# Patrimônio: cobertura honesta (sem seletor-demo) e comparação anual
# --------------------------------------------------------------------------- #
def test_patrimonio_de_ente_sem_fonte_responde_cobertura_e_nao_erro(
    client, make_org, cenario
) -> None:
    """Antes isto era 404, e a tela reagia trocando o ente por um 'de demonstração'."""
    cod = cenario[1]  # sem DCA e sem MSC
    resposta = client.get(
        f"/entes/{cod}/patrimonio", headers=_headers(client, make_org, cenario)
    )
    assert resposta.status_code == 200, resposta.text
    body = resposta.json()
    assert body["cod_ibge"] == cod  # o ente consultado é o ente devolvido
    assert body["tem_dca"] is False and body["tem_msc"] is False
    assert body["ativo"] is None  # ausência não é zero
    assert set(body["cobertura"]["fontes_ausentes"]) == {"DCA", "MSC"}
    assert "Nenhuma fonte patrimonial ingerida" in body["cobertura"]["mensagem"]


def test_patrimonio_com_dca_e_sem_msc_explica_o_que_falta(client, make_org, cenario) -> None:
    resposta = client.get(
        f"/entes/{cenario[0]}/patrimonio", headers=_headers(client, make_org, cenario)
    )
    assert resposta.status_code == 200, resposta.text
    cobertura = resposta.json()["cobertura"]
    assert cobertura["tem_dca"] is True and cobertura["tem_msc"] is False
    assert cobertura["fontes_ausentes"] == ["MSC"]
    assert cobertura["anos_dca"] == list(ANOS_DCA)
    assert "não** publica a MSC" in cobertura["mensagem"]


def test_comparacao_anual_de_balancos_traz_variacao_e_lacunas(client, make_org, cenario) -> None:
    resposta = client.get(
        f"/entes/{cenario[0]}/balancos/comparacao",
        params={"tipo": "patrimonial", "anos": 4},
        headers=_headers(client, make_org, cenario),
    )
    assert resposta.status_code == 200, resposta.text
    body = resposta.json()
    assert body["anos"] == list(ANOS_DCA)
    ativo = next(
        linha for linha in body["linhas"] if linha["cod_conta"] == "1.0.0.0.0.00.00"
    )
    assert float(ativo["valores"][str(ANOS_DCA[0])]) == 1_000_000
    assert float(ativo["valores"][str(ANOS_DCA[2])]) == 1_500_000
    assert float(ativo["variacao_abs"]) == 500_000
    assert float(ativo["variacao_pct"]) == 50.0
    assert "não é zero" in body["memoria"]["ausencia"]


def test_comparacao_recusa_ente_sem_dca(client, make_org, cenario) -> None:
    resposta = client.get(
        f"/entes/{cenario[1]}/balancos/comparacao",
        params={"tipo": "patrimonial"},
        headers=_headers(client, make_org, cenario),
    )
    assert resposta.status_code == 404
    assert "não tem DCA ingerida" in resposta.json()["detail"]
