"""Testes dos indicadores base — RCL (Anexo 03) e classificação de limites por esfera."""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.modules.catalog.models import DimEnte
from app.modules.indicators import service as indicators_service
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega, SilverEnte, SilverRreo
from app.shared.source_ref import COMPOSITE_VERSION_PREFIX, SourceRef, composite_version_key
from tests.conftest import auth_header, login

PERIODO = "2024-B6"


def _ente() -> str:
    return "7" + "".join(random.choices("0123456789", k=6))


def _seed_ente(cod: str, esfera: str) -> None:
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente", uf="CE", esfera=esfera))
        s.commit()


def _seed_rreo(
    cod: str,
    versao: str,
    linhas: list[tuple[str, str, int]],
    homologada: datetime,
    *,
    vigente: bool = True,
) -> None:
    with SessionLocal() as s:
        s.add(
            DimEntrega(
                cod_ibge=cod, relatorio="RREO", periodo=PERIODO, versao_entrega=versao,
                homologada_em=homologada, vigente=vigente,
            )
        )
        for conta, coluna, valor in linhas:
            s.add(
                SilverRreo(
                    cod_ibge=cod, periodo=PERIODO, anexo="RREO-Anexo 03", conta=conta,
                    coluna=coluna, valor=Decimal(str(valor)), versao_entrega=versao,
                )
            )
        s.commit()


# RCL = Receitas Correntes (I) − deduções (RPPS + compensação + FUNDEB)
_LINHAS_RCL = [
    ("Total das Receitas Correntes (I)", "12M", 12_000_000),
    ("Contribuição do Servidor - RPPS", "12M", 500_000),
    ("Compensação Financeira entre Regimes", "12M", 100_000),
    ("Dedução de Receita para o FUNDEB", "12M", 900_000),
    ("RECEITA CORRENTE LÍQUIDA (III)", "12M", 10_500_000),  # linha-resultado (ignorada)
]


def test_chave_versao_composta_estavel_e_sensivel_a_retificacao() -> None:
    rgf_v1 = SourceRef(
        relatorio="RGF", anexo="Anexo 01", periodo="2024-Q3", versao_entrega="1"
    )
    rgf_v2 = rgf_v1.model_copy(update={"versao_entrega": "2"})
    rreo = SourceRef(
        relatorio="RREO", anexo="Anexo 03", periodo=PERIODO, versao_entrega="1"
    )

    chave = composite_version_key((rgf_v1, rreo))
    assert chave == (
        "cmp:v1:b6a1b5eb72b621aff8c8136af18f70cf"
        "56de68f7d9a90b2b3ccf2ee98260c70c"
    )
    assert chave.startswith(COMPOSITE_VERSION_PREFIX)
    assert chave == composite_version_key((rreo, rgf_v1))
    assert chave != composite_version_key((rgf_v2, rreo))


