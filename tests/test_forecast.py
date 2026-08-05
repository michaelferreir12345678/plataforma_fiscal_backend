"""Testes de Previsões & Cenários (Módulo 13) — Sprint 14.

As fixtures usam um ano sintético (2091) para isolar o ente das séries reais já
carregadas no banco (fato_rcl/bcb_indice/tesouro_fpm de Fortaleza). Seedamos:
- ``gold.fato_rcl`` (série de 6 pontos, base da regressão);
- ``silver.tesouro_fpm`` + ``silver.bcb_indice`` (exógenas alinhadas por período);
- ``gold.mart_indicador`` (pessoal % RCL crescente, para o cruzamento do teto 54%).
"""

from __future__ import annotations

import calendar
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.modules.catalog.models import DimEnte
from app.modules.forecast.models import FatoProjecao
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import BcbIndice, DimEntrega, SilverEnte, TesouroFpm
from tests.conftest import auth_header, login

ANO = 2091  # fora das séries reais (2022–2024) — isola as exógenas do ente
VERSAO = "fc-v1"
BIMESTRES = [f"{ANO}-B{i}" for i in range(1, 7)]
# RCL crescente e suave (R$).
RCL_VALORES = [
    Decimal(v)
    for v in (10_000_000, 10_250_000, 10_600_000, 10_900_000, 11_150_000, 11_500_000)
]


def _ente() -> str:
    return "8" + "".join(random.choices("0123456789", k=6))


@dataclass(frozen=True)
class ForecastCase:
    cod_ibge: str


def _seed(case: ForecastCase) -> None:
    cod = case.cod_ibge
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente forecast", uf="CE", esfera="M"))

        # Série RCL (6 pontos) + entrega RREO por bimestre (as_of/source_ref).
        for i, (periodo, rcl) in enumerate(zip(BIMESTRES, RCL_VALORES, strict=True), start=1):
            s.add(
                FatoRcl(
                    cod_ibge=cod,
                    periodo_ref=periodo,
                    rcl_12m=rcl,
                    receita_corrente=rcl * Decimal("1.1"),
                    deducoes=rcl * Decimal("0.1"),
                    versao_entrega=VERSAO,
                    memoria={},
                )
            )
            s.add(
                DimEntrega(
                    cod_ibge=cod,
                    relatorio="RREO",
                    periodo=periodo,
                    versao_entrega=VERSAO,
                    homologada_em=datetime(2092, 1, i, tzinfo=UTC),
                    vigente=True,
                )
            )

        # Exógenas (silver): FPM (tesouro) + IPCA/Selic (BCB) mensais. Valores
        # oscilantes (não colineares com a tendência) para a regressão ser bem-posta.
        fpm_mensal = [920, 1180, 860, 1240, 990, 1300, 880, 1150, 1010, 1260, 940, 1210]
        ipca_mensal = ["0.62", "0.28", "0.71", "0.35", "0.59", "0.24",
                       "0.66", "0.31", "0.55", "0.40", "0.63", "0.29"]
        selic_mensal = ["0.90", "0.95", "0.88", "1.02", "0.93", "1.05",
                        "0.91", "0.98", "0.94", "1.00", "0.92", "1.03"]
        for mes in range(1, 13):
            vt = date(ANO, mes, calendar.monthrange(ANO, mes)[1])
            s.add(
                TesouroFpm(
                    cod_ibge=cod,
                    ano=ANO,
                    mes=mes,
                    valor_bruto=Decimal(fpm_mensal[mes - 1] * 1100),
                    valor_liquido=Decimal(fpm_mensal[mes - 1] * 1000),
                    valid_time=vt,
                    versao_entrega=VERSAO,
                )
            )
            # A14 (Sprint A5): FPM não tem vigência própria — vem de gold.dim_entrega sob
            # cod_ibge='BR' (ingestão nacional batch, §6.7). Sem isto, _fpm_periodo não
            # encontra versão vigente para nenhum mês e a exógena é descartada por inteira.
            s.add(
                DimEntrega(
                    cod_ibge="BR",
                    relatorio="FPM",
                    periodo=f"{ANO}-M{mes:02d}",
                    versao_entrega=VERSAO,
                    homologada_em=datetime(2092, 1, 1, tzinfo=UTC),
                    vigente=True,
                )
            )
            s.add(
                BcbIndice(
                    codigo_serie=433,  # IPCA
                    data_ref=date(ANO, mes, 1),
                    valor=Decimal(ipca_mensal[mes - 1]),
                    valid_time=date(ANO, mes, 1),
                    versao_entrega=VERSAO,
                )
            )
            s.add(
                BcbIndice(
                    codigo_serie=4390,  # Selic
                    data_ref=date(ANO, mes, 1),
                    valor=Decimal(selic_mensal[mes - 1]),
                    valid_time=date(ANO, mes, 1),
                    versao_entrega=VERSAO,
                )
            )

        # Pessoal % RCL crescente, cruzando o teto 54% do Executivo municipal.
        pct_pessoal = (Decimal("50.0"), Decimal("52.0"), Decimal("54.0"))
        for periodo, pct in zip(BIMESTRES[:3], pct_pessoal, strict=True):
            s.add(
                MartIndicador(
                    cod_ibge=cod,
                    periodo=periodo,
                    indicador="pessoal_executivo",
                    valor_rs=Decimal("100"),
                    valor_pct_rcl=pct,
                    faixa="prudencial",
                    teto_pct=Decimal("54"),
                    versao_entrega=VERSAO,
                    source_ref={"relatorio": "RGF"},
                )
            )
        s.commit()


