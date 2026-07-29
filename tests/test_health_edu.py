"""Aceites da Sprint 11 — mínimos primários RREO e enriquecimentos separados."""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.modules.cash_rap.models import FatoDisponibilidade
from app.modules.catalog.models import DimEnte
from app.modules.health_edu.models import FatoEducacao, FatoSaude, FatoSaudeSubfuncao
from app.modules.indicators.models import MartIndicador
from app.modules.ingestion.models import (
    DimEntrega,
    SilverEnte,
    SilverRgf,
    SilverRreo,
    SiopeEducacao,
    SiopsSaude,
)
from tests.conftest import auth_header, login

ANO = 2094
PERIODO = f"{ANO}-B6"
PERIODO_RGF = f"{ANO}-Q3"
RREO_V = "health-edu-rreo-v1"
RGF_V = "health-edu-rgf-v1"

BRUTA = "DISPONIBILIDADE DE CAIXA BRUTA (a)"
ANTES = (
    "DISPONIBILIDADE DE CAIXA LÍQUIDA (ANTES DA INSCRIÇÃO EM RESTOS A PAGAR NÃO "
    "PROCESSADOS DO EXERCÍCIO) (f)=(a-(b+c+d+e))"
)
RPNP = "RESTOS A PAGAR EMPENHADOS E NÃO LIQUIDADOS DO EXERCÍCIO (g)"


def _ente() -> str:
    return "7" + "".join(random.choices("0123456789", k=6))


def _row(
    cod: str,
    anexo: str,
    codigo: str,
    valor: str,
    *,
    coluna: str = "VALOR",
    seq: int = 1,
) -> SilverRreo:
    return SilverRreo(
        cod_ibge=cod,
        periodo=PERIODO,
        anexo=anexo,
        conta=codigo.replace("_", " "),
        cod_conta=codigo,
        coluna=coluna,
        linha_seq=seq,
        valor=Decimal(valor),
        versao_entrega=RREO_V,
    )


def _rreo(cod: str, *, saude_bruta: str = "160") -> list[SilverRreo]:
    rows = [
        _row(cod, "RREO-Anexo 12", "ASPS_BASE_IMPOSTOS_TRANSFERENCIAS", "1000", seq=1),
        _row(
            cod, "RREO-Anexo 12", "ASPS_DESPESA_TOTAL", saude_bruta,
            coluna="DESPESAS EMPENHADAS", seq=2,
        ),
        _row(
            cod, "RREO-Anexo 12", "ASPS_DEDUCOES_OUTRAS", "0",
            coluna="DESPESAS EMPENHADAS", seq=3,
        ),
        _row(cod, "RREO-Anexo 12", "ASPS_RPNP_SEM_LASTRO_REPORTADO", "10", seq=4),
        _row(
            cod, "RREO-Anexo 12", "ASPS_SUBFUNCAO_ATENCAO_BASICA", "100",
            coluna="DESPESAS EMPENHADAS", seq=10,
        ),
        _row(
            cod, "RREO-Anexo 12",
            "ASPS_SUBFUNCAO_ASSISTENCIA_HOSPITALAR_E_AMBULATORIAL", "60",
            coluna="DESPESAS EMPENHADAS", seq=11,
        ),
        _row(cod, "RREO-Anexo 08", "MDE_BASE_IMPOSTOS_TRANSFERENCIAS", "1000", seq=1),
        _row(
            cod, "RREO-Anexo 08", "MDE_DESPESA_IMPOSTOS", "200",
            coluna="DESPESAS EMPENHADAS", seq=2,
        ),
        _row(cod, "RREO-Anexo 08", "MDE_TRANSFERENCIA_FUNDEB", "60", seq=3),
        _row(
            cod, "RREO-Anexo 08", "MDE_SUPERAVIT_EXERCICIO_ANTERIOR", "5",
            coluna="DESPESAS EMPENHADAS", seq=4,
        ),
        _row(
            cod, "RREO-Anexo 08", "MDE_COMPLEMENTACAO_VAAF_EXERCICIO_ANTERIOR", "0",
            coluna="DESPESAS EMPENHADAS", seq=5,
        ),
        _row(
            cod, "RREO-Anexo 08", "MDE_CANCELAMENTOS", "2",
            coluna="DESPESAS EMPENHADAS", seq=6,
        ),
        _row(cod, "RREO-Anexo 08", "MDE_RPNP_SEM_LASTRO_REPORTADO", "3", seq=7),
        _row(cod, "RREO-Anexo 08", "FUNDEB_BASE_PROFISSIONAIS", "200", seq=8),
        _row(
            cod, "RREO-Anexo 08", "FUNDEB_PROFISSIONAIS", "140",
            coluna="DESPESAS EMPENHADAS", seq=9,
        ),
    ]
    return rows


