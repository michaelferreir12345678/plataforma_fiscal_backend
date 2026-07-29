"""Sprint 25A — comparabilidade da série e conciliação auditável (Receita & Despesa).

Cobre os aceites da sub-sprint:
- série multi-exercício em **nominal × real (IPCA) × per capita**, sem inventar valor
  quando falta deflator ou população;
- conciliação de transferências com **fonte dos dois lados** e marcação explícita das
  séries derivadas do próprio RREO (que não são contraprova independente).
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.modules.catalog.models import DimEnte
from app.modules.expense.models import FatoDespesa
from app.modules.indicators import serie_ajuste
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import (
    BcbIndice,
    DimEntrega,
    FndeFundebRepasse,
    IbgePopulacao,
    SilverEnte,
    SilverRreo,
    TesouroFpm,
    TransferenciaGenerica,
)
from app.modules.revenue.models import FatoReceita
from app.shared import periodo as periodo_util
from app.workers import materialize
from tests.conftest import auth_header, login

ANEXO01 = "RREO-Anexo 01"
PREV_ATU = "PREVISÃO ATUALIZADA (a)"
ACUM = "Até o Bimestre (c)"
N_EMP = "DESPESAS EMPENHADAS ATÉ O BIMESTRE (f)"
N_DOT = "DOTAÇÃO ATUALIZADA (e)"
N_LIQ = "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)"
N_PAG = "DESPESAS PAGAS ATÉ O BIMESTRE (j)"
N_RAP = "INSCRITAS EM RESTOS A PAGAR NÃO PROCESSADOS (k)"
ANEXO02 = "RREO-Anexo 02"
F_EMP = "DESPESAS EMPENHADAS ATÉ O BIMESTRE (b)"
F_DOT = "DOTAÇÃO ATUALIZADA (a)"

# Versão alta o bastante para vencer a série 433 real que já vive no banco local.
VERSAO_IPCA_TESTE = "9999-sprint25a"
POPULACAO = {2023: 1_000_000, 2024: 1_100_000}


def _ente() -> str:
    return "4" + "".join(random.choices("0123456789", k=6))


@pytest.fixture
def limpar() -> Iterator[list[str]]:
    usados: list[str] = []
    yield usados
    with SessionLocal() as s:
        for cod in usados:
            s.execute(delete(FatoReceita).where(FatoReceita.cod_ibge == cod))
            s.execute(delete(FatoDespesa).where(FatoDespesa.cod_ibge == cod))
            s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
            s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
            s.execute(delete(SilverRreo).where(SilverRreo.cod_ibge == cod))
            s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
            s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
            s.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
            s.execute(delete(IbgePopulacao).where(IbgePopulacao.cod_ibge == cod))
            s.execute(delete(TesouroFpm).where(TesouroFpm.cod_ibge == cod))
            s.execute(delete(FndeFundebRepasse).where(FndeFundebRepasse.cod_ibge == cod))
            s.execute(
                delete(TransferenciaGenerica).where(TransferenciaGenerica.cod_ibge == cod)
            )
        s.execute(delete(BcbIndice).where(BcbIndice.versao_entrega == VERSAO_IPCA_TESTE))
        s.commit()


def _seed_periodo(cod: str, periodo: str, arrecadado: str, empenhado: str) -> None:
    """Um exercício de receita (A01 receita) e despesa (A01 natureza) para o ente."""
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente", uf="CE", esfera="M"))
        s.add(
            DimEntrega(
                cod_ibge=cod, relatorio="RREO", periodo=periodo, versao_entrega="1",
                homologada_em=datetime(2025, 1, 10, tzinfo=UTC), vigente=True,
            )
        )
        linhas_receita = [
            (8, "ReceitasCorrentes", "RECEITAS CORRENTES", arrecadado),
            (36, "TransferenciasCorrentes", "TRANSFERÊNCIAS CORRENTES", arrecadado),
        ]
        for seq, cod_conta, desc, valor in linhas_receita[:1]:
            s.add(SilverRreo(
                cod_ibge=cod, periodo=periodo, anexo=ANEXO01, conta=desc, cod_conta=cod_conta,
                coluna=PREV_ATU, linha_seq=seq, valor=Decimal(valor), versao_entrega="1",
            ))
            s.add(SilverRreo(
                cod_ibge=cod, periodo=periodo, anexo=ANEXO01, conta=desc, cod_conta=cod_conta,
                coluna=ACUM, linha_seq=seq, valor=Decimal(valor), versao_entrega="1",
            ))
        liquidado = str(Decimal(empenhado) * Decimal("0.9"))
        pago = str(Decimal(empenhado) * Decimal("0.8"))
        rap = str(Decimal(empenhado) * Decimal("0.1"))
        for cc, conta, coluna, valor in (
            ("DespesasCorrentes", "DESPESAS CORRENTES", N_DOT, empenhado),
            ("DespesasCorrentes", "DESPESAS CORRENTES", N_EMP, empenhado),
            ("DespesasCorrentes", "DESPESAS CORRENTES", N_LIQ, liquidado),
            ("DespesasCorrentes", "DESPESAS CORRENTES", N_PAG, pago),
            ("DespesasCorrentes", "DESPESAS CORRENTES", N_RAP, rap),
        ):
            s.add(SilverRreo(
                cod_ibge=cod, periodo=periodo, anexo=ANEXO01, cod_conta=cc, conta=conta,
                coluna=coluna, valor=Decimal(valor), versao_entrega="1",
            ))
        # Anexo 02 (função): é o eixo em que a série de despesa é apurada.
        for coluna, valor in ((F_DOT, empenhado), (F_EMP, empenhado)):
            s.add(SilverRreo(
                cod_ibge=cod, periodo=periodo, anexo=ANEXO02, cod_conta="RREO2TotalDespesas",
                conta="Saúde", coluna=coluna, linha_seq=2, valor=Decimal(valor),
                versao_entrega="1",
            ))
        s.commit()


def _seed_ipca(variacao_mensal: str = "1", ano: int = 2024) -> None:
    """IPCA de todos os meses do ano (variação % mensal), na versão de teste."""
    with SessionLocal() as s:
        for mes in range(1, 13):
            s.add(BcbIndice(
                codigo_serie=433, data_ref=date(ano, mes, 1),
                valor=Decimal(variacao_mensal), versao_entrega=VERSAO_IPCA_TESTE,
            ))
        s.commit()


def _seed_populacao(cod: str) -> None:
    with SessionLocal() as s:
        for ano, pop in POPULACAO.items():
            s.add(IbgePopulacao(
                cod_ibge=cod, ano_ref=ano, populacao=pop, fonte="IBGE", versao_entrega="1",
            ))
        s.commit()


def _materializar(cod: str) -> None:
    """Roda a materialização da gold como o job de produção (Sprint 21).

    A série multi-exercício mostra o que está **na gold**; períodos entregues mas nunca
    materializados não existem para a tela — a lacuna aparece na cobertura, não como zero.
    """
    with SessionLocal() as s:
        materialize.materialize_ente(s, cod)
        s.commit()


# --- aritmética pura do ajuste -------------------------------------------------
def test_mes_final_do_periodo_fiscal() -> None:
    assert periodo_util.mes_final("2024-B6") == 12
    assert periodo_util.mes_final("2024-B3") == 6
    assert periodo_util.mes_final("2024-Q1") == 4
    assert periodo_util.mes_final("2024-S1") == 6
    assert periodo_util.mes_final("2024-M07") == 7
    assert periodo_util.mes_final("2024") == 12
    assert periodo_util.mes_final("lixo") is None


def test_deflator_acumula_a_inflacao_do_intervalo() -> None:
    ipca = {(2024, m): Decimal("1") for m in range(1, 13)}
    # mesmo período ⇒ fator 1 (nada a deflacionar)
    assert serie_ajuste._fator(ipca, (2024, 12), (2024, 12)) == Decimal(1)
    # 2023-B6 → 2024-B6: doze meses a 1% ⇒ 1,01^12
    fator = serie_ajuste._fator(ipca, (2023, 12), (2024, 12))
    assert fator is not None
    assert float(fator) == pytest.approx(1.01**12, rel=1e-9)
    # período posterior à base ⇒ fator inverso (traz de volta a preços da base)
    inverso = serie_ajuste._fator(ipca, (2024, 12), (2023, 12))
    assert inverso is not None
    assert float(inverso) == pytest.approx(1 / 1.01**12, rel=1e-9)


def test_mes_faltante_nao_vira_inflacao_zero() -> None:
    ipca = {(2024, m): Decimal("1") for m in range(1, 13) if m != 7}
    assert serie_ajuste._fator(ipca, (2023, 12), (2024, 12)) is None


def test_serie_sem_ipca_declara_a_lacuna(limpar) -> None:
    """Exercício anterior à série 433 do BCB: sem deflator, e a resposta diz por quê."""
    with SessionLocal() as s:
        ajuste = serie_ajuste.calcular(s, "9999999", ["1990-B6", "2024-B6"], "2024-B6")
    por_periodo = {i.periodo: i for i in ajuste.itens}
    assert por_periodo["1990-B6"].fator_deflator is None
    assert por_periodo["2024-B6"].fator_deflator == Decimal(1)  # a base é sempre 1
    assert ajuste.observacao is not None and "1990-B6" in ajuste.observacao


# --- contrato dos endpoints ----------------------------------------------------
def test_receita_serie_traz_real_e_per_capita(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_periodo(cod, "2023-B6", arrecadado="1000", empenhado="800")
    _seed_periodo(cod, "2024-B6", arrecadado="1100", empenhado="900")
    _seed_ipca("1", ano=2024)
    _seed_populacao(cod)
    _materializar(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    body = client.get(
        f"/entes/{cod}/receita", params={"periodo": "2024-B6"}, headers=auth_header(token)
    ).json()

    ajuste = body["serie_ajuste"]
    assert ajuste["base_periodo"] == "2024-B6"
    assert ajuste["deflator_disponivel"] is True
    assert ajuste["populacao_disponivel"] is True
    assert "433" in ajuste["fonte_deflator"]

    serie = {s["periodo"]: s for s in body["serie"]}
    # 2023 a preços de 2024: 1000 × 1,01^12
    assert float(serie["2023-B6"]["arrecadado_real"]) == pytest.approx(1000 * 1.01**12, rel=1e-6)
    # o período base não é deflacionado
    assert float(serie["2024-B6"]["arrecadado_real"]) == 1100.0
    # per capita usa a população do ANO do ponto, não a do período consultado
    assert float(serie["2023-B6"]["arrecadado_per_capita"]) == pytest.approx(1000 / 1_000_000)
    assert float(serie["2024-B6"]["arrecadado_per_capita"]) == pytest.approx(1100 / 1_100_000)
    assert serie["2023-B6"]["populacao"] == POPULACAO[2023]


def test_despesa_serie_traz_real_e_per_capita(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_periodo(cod, "2023-B6", arrecadado="1000", empenhado="800")
    _seed_periodo(cod, "2024-B6", arrecadado="1100", empenhado="900")
    _seed_ipca("1", ano=2024)
    _seed_populacao(cod)
    _materializar(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    body = client.get(
        f"/entes/{cod}/despesa",
        params={"periodo": "2024-B6", "eixo": "natureza"},
        headers=auth_header(token),
    ).json()

    serie = {s["periodo"]: s for s in body["serie"]}
    assert float(serie["2023-B6"]["empenhado_real"]) == pytest.approx(800 * 1.01**12, rel=1e-6)
    assert float(serie["2024-B6"]["empenhado_real"]) == 900.0
    assert float(serie["2024-B6"]["empenhado_per_capita"]) == pytest.approx(900 / 1_100_000)
    assert body["serie_ajuste"]["base_periodo"] == "2024-B6"


def test_sem_populacao_o_per_capita_fica_ausente(client, make_org, limpar) -> None:
    """Ausência aparece como ausência — jamais como zero (regra do produto)."""
    cod = _ente()
    limpar.append(cod)
    _seed_periodo(cod, "2024-B6", arrecadado="1100", empenhado="900")
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    body = client.get(
        f"/entes/{cod}/receita", params={"periodo": "2024-B6"}, headers=auth_header(token)
    ).json()
    serie = body["serie"][0]
    assert serie["arrecadado_per_capita"] is None
    assert serie["populacao"] is None
    assert body["serie_ajuste"]["populacao_disponivel"] is False


def test_conciliacao_expoe_fonte_dos_dois_lados(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente", uf="CE", esfera="M"))
        s.add(DimEntrega(
            cod_ibge=cod, relatorio="RREO", periodo="2024-B6", versao_entrega="1",
            homologada_em=datetime(2025, 1, 10, tzinfo=UTC), vigente=True,
        ))
        for seq, cc, desc, valor in (
            (8, "ReceitasCorrentes", "RECEITAS CORRENTES", "1000"),
            (36, "TransferenciasCorrentes", "TRANSFERÊNCIAS CORRENTES", "800"),
            (43, "TransferenciasDaUniao", "Cota-Parte do FPM", "300"),
            (50, "TransferenciasDosEstados", "Cota-Parte do ICMS", "500"),
        ):
            s.add(SilverRreo(
                cod_ibge=cod, periodo="2024-B6", anexo=ANEXO01, conta=desc, cod_conta=cc,
                coluna=ACUM, linha_seq=seq, valor=Decimal(valor), versao_entrega="1",
            ))
        # FPM: fonte independente (Tesouro) e divergente do RREO em +10.
        s.add(TesouroFpm(cod_ibge=cod, ano=2024, mes=6, valor_liquido=Decimal("310"),
                         versao_entrega="1"))
        # ICMS: série DERIVADA do próprio RREO (Sprint 21) — não é contraprova.
        s.add(TransferenciaGenerica(
            cod_ibge=cod, tipo="Cota-Parte do ICMS", ano=2024, mes=6, valor=Decimal("500"),
            fonte="derivado_rreo_a1", versao_entrega="1",
        ))
        s.commit()
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    body = client.get(
        f"/entes/{cod}/receita/transferencias/conciliacao",
        params={"periodo": "2024-B6"}, headers=auth_header(token),
    ).json()
    itens = {i["transferencia"]: i for i in body["itens"]}

    fpm = itens["FPM"]
    assert fpm["status"] == "divergente"
    assert fpm["independente"] is True
    assert fpm["tabela_externa"] == "silver.tesouro_fpm"
    assert fpm["periodo_externo"] == "2024 · meses 1–12"
    assert fpm["nos_rreo"] == ["TransferenciasDaUniao"]  # lado RREO auditável
    assert float(fpm["divergencia_rs"]) == 10.0
    assert float(fpm["rreo_acum"]) == 300.0 and float(fpm["externo_acum"]) == 310.0

    icms = itens["Cota-Parte do ICMS"]
    assert icms["status"] == "conciliado"
    assert icms["independente"] is False  # derivada do RREO: consistência, não contraprova
    assert icms["tabela_externa"] == "silver.transferencia_generica"
    assert icms["nos_rreo"] == ["TransferenciasDosEstados"]
    assert fpm["base_comparacao"] == "linha_especifica"


def test_ente_que_nao_abre_a_linha_concilia_por_contencao(client, make_org, limpar) -> None:
    """Caso real (Fortaleza): o Anexo 01 para na espécie e não abre FPM.

    Comparar contra o agregado não é equivalência — é contenção. O FPM tem de **caber**
    nas transferências da União informadas; exceder o todo é erro de dado, não divergência.
    """
    cod = _ente()
    limpar.append(cod)
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente", uf="CE", esfera="M"))
        s.add(DimEntrega(
            cod_ibge=cod, relatorio="RREO", periodo="2024-B6", versao_entrega="1",
            homologada_em=datetime(2025, 1, 10, tzinfo=UTC), vigente=True,
        ))
        for seq, cc, desc, valor in (
            (8, "ReceitasCorrentes", "RECEITAS CORRENTES", "1000"),
            (36, "TransferenciasCorrentes", "TRANSFERÊNCIAS CORRENTES", "800"),
            (43, "TransferenciasCorrentesDaUniaoEDeSuasEntidades",
             "Transferências da União e de suas Entidades", "800"),
        ):
            s.add(SilverRreo(
                cod_ibge=cod, periodo="2024-B6", anexo=ANEXO01, conta=desc, cod_conta=cc,
                coluna=ACUM, linha_seq=seq, valor=Decimal(valor), versao_entrega="1",
            ))
        s.add(TesouroFpm(cod_ibge=cod, ano=2024, mes=6, valor_liquido=Decimal("200"),
                         versao_entrega="1"))
        # FUNDEB não tem agregado inequívoco: permanece "sem par" em vez de comparar errado.
        s.add(FndeFundebRepasse(cod_ibge=cod, ano=2024, mes=6, valor_repassado=Decimal("100"),
                                versao_entrega="1"))
        s.commit()
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    itens = {
        i["transferencia"]: i
        for i in client.get(
            f"/entes/{cod}/receita/transferencias/conciliacao",
            params={"periodo": "2024-B6"}, headers=auth_header(token),
        ).json()["itens"]
    }
    fpm = itens["FPM"]
    assert fpm["base_comparacao"] == "agregado"
    assert fpm["status"] == "contido"
    assert float(fpm["rreo_acum"]) == 800.0  # agregado da União
    assert float(fpm["participacao_no_agregado_pct"]) == 25.0
    assert fpm["divergencia_pct"] is None  # % de divergência contra agregado seria mentira

    assert itens["FUNDEB"]["status"] == "sem_par_rreo"
    assert itens["FUNDEB"]["base_comparacao"] == "ausente"


def test_parte_maior_que_o_todo_e_erro_de_dado(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente", uf="CE", esfera="M"))
        s.add(DimEntrega(
            cod_ibge=cod, relatorio="RREO", periodo="2024-B6", versao_entrega="1",
            homologada_em=datetime(2025, 1, 10, tzinfo=UTC), vigente=True,
        ))
        for seq, cc, desc, valor in (
            (8, "ReceitasCorrentes", "RECEITAS CORRENTES", "1000"),
            (36, "TransferenciasCorrentes", "TRANSFERÊNCIAS CORRENTES", "100"),
            (43, "TransferenciasCorrentesDaUniaoEDeSuasEntidades",
             "Transferências da União e de suas Entidades", "100"),
        ):
            s.add(SilverRreo(
                cod_ibge=cod, periodo="2024-B6", anexo=ANEXO01, conta=desc, cod_conta=cc,
                coluna=ACUM, linha_seq=seq, valor=Decimal(valor), versao_entrega="1",
            ))
        s.add(TesouroFpm(cod_ibge=cod, ano=2024, mes=6, valor_liquido=Decimal("500"),
                         versao_entrega="1"))
        s.commit()
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    itens = {
        i["transferencia"]: i
        for i in client.get(
            f"/entes/{cod}/receita/transferencias/conciliacao",
            params={"periodo": "2024-B6"}, headers=auth_header(token),
        ).json()["itens"]
    }
    assert itens["FPM"]["status"] == "excede_agregado"


def test_cascata_sem_pago_diz_qual_anexo_publica(client, make_org, limpar) -> None:
    """Aceite 'cascata mostra pago e RAP': no eixo função o RREO não publica pago."""
    cod = _ente()
    limpar.append(cod)
    _seed_periodo(cod, "2024-B6", arrecadado="1000", empenhado="900")
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    def estagios(eixo: str) -> dict:
        return client.get(
            f"/entes/{cod}/despesa/estagios",
            params={"periodo": "2024-B6", "eixo": eixo}, headers=auth_header(token),
        ).json()

    funcao = estagios("funcao")
    assert "pago" in funcao["estagios_ausentes"]
    assert "Anexo 02" in (funcao["observacao"] or "")
    assert "natureza" in (funcao["observacao"] or "")

    natureza = estagios("natureza")
    passos = {p["estagio"]: p["valor"] for p in natureza["cascata"]}
    assert passos["pago"] is not None
    lacunas = {lac["nome"] for lac in natureza["lacunas"]}
    assert {"a_pagar", "potencial_rap"} <= lacunas


def test_ritmo_de_pagamento_exige_o_eixo_que_publica_pago(client, make_org, limpar) -> None:
    """Base e numerador do ritmo saem do MESMO anexo — misturar eixos daria % sem sentido."""
    cod = _ente()
    limpar.append(cod)
    _seed_periodo(cod, "2024-B6", arrecadado="1000", empenhado="900")
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    def execucao(eixo: str) -> dict[str, dict]:
        body = client.get(
            f"/entes/{cod}/despesa/execucao",
            params={"periodo": "2024-B6", "eixo": eixo}, headers=auth_header(token),
        ).json()
        assert body["eixo"] == eixo
        return {e["estagio"]: e for e in body["estagios"]}

    assert execucao("funcao")["pago"]["status"] == "sem_base"  # A02 não publica pago
    pago = execucao("natureza")["pago"]
    assert pago["status"] != "sem_base"
    assert float(pago["executado_pct"]) == pytest.approx(80.0)  # 720 ÷ 900 de dotação