@pytest.fixture
def forecast_case() -> Iterator[ForecastCase]:
    case = ForecastCase(cod_ibge=_ente())
    _seed(case)
    yield case
    cod = case.cod_ibge
    with SessionLocal() as s:
        s.execute(delete(FatoProjecao).where(FatoProjecao.cod_ibge == cod))
        s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
        s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
        s.execute(delete(TesouroFpm).where(TesouroFpm.cod_ibge == cod))
        s.execute(delete(BcbIndice).where(BcbIndice.versao_entrega == VERSAO))
        s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
        # Linha nacional (cod_ibge='BR') do FPM sintético — escopada por período (ANO=2091
        # é fora de qualquer intervalo real) e versão, nunca toca a vigência real do FPM.
        s.execute(
            delete(DimEntrega).where(
                DimEntrega.cod_ibge == "BR",
                DimEntrega.relatorio == "FPM",
                DimEntrega.versao_entrega == VERSAO,
            )
        )
        s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
        s.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
        s.commit()


def _token(client, make_org, cod_ibge: str) -> str:
    org = make_org(capacidades=["ver"], entes=[cod_ibge])
    return login(client, org.email, org.senha)


def test_projecao_sempre_traz_ic(client, make_org, forecast_case: ForecastCase) -> None:
    """Aceite: toda projeção retorna IC — nunca número único."""
    token = _token(client, make_org, forecast_case.cod_ibge)
    resp = client.get(
        f"/entes/{forecast_case.cod_ibge}/projecao",
        params={"indicador": "rcl", "horizonte": 3},
        headers=auth_header(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unidade"] == "BRL"
    assert len(body["historico"]) == 6
    assert len(body["projecao"]) == 3
    for ponto in body["projecao"]:
        inf = float(ponto["ic_inferior"])
        prev = float(ponto["valor_previsto"])
        sup = float(ponto["ic_superior"])
        assert inf < prev < sup, ponto
    # Rastreabilidade obrigatória.
    assert body["source_ref"]["relatorio"] == "RREO"
    assert body["nivel_confianca"] == "95"


def test_regressao_consome_bcb_indice_e_tesouro_fpm(
    client, make_org, forecast_case: ForecastCase
) -> None:
    """Aceite: a regressão com exógenas usa efetivamente bcb_indice e tesouro_fpm."""
    token = _token(client, make_org, forecast_case.cod_ibge)
    resp = client.get(
        f"/entes/{forecast_case.cod_ibge}/projecao",
        params={"indicador": "rcl", "horizonte": 3, "modelo": "regressao_exogenas"},
        headers=auth_header(token),
    )
    assert resp.status_code == 200, resp.text
    memoria = resp.json()["memoria"]
    usadas = memoria["exogenas_usadas"]
    assert "FPM" in usadas  # silver.tesouro_fpm
    assert "IPCA" in usadas  # silver.bcb_indice#433
    fontes = memoria["exogenas_fontes"]
    assert fontes["FPM"] == "silver.tesouro_fpm"
    assert fontes["IPCA"] == "silver.bcb_indice#433"
    # Coeficientes estimados para as exógenas (prova de consumo no ajuste).
    assert "FPM" in memoria["coeficientes"]
    assert "IPCA" in memoria["coeficientes"]


def test_projecao_materializa_gold_com_ic(client, make_org, forecast_case: ForecastCase) -> None:
    token = _token(client, make_org, forecast_case.cod_ibge)
    client.get(
        f"/entes/{forecast_case.cod_ibge}/projecao",
        params={"indicador": "rcl", "horizonte": 3},
        headers=auth_header(token),
    )
    with SessionLocal() as s:
        linhas = list(
            s.scalars(
                select(FatoProjecao).where(
                    FatoProjecao.cod_ibge == forecast_case.cod_ibge,
                    FatoProjecao.indicador == "rcl",
                )
            )
        )
    assert linhas, "projeção não materializada em gold.fato_projecao"
    # O check-constraint do banco garante o IC; reafirma no Python.
    for linha in linhas:
        assert linha.ic_inferior <= linha.valor_previsto <= linha.ic_superior


def test_cruzamento_de_limite_calculado(client, make_org, forecast_case: ForecastCase) -> None:
    """Aceite: cruzamento de limite calculado (pessoal cruza 54% do Executivo)."""
    token = _token(client, make_org, forecast_case.cod_ibge)
    resp = client.get(
        f"/entes/{forecast_case.cod_ibge}/projecao",
        params={"indicador": "pessoal", "horizonte": 4, "modelo": "fechamento"},
        headers=auth_header(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unidade"] == "PCT_RCL"
    cruz = body["cruzamento"]
    assert cruz["aplicavel"] is True
    assert cruz["cruza"] is True
    assert float(cruz["teto_pct"]) == 54.0
    assert cruz["indicador_limite"] == "pessoal_executivo"
    # O ponto de cruzamento marca a faixa excedida.
    ponto_cruza = next(p for p in body["projecao"] if p["cruza_limite"])
    assert ponto_cruza["periodo_alvo"] == cruz["periodo_cruzamento"]
    assert ponto_cruza["faixa"] == "excedido"


def test_cenario_nao_persiste_sem_salvar(client, make_org, forecast_case: ForecastCase) -> None:
    """Aceite: cenário não persiste salvo se salvar=True."""
    token = _token(client, make_org, forecast_case.cod_ibge)
    cod = forecast_case.cod_ibge

    def _n_cenarios() -> int:
        r = client.get(f"/entes/{cod}/cenarios", headers=auth_header(token))
        assert r.status_code == 200, r.text
        return len(r.json())

    antes = _n_cenarios()
    simulado = client.post(
        f"/entes/{cod}/cenario/simular",
        params={"indicador": "rcl"},
        json={"nome": "Choque", "horizonte": 3, "ipca_aa_pct": 6.0, "crescimento_rcl_pct": 2.0},
        headers=auth_header(token),
    )
    assert simulado.status_code == 200, simulado.text
    body = simulado.json()
    assert body["persistido"] is False
    assert body["cenario_id"] is None
    # Traz base + cenário, ambos com IC, e impacto em limites/mínimos.
    cenario_pontos = body["cenario"]["projecao"]
    assert all(float(p["ic_inferior"]) < float(p["valor_previsto"]) for p in cenario_pontos)
    assert {item["indicador"] for item in body["impacto_limites"]}  # tetos
    assert {item["indicador"] for item in body["impacto_minimos"]}  # pisos
    assert _n_cenarios() == antes  # nada gravado

    salvo = client.post(
        f"/entes/{cod}/cenario/simular",
        params={"indicador": "rcl"},
        json={"nome": "Choque salvo", "horizonte": 3, "ipca_aa_pct": 6.0, "salvar": True},
        headers=auth_header(token),
    )
    assert salvo.status_code == 200, salvo.text
    salvo_body = salvo.json()
    assert salvo_body["persistido"] is True
    assert salvo_body["cenario_id"] is not None
    assert _n_cenarios() == antes + 1


def test_projecao_403_fora_da_carteira(client, make_org) -> None:
    cod = _ente()
    org = make_org(capacidades=["ver"], entes=[])
    token = login(client, org.email, org.senha)
    resp = client.get(
        f"/entes/{cod}/projecao",
        params={"indicador": "rcl"},
        headers=auth_header(token),
    )
    assert resp.status_code == 403
    assert resp.json()["title"] == "Ente fora do escopo"


def test_cenario_salvo_isolado_por_organizacao(
    client, make_org, forecast_case: ForecastCase
) -> None:
    """RLS: um cenário salvo por uma org não vaza para outra (op.cenario)."""
    cod = forecast_case.cod_ibge
    token_a = _token(client, make_org, cod)
    client.post(
        f"/entes/{cod}/cenario/simular",
        params={"indicador": "rcl"},
        json={"nome": "Privado A", "horizonte": 2, "salvar": True},
        headers=auth_header(token_a),
    )
    # Outra organização com o mesmo ente na carteira não enxerga o cenário da primeira.
    org_b = make_org(capacidades=["ver"], entes=[cod])
    token_b = login(client, org_b.email, org_b.senha)
    r = client.get(f"/entes/{cod}/cenarios", headers=auth_header(token_b))
    assert r.status_code == 200, r.text
    assert r.json() == []