def _rgf(cod: str) -> list[SilverRgf]:
    rows: list[SilverRgf] = []
    # Saúde: max(0, 20 - max(0, 10)) = 10.
    # Educação: max(0, 3 - max(0, 0)) = 3.
    for seq, conta, coluna, valor in (
        (1, "Recursos Vinculados à Saúde", BRUTA, "30"),
        (2, "Recursos Vinculados à Saúde", ANTES, "10"),
        (3, "Recursos Vinculados à Saúde", RPNP, "20"),
        (4, "Recursos de Operações de Crédito (exceto vinculados à Educação e à Saúde)",
         BRUTA, "999"),
        (5, "Recursos de Operações de Crédito (exceto vinculados à Educação e à Saúde)",
         ANTES, "0"),
        (6, "Recursos de Operações de Crédito (exceto vinculados à Educação e à Saúde)",
         RPNP, "999"),
        (7, "Recursos Vinculados à Educação", BRUTA, "10"),
        (8, "Recursos Vinculados à Educação", ANTES, "0"),
        (9, "Recursos Vinculados à Educação", RPNP, "3"),
    ):
        rows.append(
            SilverRgf(
                cod_ibge=cod,
                periodo=PERIODO_RGF,
                anexo="RGF-Anexo 05",
                conta=conta,
                coluna=coluna,
                poder="E",
                linha_seq=seq,
                valor=Decimal(valor),
                versao_entrega=RGF_V,
            )
        )
    return rows


@pytest.fixture
def caso() -> Iterator[tuple[str, bool]]:
    cod = _ente()
    with SessionLocal() as session:
        session.add(SilverEnte(cod_ibge=cod, nome="Ente mínimos", uf="CE", esfera="M"))
        session.add_all(
            [
                DimEntrega(
                    cod_ibge=cod, relatorio="RREO", periodo=PERIODO,
                    versao_entrega=RREO_V, homologada_em=datetime(2095, 1, 31, tzinfo=UTC),
                    vigente=True,
                ),
                DimEntrega(
                    cod_ibge=cod, relatorio="RGF", periodo=PERIODO_RGF,
                    versao_entrega=RGF_V, homologada_em=datetime(2095, 2, 20, tzinfo=UTC),
                    vigente=True,
                ),
            ]
        )
        session.add_all(_rreo(cod))
        session.add_all(_rgf(cod))
        session.commit()
    yield cod, False
    with SessionLocal() as session:
        # Desde a Sprint 25C a apuração dos mínimos também escreve no mart; sem esta
        # linha o banco de desenvolvimento acumularia entes sintéticos no benchmarking.
        session.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
        session.execute(delete(FatoSaudeSubfuncao).where(FatoSaudeSubfuncao.cod_ibge == cod))
        session.execute(delete(FatoSaude).where(FatoSaude.cod_ibge == cod))
        session.execute(delete(FatoEducacao).where(FatoEducacao.cod_ibge == cod))
        session.execute(delete(FatoDisponibilidade).where(FatoDisponibilidade.cod_ibge == cod))
        session.execute(delete(SiopsSaude).where(SiopsSaude.cod_ibge == cod))
        session.execute(delete(SiopeEducacao).where(SiopeEducacao.cod_ibge == cod))
        session.execute(delete(SilverRreo).where(SilverRreo.cod_ibge == cod))
        session.execute(delete(SilverRgf).where(SilverRgf.cod_ibge == cod))
        session.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
        session.execute(
            delete(DimEntrega).where(DimEntrega.versao_entrega == "siops-falso")
        )
        session.execute(
            delete(DimEntrega).where(DimEntrega.versao_entrega == "siope-falso")
        )
        session.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
        session.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
        session.commit()


def _headers(client, make_org, cod: str) -> dict[str, str]:
    org = make_org(capacidades=["ver"], entes=[cod])
    return auth_header(login(client, org.email, org.senha))