def test_limite_composto_separa_historico_da_projecao_vigente(limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_ente(cod, "M")
    homologada = datetime(2025, 1, 10, tzinfo=UTC)
    _seed_rreo(cod, "1", _LINHAS_RCL, homologada)
    componentes = (
        SourceRef(
            relatorio="RGF", anexo="Anexo 01", periodo="2024-Q3", versao_entrega="7"
        ),
        SourceRef(
            relatorio="RREO", anexo="Anexo 03", periodo=PERIODO, versao_entrega="1"
        ),
    )
    source_ref = SourceRef(
        relatorio="RGF/RREO",
        anexo="RGF Anexo 01 / RREO Anexo 03",
        periodo=f"2024-Q3 / {PERIODO}",
        versao_entrega="RGF:7;RREO:1",
    )

    with SessionLocal() as s:
        historico = indicators_service.classificar_limite(
            s,
            cod,
            PERIODO,
            "pessoal_executivo",
            Decimal("5000000"),
            poder="Executivo",
            as_of=datetime(2025, 2, 1, tzinfo=UTC),
            source_ref=source_ref,
            source_components=componentes,
        )
        s.commit()
        rows = list(
            s.scalars(
                select(MartIndicador).where(
                    MartIndicador.cod_ibge == cod,
                    MartIndicador.indicador == "pessoal_executivo",
                )
            )
        )

    assert historico.versao_entrega == composite_version_key(componentes)
    assert [row.versao_entrega for row in rows] == [historico.versao_entrega]
    assert rows[0].source_ref["tipo_registro"] == "historico_composto"

    with SessionLocal() as s:
        vigente = indicators_service.classificar_limite(
            s,
            cod,
            PERIODO,
            "pessoal_executivo",
            Decimal("5000000"),
            poder="Executivo",
            source_ref=source_ref,
            source_components=componentes,
        )
        s.commit()
        rows = list(
            s.scalars(
                select(MartIndicador).where(
                    MartIndicador.cod_ibge == cod,
                    MartIndicador.indicador == "pessoal_executivo",
                )
            )
        )

    assert vigente.versao_entrega == historico.versao_entrega
    assert {row.versao_entrega for row in rows} == {historico.versao_entrega, "1"}
    projection = next(row for row in rows if row.versao_entrega == "1")
    assert projection.source_ref["tipo_registro"] == "projecao_vigente"
    assert projection.source_ref["chave_versao_composta"] == historico.versao_entrega


@pytest.fixture
def limpar() -> Iterator[list[str]]:
    usados: list[str] = []
    yield usados
    with SessionLocal() as s:
        for cod in usados:
            s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
            s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
            s.execute(delete(SilverRreo).where(SilverRreo.cod_ibge == cod))
            s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
            s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
            s.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
        s.commit()


def test_rcl_caso_conhecido(limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_ente(cod, "M")
    _seed_rreo(cod, "1", _LINHAS_RCL, datetime(2025, 1, 10, tzinfo=UTC))

    with SessionLocal() as s:
        fato = indicators_service.calcular_rcl(s, cod, PERIODO)
        s.commit()
    assert fato.rcl_12m == Decimal("10500000")
    assert fato.receita_corrente == Decimal("12000000")
    assert fato.deducoes == Decimal("1500000")


# Anexo 03 real (SICONFI) traz o subtotal (I)/(II) E os itens-filhos, na coluna real de 12
# meses. Somar os dois duplicaria — a RCL deve usar só os subtotais marcados.
_LINHAS_RCL_SUBTOTAIS = [
    ("RECEITAS CORRENTES (I)", "TOTAL (ÚLTIMOS 12 MESES)", 12_000_000),
    ("Impostos, Taxas e Contribuições de Melhoria", "TOTAL (ÚLTIMOS 12 MESES)", 8_000_000),
    ("Outras Receitas Correntes", "TOTAL (ÚLTIMOS 12 MESES)", 4_000_000),
    ("DEDUÇÕES (II)", "TOTAL (ÚLTIMOS 12 MESES)", 1_500_000),
    ("Contribuição do Servidor para o RPPS", "TOTAL (ÚLTIMOS 12 MESES)", 500_000),
    ("Dedução de Receita para Formação do FUNDEB", "TOTAL (ÚLTIMOS 12 MESES)", 1_000_000),
    ("RECEITA CORRENTE LÍQUIDA (III)", "TOTAL (ÚLTIMOS 12 MESES)", 10_500_000),
]


def test_rcl_prefere_subtotais_e_nao_duplica(limpar) -> None:
    """Regressão do bug de dupla contagem: soma só (I)/(II), não os filhos."""
    cod = _ente()
    limpar.append(cod)
    _seed_ente(cod, "M")
    _seed_rreo(cod, "1", _LINHAS_RCL_SUBTOTAIS, datetime(2025, 1, 10, tzinfo=UTC))

    with SessionLocal() as s:
        fato = indicators_service.calcular_rcl(s, cod, PERIODO)
        s.commit()
    # (I) 12M − (II) 1,5M = 10,5M — e não (12M+8M+4M) − (1,5M+0,5M+1M).
    assert fato.receita_corrente == Decimal("12000000")
    assert fato.deducoes == Decimal("1500000")
    assert fato.rcl_12m == Decimal("10500000")


def test_rcl_endpoint_memoria_e_drill(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_ente(cod, "M")
    _seed_rreo(cod, "1", _LINHAS_RCL, datetime(2025, 1, 10, tzinfo=UTC))
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    resp = client.get(f"/entes/{cod}/rcl", params={"periodo": PERIODO}, headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert float(body["rcl_12m"]) == 10_500_000.0
    assert len(body["deducoes"]) == 3  # drill DOWN das deduções
    assert body["source_ref"]["relatorio"] == "RREO"
    assert body["source_ref"]["anexo"] == "Anexo 03"


def test_rcl_as_of_retorna_versao_historica(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_ente(cod, "M")
    _seed_rreo(cod, "1", [("Receitas Correntes (I)", "12M", 10_000_000)],
               datetime(2025, 1, 10, tzinfo=UTC), vigente=False)
    _seed_rreo(cod, "2", [("Receitas Correntes (I)", "12M", 11_000_000)],
               datetime(2025, 3, 15, tzinfo=UTC), vigente=True)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    vig = client.get(
        f"/entes/{cod}/rcl", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert vig["versao_entrega"] == "2"
    assert float(vig["rcl_12m"]) == 11_000_000.0

    hist = client.get(
        f"/entes/{cod}/rcl",
        params={"periodo": PERIODO, "as_of": "2025-02-01T00:00:00Z"},
        headers=auth_header(token),
    ).json()
    assert hist["versao_entrega"] == "1"
    assert float(hist["rcl_12m"]) == 10_000_000.0


def test_limite_pessoal_varia_por_esfera(limpar) -> None:
    """Mesmo % da RCL (50%) classifica diferente: município (teto 54) × estado (teto 49)."""
    linhas = [("Receitas Correntes (I)", "12M", 10_000_000)]
    cod_m, cod_e = _ente(), _ente()
    limpar.extend([cod_m, cod_e])
    _seed_ente(cod_m, "M")
    _seed_rreo(cod_m, "1", linhas, datetime(2025, 1, 10, tzinfo=UTC))
    _seed_ente(cod_e, "E")
    _seed_rreo(cod_e, "1", linhas, datetime(2025, 1, 10, tzinfo=UTC))

    with SessionLocal() as s:
        ind_m = indicators_service.classificar_limite(
            s, cod_m, PERIODO, "pessoal_executivo", Decimal("5000000"), poder="Executivo"
        )
        ind_e = indicators_service.classificar_limite(
            s, cod_e, PERIODO, "pessoal_executivo", Decimal("5000000"), poder="Executivo"
        )
        s.commit()

    assert ind_m.esfera == "municipal"
    assert ind_m.valor_pct_rcl == Decimal("50")
    assert ind_m.faixa == "alerta"  # 50% entre 48,6 (alerta) e 51,3 (prudencial) do teto 54
    assert ind_e.esfera == "estadual"
    assert ind_e.faixa == "excedido"  # 50% ≥ teto 49
