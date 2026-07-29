"""Aceites da Sprint 25C — mínimos no mart, série plurianual, coorte e alerta de risco.

Os dados são sintéticos e vivem em exercícios futuros exclusivos; o cleanup filtra
estritamente os códigos criados aqui, para não tocar na carga real do banco local.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal, admin_session, apply_context
from app.modules.alerts import engine
from app.modules.alerts.models import Alerta
from app.modules.benchmark.models import MartBenchmark
from app.modules.cash_rap.models import FatoDisponibilidade
from app.modules.catalog.models import DimEnte
from app.modules.health_edu import service as health_edu_service
from app.modules.health_edu.models import FatoEducacao, FatoSaude, FatoSaudeSubfuncao
from app.modules.indicators.models import MartIndicador
from app.modules.ingestion.models import (
    DimEntrega,
    IbgePib,
    IbgePopulacao,
    SilverEnte,
    SilverRgf,
    SilverRreo,
)
from tests.conftest import auth_header, login

ANO_ANTERIOR = 2091
ANO = 2092
FECHADO = f"{ANO_ANTERIOR}-B6"
FECHADO_RGF = f"{ANO_ANTERIOR}-Q3"
PARCIAL = f"{ANO}-B4"
RREO_V = "s25c-rreo-v1"
RGF_V = "s25c-rgf-v1"

BRUTA = "DISPONIBILIDADE DE CAIXA BRUTA (a)"
ANTES = (
    "DISPONIBILIDADE DE CAIXA LÍQUIDA (ANTES DA INSCRIÇÃO EM RESTOS A PAGAR NÃO "
    "PROCESSADOS DO EXERCÍCIO) (f)=(a-(b+c+d+e))"
)
RPNP = "RESTOS A PAGAR EMPENHADOS E NÃO LIQUIDADOS DO EXERCÍCIO (g)"


def _codigos(quantidade: int) -> tuple[str, ...]:
    while True:
        inicio = 9_100_000 + uuid.uuid4().int % 700_000
        codigos = tuple(str(inicio + i) for i in range(quantidade))
        with SessionLocal() as session:
            existe = session.scalar(
                select(DimEnte.cod_ibge).where(DimEnte.cod_ibge.in_(codigos)).limit(1)
            )
        if existe is None:
            return codigos


def _rreo_rows(
    cod: str,
    periodo: str,
    *,
    coluna: str,
    asps: str,
    mde: str,
    fundeb: str = "140",
) -> list[SilverRreo]:
    """Anexos 12/08 de um período. ``asps``/``mde`` sobre base 1000 => percentual direto."""
    def linha(anexo: str, codigo: str, valor: str, seq: int, col: str = "VALOR") -> SilverRreo:
        return SilverRreo(
            cod_ibge=cod, periodo=periodo, anexo=anexo,
            conta=codigo.replace("_", " "), cod_conta=codigo, coluna=col,
            linha_seq=seq, valor=Decimal(valor), versao_entrega=RREO_V,
        )

    return [
        linha("RREO-Anexo 12", "ASPS_BASE_IMPOSTOS_TRANSFERENCIAS", "1000", 1),
        linha("RREO-Anexo 12", "ASPS_DESPESA_TOTAL", asps, 2, coluna),
        linha("RREO-Anexo 12", "ASPS_DEDUCOES_OUTRAS", "0", 3, coluna),
        linha("RREO-Anexo 12", "ASPS_RPNP_SEM_LASTRO_REPORTADO", "0", 4),
        linha("RREO-Anexo 12", "ASPS_SUBFUNCAO_ATENCAO_BASICA", "100", 10, coluna),
        linha("RREO-Anexo 08", "MDE_BASE_IMPOSTOS_TRANSFERENCIAS", "1000", 1),
        linha("RREO-Anexo 08", "MDE_DESPESA_IMPOSTOS", mde, 2, coluna),
        linha("RREO-Anexo 08", "MDE_TRANSFERENCIA_FUNDEB", "0", 3),
        linha("RREO-Anexo 08", "MDE_SUPERAVIT_EXERCICIO_ANTERIOR", "0", 4, coluna),
        linha("RREO-Anexo 08", "MDE_COMPLEMENTACAO_VAAF_EXERCICIO_ANTERIOR", "0", 5, coluna),
        linha("RREO-Anexo 08", "MDE_CANCELAMENTOS", "0", 6, coluna),
        linha("RREO-Anexo 08", "MDE_RPNP_SEM_LASTRO_REPORTADO", "0", 7),
        linha("RREO-Anexo 08", "FUNDEB_BASE_PROFISSIONAIS", "200", 8),
        linha("RREO-Anexo 08", "FUNDEB_PROFISSIONAIS", fundeb, 9, coluna),
    ]


def _rgf_rows(cod: str) -> list[SilverRgf]:
    """Anexo 5 sem RPNP a expurgar — isola o teste na apuração dos mínimos."""
    linhas = []
    for seq, conta, col, valor in (
        (1, "Recursos Vinculados à Saúde", BRUTA, "100"),
        (2, "Recursos Vinculados à Saúde", ANTES, "100"),
        (3, "Recursos Vinculados à Saúde", RPNP, "0"),
        (4, "Recursos Vinculados à Educação", BRUTA, "100"),
        (5, "Recursos Vinculados à Educação", ANTES, "100"),
        (6, "Recursos Vinculados à Educação", RPNP, "0"),
    ):
        linhas.append(
            SilverRgf(
                cod_ibge=cod, periodo=FECHADO_RGF, anexo="RGF-Anexo 05", conta=conta,
                coluna=col, poder="E", linha_seq=seq, valor=Decimal(valor),
                versao_entrega=RGF_V,
            )
        )
    return linhas


def _cadastrar_ente(session: Any, cod: str, *, nome: str, populacao: int) -> None:
    ibge_v = "s25c-ibge-v1"
    session.add(
        SilverEnte(
            cod_ibge=cod, nome=nome, uf="CE", esfera="M", populacao=populacao,
            regiao="NE", capital=False, versao_entrega=ibge_v,
        )
    )
    session.add(
        IbgePopulacao(
            cod_ibge=cod, ano_ref=2025, populacao=populacao, fonte="fixture 25C",
            valid_time=date(2025, 12, 31), versao_entrega=ibge_v,
        )
    )
    session.add(
        IbgePib(
            cod_ibge=cod, ano_ref=2023, pib_nominal=Decimal(populacao) * 20,
            pib_per_capita=None, valid_time=date(2023, 12, 31), versao_entrega=ibge_v,
        )
    )
    for relatorio, periodo in (("IBGE-POP", "2025"), ("IBGE-PIB", "2023")):
        session.add(
            DimEntrega(
                cod_ibge=cod, relatorio=relatorio, periodo=periodo, versao_entrega=ibge_v,
                homologada_em=datetime(2024, 1, 1, tzinfo=UTC), vigente=True,
                hash_payload=f"s25c-{relatorio}-{cod}",
            )
        )
    session.add(
        DimEnte(
            cod_ibge=cod, nome=nome, esfera="municipal", populacao=populacao, rpps=False,
            possui_tcm=False, uf="CE", regiao="NE", pib=Decimal(populacao) * 20,
            pop_ano_ref=2025, pib_ano_ref=2023,
        )
    )


@pytest.fixture
def coorte() -> Iterator[tuple[str, ...]]:
    """Quatro municípios do mesmo porte: um em risco no MDE e três pares."""
    codigos = _codigos(4)
    # ASPS e MDE aplicados (sobre base 1000): o primeiro fecha 2091 abaixo do MDE.
    perfis = (("160", "220"), ("200", "260"), ("180", "300"), ("170", "280"))
    with SessionLocal() as session:
        for indice, (cod, (asps, mde)) in enumerate(zip(codigos, perfis, strict=True)):
            _cadastrar_ente(session, cod, nome=f"Município 25C {indice}", populacao=300_000)
            session.add_all(
                [
                    DimEntrega(
                        cod_ibge=cod, relatorio="RREO", periodo=FECHADO,
                        versao_entrega=RREO_V,
                        homologada_em=datetime(2092, 1, 31, tzinfo=UTC), vigente=True,
                    ),
                    DimEntrega(
                        cod_ibge=cod, relatorio="RGF", periodo=FECHADO_RGF,
                        versao_entrega=RGF_V,
                        homologada_em=datetime(2092, 2, 20, tzinfo=UTC), vigente=True,
                    ),
                    DimEntrega(
                        cod_ibge=cod, relatorio="RREO", periodo=PARCIAL,
                        versao_entrega=RREO_V,
                        homologada_em=datetime(2092, 9, 30, tzinfo=UTC), vigente=True,
                    ),
                ]
            )
            session.add_all(_rgf_rows(cod))
            # Exercício fechado: 6º bimestre, estágio empenhado.
            session.add_all(
                _rreo_rows(
                    cod, FECHADO, coluna="DESPESAS EMPENHADAS", asps=asps, mde=mde
                )
            )
            # Exercício corrente, 4º bimestre (parcial): acumulado ainda abaixo do piso.
            session.add_all(
                _rreo_rows(
                    cod, PARCIAL, coluna="DESPESAS LIQUIDADAS", asps="170", mde="180"
                )
            )
        session.commit()
    yield codigos
    with SessionLocal() as session:
        for modelo in (
            FatoSaudeSubfuncao, FatoSaude, FatoEducacao, FatoDisponibilidade,
            MartBenchmark, MartIndicador, SilverRreo, SilverRgf, DimEntrega,
            IbgePib, IbgePopulacao, DimEnte, SilverEnte,
        ):
            session.execute(delete(modelo).where(modelo.cod_ibge.in_(codigos)))
        session.commit()
    with admin_session() as session:
        session.execute(delete(Alerta).where(Alerta.cod_ibge.in_(codigos)))
        session.commit()


def _headers(client, make_org, codigos: tuple[str, ...]) -> dict[str, str]:
    org = make_org(capacidades=["ver"], entes=list(codigos))
    return auth_header(login(client, org.email, org.senha))


# --------------------------------------------------------------------------- #
# Mínimos no mart — com a base declarada
# --------------------------------------------------------------------------- #
def test_minimos_entram_no_mart_com_a_propria_base_e_nao_como_percentual_da_rcl(
    client, make_org, coorte
) -> None:
    cod = coorte[0]
    headers = _headers(client, make_org, coorte)
    for rota in ("saude", "educacao"):
        resposta = client.get(f"/entes/{cod}/{rota}", params={"periodo": FECHADO}, headers=headers)
        assert resposta.status_code == 200, resposta.text

    with SessionLocal() as session:
        linhas = {
            row.indicador: row
            for row in session.scalars(
                select(MartIndicador).where(
                    MartIndicador.cod_ibge == cod,
                    MartIndicador.periodo == FECHADO,
                    MartIndicador.versao_entrega == RREO_V,
                )
            )
        }
    assert {"saude_minimo", "educacao_mde", "fundeb_profissionais"} <= set(linhas)

    saude = linhas["saude_minimo"]
    assert saude.denominador == "impostos_transferencias"
    assert Decimal(saude.base_valor) == Decimal(1000)
    assert Decimal(saude.valor_pct_rcl) == Decimal(16)  # 160/1000 — nunca sobre a RCL
    assert saude.faixa == "adequado"  # piso municipal de 15%

    mde = linhas["educacao_mde"]
    assert Decimal(mde.valor_pct_rcl) == Decimal(22)
    assert mde.faixa == "insuficiente"  # semântica de piso: 22% < 25%

    fundeb = linhas["fundeb_profissionais"]
    # A base do FUNDEB é o próprio fundo (200), não a base de impostos do MDE (1000).
    assert fundeb.denominador == "fundeb"
    assert Decimal(fundeb.base_valor) == Decimal(200)
    assert Decimal(fundeb.valor_pct_rcl) == Decimal(70)


def test_semaforo_do_dashboard_nao_chama_o_minimo_de_percentual_da_rcl(
    client, make_org, coorte
) -> None:
    cod = coorte[0]
    headers = _headers(client, make_org, coorte)
    client.get(f"/entes/{cod}/educacao", params={"periodo": FECHADO}, headers=headers)
    resposta = client.get(f"/entes/{cod}/dashboard", params={"periodo": FECHADO}, headers=headers)
    assert resposta.status_code == 200, resposta.text
    body = resposta.json()
    mde = next(i for i in body["semaforo"] if i["indicador"] == "educacao_mde")
    assert mde["denominador"] == "impostos_transferencias"
    assert mde["faixa"] == "insuficiente"
    destaque = next(d for d in body["destaques"] if d["indicador"] == "educacao_mde")
    assert "dos impostos e transferências" in destaque["mensagem"]
    assert "da RCL" not in destaque["mensagem"]


def test_limites_expoem_o_denominador_de_cada_indicador(client, make_org, coorte) -> None:
    cod = coorte[0]
    headers = _headers(client, make_org, coorte)
    client.get(f"/entes/{cod}/saude", params={"periodo": FECHADO}, headers=headers)
    resposta = client.get(f"/entes/{cod}/limites", params={"periodo": FECHADO}, headers=headers)
    assert resposta.status_code == 200, resposta.text
    itens = {item["indicador"]: item for item in resposta.json()["itens"]}
    assert itens["saude_minimo"]["denominador"] == "impostos_transferencias"
    assert itens["saude_minimo"]["sentido"] == "piso"
    assert Decimal(itens["saude_minimo"]["base_valor"]) == Decimal(1000)


# --------------------------------------------------------------------------- #
# Série plurianual
# --------------------------------------------------------------------------- #
def test_serie_plurianual_tem_um_ponto_por_exercicio_e_marca_o_parcial(
    client, make_org, coorte
) -> None:
    cod = coorte[0]
    headers = _headers(client, make_org, coorte)
    resposta = client.get(
        f"/entes/{cod}/educacao/serie",
        params={"periodo": PARCIAL, "anos": 5},
        headers=headers,
    )
    assert resposta.status_code == 200, resposta.text
    body = resposta.json()
    assert body["indicador"] == "educacao"
    assert [item["exercicio"] for item in body["data"]] == [ANO_ANTERIOR, ANO]

    fechado, corrente = body["data"]
    assert fechado["periodo"] == FECHADO
    assert fechado["parcial"] is False
    assert fechado["estagio_legal"] == "empenhado"
    assert float(fechado["pct_aplicado"]) == 22
    # O exercício corrente entra pelo período consultado, explicitamente parcial.
    assert corrente["periodo"] == PARCIAL
    assert corrente["parcial"] is True
    assert corrente["estagio_legal"] == "liquidado"


def test_serie_declara_os_exercicios_sem_dado_em_vez_de_omiti_los(
    client, make_org, coorte
) -> None:
    cod = coorte[0]
    resposta = client.get(
        f"/entes/{cod}/saude/serie",
        params={"periodo": PARCIAL, "anos": 5},
        headers=_headers(client, make_org, coorte),
    )
    assert resposta.status_code == 200, resposta.text
    body = resposta.json()
    assert body["exercicios_com_dado"] == [ANO_ANTERIOR, ANO]
    assert body["exercicios_sem_dado"] == [ANO - 4, ANO - 3, ANO - 2]
    assert body["cobertura_completa"] is False
    assert "sem dado em" in body["observacao"]
    # Nenhum ponto inventado para os exercícios ausentes.
    assert len(body["data"]) == 2


def test_trajetoria_do_exercicio_nao_se_mistura_com_a_serie_plurianual(
    client, make_org, coorte
) -> None:
    cod = coorte[0]
    resposta = client.get(
        f"/entes/{cod}/educacao/serie",
        params={"periodo": PARCIAL},
        headers=_headers(client, make_org, coorte),
    )
    body = resposta.json()
    trajetoria = body["trajetoria_exercicio"]
    assert [item["periodo"] for item in trajetoria] == [PARCIAL]
    assert all(item["exercicio"] == ANO for item in trajetoria)
    # A série plurianual conserva o exercício fechado; a trajetória, não.
    assert any(item["periodo"] == FECHADO for item in body["data"])


# --------------------------------------------------------------------------- #
# Coorte (benchmark) — o pedido central da 25C
# --------------------------------------------------------------------------- #
def test_benchmark_asps_posiciona_o_ente_na_coorte_com_a_unidade_correta(
    client, make_org, coorte
) -> None:
    headers = _headers(client, make_org, coorte)
    for cod in coorte:
        assert client.get(
            f"/entes/{cod}/saude", params={"periodo": FECHADO}, headers=headers
        ).status_code == 200

    resposta = client.get(
        "/benchmark",
        params={"ente": coorte[0], "indicador": "saude_minimo", "periodo": FECHADO,
                "coorte": "porte"},
        headers=headers,
    )
    assert resposta.status_code == 200, resposta.text
    body = resposta.json()
    # O rótulo da unidade não pode dizer "RCL" para um percentual de impostos.
    assert body["unidade"] == "percentual_impostos_transferencias"
    assert body["sentido"] == "maior_melhor"  # piso: mais é melhor
    assert body["cobertura"]["entes_com_valor"] >= 4
    # 16% é o menor da coorte (16, 17, 18, 20) => posição 1, percentil 0.
    assert float(body["ente"]["valor"]) == 16
    assert body["ente"]["posicao"] == 1
    assert float(body["distribuicao"]["mediana"]) == 17.5
    assert body["memoria"]["denominador"] == "impostos_transferencias"


def test_coorte_ignora_pares_cuja_base_do_percentual_e_outra(
    client, make_org, coorte
) -> None:
    """Um par com denominador diferente é excluído, não comparado."""
    headers = _headers(client, make_org, coorte)
    for cod in coorte:
        client.get(f"/entes/{cod}/saude", params={"periodo": FECHADO}, headers=headers)
    with SessionLocal() as session:
        # A linha efetiva é a que casa com a entrega RREO vigente (a outra é o
        # histórico de versão composta RREO+RGF).
        linha = session.scalar(
            select(MartIndicador).where(
                MartIndicador.cod_ibge == coorte[3],
                MartIndicador.indicador == "saude_minimo",
                MartIndicador.periodo == FECHADO,
                MartIndicador.versao_entrega == RREO_V,
            )
        )
        assert linha is not None
        linha.denominador = "rcl"  # simula uma linha herdada de outra apuração
        session.commit()

    resposta = client.get(
        "/benchmark",
        params={"ente": coorte[0], "indicador": "saude_minimo", "periodo": FECHADO,
                "coorte": "porte"},
        headers=headers,
    )
    assert resposta.status_code == 200, resposta.text
    body = resposta.json()
    assert body["memoria"]["entes_excluidos_por_base_divergente"] >= 1
    assert coorte[3] not in {ponto["cod_ibge"] for ponto in [body["ente"]]}
    assert body["quantidade"] == 3


# --------------------------------------------------------------------------- #
# Alerta de risco de descumprimento (projeção da Sprint 11 → motor da 15)
# --------------------------------------------------------------------------- #
def _org_para_alerta(make_org, codigos: tuple[str, ...]):
    return make_org(capacidades=["ver"], entes=list(codigos))


def test_risco_de_descumprimento_vira_alerta_no_bimestre_intermediario(
    make_org, coorte
) -> None:
    cod = coorte[0]
    org = _org_para_alerta(make_org, coorte)
    agora = datetime.now(UTC)
    with SessionLocal() as session:
        apply_context(session, org_id=org.org_id, user_id=None, is_admin=True)
        escritos = engine._alertas_minimos(session, org.org_id, cod, PARCIAL, agora)
        session.commit()
    assert escritos == 1  # MDE em 18% contra piso de 25%; ASPS em 17% cumpre os 15%
    with SessionLocal() as session:
        apply_context(session, org_id=org.org_id, user_id=None, is_admin=True)
        alerta = session.scalar(
            select(Alerta).where(
                Alerta.cod_ibge == cod, Alerta.chave.like("minimo_risco:%")
            )
        )
        assert alerta is not None
        assert alerta.indicador == "educacao_mde"
        assert alerta.categoria == "preditivo"
        assert alerta.severidade == "atencao"  # risco, não descumprimento
        assert alerta.link == "/saude-educacao"
        assert alerta.motivo_legal.startswith("CF art. 212")
        assert "não é apuração" in alerta.acao_sugerida
        assert alerta.memoria["apuracao_definitiva"].startswith("6º bimestre")


def test_no_fechamento_o_risco_cede_lugar_ao_alerta_de_limite(make_org, coorte) -> None:
    """No 6º bimestre a apuração é definitiva: dois alertas para o mesmo fato seria ruído."""
    cod = coorte[0]
    org = _org_para_alerta(make_org, coorte)
    agora = datetime.now(UTC)
    with SessionLocal() as session:
        apply_context(session, org_id=org.org_id, user_id=None, is_admin=True)
        # A apuração é materializada pelo job de carga (``workers.materialize``), não por
        # efeito colateral de uma leitura. Antes, este teste passava porque
        # ``_alertas_minimos`` projetava — e, de passagem, gravava o mart que o motor de
        # limites lê. Depender disso escondia que um GET estava escrevendo na gold.
        health_edu_service.build_saude(session, cod, FECHADO)
        health_edu_service.build_educacao(session, cod, FECHADO)

        assert engine._alertas_minimos(session, org.org_id, cod, FECHADO, agora) == 0
        # O motor de limites é quem fala no fechamento — com a faixa 'insuficiente'.
        assert engine._alertas_limite(session, org.org_id, cod, FECHADO, agora) >= 1
        session.commit()
    with SessionLocal() as session:
        apply_context(session, org_id=org.org_id, user_id=None, is_admin=True)
        limite = session.scalar(
            select(Alerta).where(
                Alerta.cod_ibge == cod, Alerta.chave == f"limite:educacao_mde:{FECHADO}"
            )
        )
        assert limite is not None
        assert limite.severidade == "critico"
        assert "insuficiente" in limite.titulo