def test_saude_base_expurgo_esfera_e_flag_exata(client, make_org, caso) -> None:
    cod, _ = caso
    response = client.get(
        f"/entes/{cod}/saude", params={"periodo": PERIODO},
        headers=_headers(client, make_org, cod),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert float(body["base_impostos_transferencias"]) == 1000
    assert float(body["rpnp_sem_lastro"]) == 10
    assert float(body["despesa_aplicada"]) == 150
    assert float(body["pct_aplicado"]) == 15
    assert float(body["minimo_pct"]) == 15
    assert body["abaixo_do_minimo"] is False  # igualdade cumpre o piso
    assert body["source_ref"]["anexo"].startswith("Anexo 12")
    assert body["source_ref_expurgo"]["relatorio"] == "RGF"
    assert body["memoria_calculo"]["detalhes"]["base_nao_e_rcl"] is True
    assert "EXCETO" not in " ".join(
        body["memoria_calculo"]["detalhes"]["fontes_rgf_selecionadas"]
    ).upper()


def test_educacao_mde_e_fundeb_70_exatos(client, make_org, caso) -> None:
    cod, _ = caso
    response = client.get(
        f"/entes/{cod}/educacao", params={"periodo": PERIODO},
        headers=_headers(client, make_org, cod),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert float(body["despesa_bruta"]) == 260
    assert float(body["deducoes_outras"]) == 7
    assert float(body["rpnp_sem_lastro"]) == 3
    assert float(body["despesa_aplicada"]) == 250
    assert float(body["pct_aplicado"]) == 25
    assert body["abaixo_do_minimo"] is False
    assert float(body["fundeb"]["base"]) == 200
    assert float(body["fundeb"]["aplicado_profissionais"]) == 140
    assert float(body["fundeb"]["pct_aplicado"]) == 70
    assert body["fundeb"]["abaixo_do_minimo"] is False


def test_bimestre_intermediario_usa_expurgo_reportado_quando_rgf_nao_publica_a5(
    client, make_org, caso
) -> None:
    cod, _ = caso
    periodo = f"{ANO}-B2"
    periodo_rgf = f"{ANO}-Q1"
    rows = _rreo(cod)
    for row in rows:
        row.periodo = periodo
        if "EMPENHADAS" in row.coluna:
            row.coluna = "DESPESAS LIQUIDADAS"

    with SessionLocal() as session:
        session.add_all(
            [
                DimEntrega(
                    cod_ibge=cod,
                    relatorio="RREO",
                    periodo=periodo,
                    versao_entrega=RREO_V,
                    homologada_em=datetime(2094, 5, 31, tzinfo=UTC),
                    vigente=True,
                ),
                # A entrega existe, mas, como ocorre em dados reais intermediarios,
                # ainda nao contem o Anexo 5 anual.
                DimEntrega(
                    cod_ibge=cod,
                    relatorio="RGF",
                    periodo=periodo_rgf,
                    versao_entrega="health-edu-rgf-sem-a5",
                    homologada_em=datetime(2094, 6, 30, tzinfo=UTC),
                    vigente=True,
                ),
            ]
        )
        session.add_all(rows)
        session.commit()

    headers = _headers(client, make_org, cod)
    saude = client.get(
        f"/entes/{cod}/saude", params={"periodo": periodo}, headers=headers
    )
    assert saude.status_code == 200, saude.text
    assert float(saude.json()["rpnp_sem_lastro"]) == 10
    assert saude.json()["source_ref_expurgo"] is None
    assert (
        saude.json()["memoria_calculo"]["detalhes"]["metodo_expurgo"]
        == "rreo_fallback_rgf_sem_anexo_5"
    )

    educacao = client.get(
        f"/entes/{cod}/educacao", params={"periodo": periodo}, headers=headers
    )
    assert educacao.status_code == 200, educacao.text
    assert float(educacao.json()["rpnp_sem_lastro"]) == 3
    assert float(educacao.json()["despesa_aplicada"]) == 250


def test_saude_estadual_usa_12_e_abaixo_semantica_invertida(client, make_org, caso) -> None:
    cod, _ = caso
    with SessionLocal() as session:
        silver = session.get(SilverEnte, cod)
        assert silver is not None
        silver.esfera = "E"
        # 129 - RPNP 10 = 119 => 11,9%, abaixo do piso estadual de 12%.
        row = session.scalar(
            select(SilverRreo).where(
                SilverRreo.cod_ibge == cod,
                SilverRreo.cod_conta == "ASPS_DESPESA_TOTAL",
            )
        )
        assert row is not None
        row.valor = Decimal("129")
        session.commit()
    response = client.get(
        f"/entes/{cod}/saude", params={"periodo": PERIODO},
        headers=_headers(client, make_org, cod),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["esfera"] == "estadual"
    assert float(body["minimo_pct"]) == 12
    assert float(body["pct_aplicado"]) == 11.9
    assert body["abaixo_do_minimo"] is True


def test_arvores_projecao_e_escopo(client, make_org, caso) -> None:
    cod, _ = caso
    headers = _headers(client, make_org, cod)
    saude = client.get(
        f"/entes/{cod}/saude/arvore", params={"periodo": PERIODO, "node": "10"},
        headers=headers,
    )
    assert saude.status_code == 200, saude.text
    assert {item["codigo"] for item in saude.json()["children"]} >= {
        "10.ATENCAO_BASICA", "10.ASSISTENCIA_HOSPITALAR_E_AMBULATORIAL"
    }
    educacao = client.get(
        f"/entes/{cod}/educacao/arvore", params={"periodo": PERIODO, "node": "MDE"},
        headers=headers,
    ).json()
    assert {item["codigo"] for item in educacao["children"]} == {
        "MDE.IMPOSTOS", "MDE.FUNDEB"
    }
    proj = client.get(
        f"/entes/{cod}/minimos/projecao", params={"periodo": PERIODO}, headers=headers,
    )
    assert proj.status_code == 200, proj.text
    assert proj.json()["saude"]["source_ref"]["relatorio"] == "RREO"

    fora = make_org(capacidades=["ver"], entes=[])
    negado = client.get(
        f"/entes/{cod}/saude", params={"periodo": PERIODO},
        headers=auth_header(login(client, fora.email, fora.senha)),
    )
    assert negado.status_code == 403


def test_siops_nao_sobrescreve_minimo_primario(client, make_org, caso) -> None:
    cod, _ = caso
    with SessionLocal() as session:
        session.add(
            DimEntrega(
                cod_ibge="BR", relatorio="SIOPS", periodo=PERIODO,
                versao_entrega="siops-falso", homologada_em=datetime(2095, 3, 1, tzinfo=UTC),
                vigente=True,
            )
        )
        session.add(
            SiopsSaude(
                cod_ibge=cod, ano=ANO, bimestre=6, indicador_codigo="PCT_ASPS",
                valor=Decimal("99.99"), versao_entrega="siops-falso",
            )
        )
        session.commit()
    headers = _headers(client, make_org, cod)
    detalhe = client.get(
        f"/entes/{cod}/saude", params={"periodo": PERIODO}, headers=headers,
    ).json()
    enriquecimento = client.get(
        f"/entes/{cod}/saude/detalhamento-siops",
        params={"periodo": PERIODO}, headers=headers,
    )
    assert enriquecimento.status_code == 200, enriquecimento.text
    assert float(detalhe["pct_aplicado"]) == 15
    assert float(enriquecimento.json()["itens"][0]["valor"]) == 99.99
    assert enriquecimento.json()["nao_substitui_base"] is True


def test_siope_nao_sobrescreve_mde_primaria(client, make_org, caso) -> None:
    cod, _ = caso
    with SessionLocal() as session:
        session.add(
            DimEntrega(
                cod_ibge="BR", relatorio="SIOPE", periodo=PERIODO,
                versao_entrega="siope-falso", homologada_em=datetime(2095, 3, 2, tzinfo=UTC),
                vigente=True,
            )
        )
        session.add(
            SiopeEducacao(
                cod_ibge=cod, ano=ANO, bimestre=6, indicador_codigo="PCT_MDE",
                valor=Decimal("1.23"), versao_entrega="siope-falso",
            )
        )
        session.commit()
    headers = _headers(client, make_org, cod)
    detalhe = client.get(
        f"/entes/{cod}/educacao", params={"periodo": PERIODO}, headers=headers,
    ).json()
    enriquecimento = client.get(
        f"/entes/{cod}/educacao/detalhamento-siope",
        params={"periodo": PERIODO}, headers=headers,
    )
    assert enriquecimento.status_code == 200, enriquecimento.text
    assert float(detalhe["pct_aplicado"]) == 25
    assert any(
        item["codigo"] == "PCT_MDE" and float(item["valor"]) == 1.23
        for item in enriquecimento.json()["itens"]
    )
    assert enriquecimento.json()["nao_substitui_base"] is True
