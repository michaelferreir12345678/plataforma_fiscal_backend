"""Testes do Resultado Fiscal (Módulo 8) — Sprint 9, formato **real** do SICONFI (Anexo 6).

Cobrem os aceites: primário = receitas − despesas primárias; nominal = primário − juros;
nominal (abaixo) = −variação da DCL; ajustes metodológicos reconciliam acima × abaixo;
meta × realizado + projeção; e reprodução bitemporal (as_of).
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.modules.ingestion.models import DimEntrega, SilverRreo
from app.modules.result.models import FatoAjusteMetodologico, FatoResultado
from tests.conftest import auth_header, login

PERIODO = "2024-B6"
ANEXO = "RREO-Anexo 06"
REC_REAL = "RECEITAS REALIZADAS (a)"
D_PAGAS = "DESPESAS PAGAS (a)"
D_RP = "RESTOS A PAGAR PROCESSADOS PAGOS (b)"
D_PAGOS_C = "PAGOS (c)"
D_EMP = "DESPESAS EMPENHADAS"
VALOR = "VALOR"
DCL_INI = "Em 31/12/2023 (a)"
DCL_FIM = "Até o Bimestre 2024 (b)"

# Anexo 6 no formato real: (linha_seq, cod_conta, coluna, valor). Coerente:
# primário 200 = receita 1000 − despesa 800; nominal 150; DCL 500→700 (nominal abaixo −200).
_A6: list[tuple[int, str, str, str]] = [
    (10, "RREO6TotalReceitaPrimaria", REC_REAL, "1000"),
    (11, "RREO6TransferenciasCorrentes", REC_REAL, "600"),
    (12, "RREO6ReceitasTributarias", REC_REAL, "300"),
    (20, "RREO6TotalDespesaPrimaria", D_PAGAS, "700"),
    (21, "RREO6TotalDespesaPrimaria", D_RP, "50"),
    (22, "RREO6TotalDespesaPrimaria", D_PAGOS_C, "50"),
    (23, "RREO6PessoalEEncargosSociais", D_EMP, "500"),
    (24, "RREO6Investimentos", D_EMP, "120"),
    (30, "ResultadoPrimarioComRPPSAcimaDaLinha", VALOR, "200"),
    (31, "ResultadoNominalAcimaDaLinhaSemRPPS", VALOR, "150"),
    (40, "DividaConsolidadaLiquida", DCL_INI, "500"),
    (41, "DividaConsolidadaLiquida", DCL_FIM, "700"),
    (42, "ResultadoNominalAbaixoDaLinhaSemRPPS", VALOR, "-200"),
    (50, "VariacaoCambial", VALOR, "-50"),
    (51, "OutrosAjustes", VALOR, "100"),
    (60, "RREO6MetaDeResultadoPrimarioFixadaNoAn", VALOR, "250"),
]


def _ente() -> str:
    return "2" + "".join(random.choices("0123456789", k=6))


@pytest.fixture
def limpar() -> Iterator[list[str]]:
    usados: list[str] = []
    yield usados
    with SessionLocal() as s:
        for cod in usados:
            s.execute(delete(FatoAjusteMetodologico).where(FatoAjusteMetodologico.cod_ibge == cod))
            s.execute(delete(FatoResultado).where(FatoResultado.cod_ibge == cod))
            s.execute(delete(SilverRreo).where(SilverRreo.cod_ibge == cod))
            s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
        s.commit()


def _seed(
    cod: str,
    *,
    linhas: list[tuple[int, str, str, str]] | None = None,
    versao: str = "1",
    homologada_em: datetime | None = None,
    nova_entrega: bool = True,
    periodo: str = PERIODO,
) -> None:
    with SessionLocal() as s:
        if nova_entrega:
            s.add(
                DimEntrega(
                    cod_ibge=cod, relatorio="RREO", periodo=periodo, versao_entrega=versao,
                    homologada_em=homologada_em or datetime(2025, 1, 10, tzinfo=UTC), vigente=True,
                )
            )
        for seq, cod_conta, coluna, valor in linhas or _A6:
            s.add(
                SilverRreo(
                    cod_ibge=cod, periodo=periodo, anexo=ANEXO, cod_conta=cod_conta,
                    conta=cod_conta, coluna=coluna, linha_seq=seq, valor=Decimal(valor),
                    versao_entrega=versao,
                )
            )
        s.commit()


def test_detalhe_primario_nominal_e_componentes(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    det = client.get(
        f"/entes/{cod}/resultado", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    v = det["valores"]
    assert float(v["receita_primaria"]) == 1000.0
    assert float(v["despesa_primaria"]) == 800.0  # pagas 700 + RP 50 + pagos(c) 50
    assert float(v["resultado_primario"]) == 200.0  # = receita − despesa
    assert float(v["resultado_nominal"]) == 150.0
    assert float(v["juros_liquidos"]) == 50.0  # derivado: primário − nominal
    assert float(v["dcl_inicio"]) == 500.0
    assert float(v["dcl_fim"]) == 700.0
    assert float(v["variacao_dcl"]) == 200.0
    assert float(v["resultado_nominal_abaixo"]) == -200.0
    # Drill de componentes primários (cruza receita/despesa).
    rec = {c["codigo"]: c for c in det["receita_componentes"]}
    assert float(rec["RREO6TransferenciasCorrentes"]["valor"]) == 600.0
    desp = {c["codigo"]: c for c in det["despesa_componentes"]}
    assert float(desp["RREO6PessoalEEncargosSociais"]["valor"]) == 500.0
    assert det["source_ref"]["anexo"] == "Anexo 06"
    assert [b["codigo"] for b in det["periodo_breadcrumb"]] == ["2024"]


def test_cascata_waterfall(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    cas = client.get(
        f"/entes/{cod}/resultado/cascata", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    acima = {p["rotulo"]: p for p in cas["acima_da_linha"]}
    assert float(acima["Receita primária"]["acumulado"]) == 1000.0
    assert float(acima["= Resultado primário"]["acumulado"]) == 200.0
    assert float(acima["= Resultado nominal"]["acumulado"]) == 150.0
    abaixo = {p["rotulo"]: p for p in cas["abaixo_da_linha"]}
    assert float(abaixo["DCL início do exercício"]["valor"]) == 500.0
    assert float(abaixo["= DCL até o bimestre"]["valor"]) == 700.0


def test_reconciliacao_identidades_e_ajustes(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    rec = client.get(
        f"/entes/{cod}/resultado/reconciliacao",
        params={"periodo": PERIODO}, headers=auth_header(token),
    ).json()
    # Identidades do domínio (aceite).
    assert rec["identidade_primario_ok"] is True  # primário = receita − despesa
    assert rec["identidade_nominal_dcl_ok"] is True  # nominal abaixo = início − fim = −ΔDCL
    assert float(rec["nominal_acima"]) == 150.0
    assert float(rec["nominal_abaixo"]) == -200.0
    ajustes = {a["codigo"]: a for a in rec["ajustes"]}
    assert float(ajustes["VariacaoCambial"]["valor"]) == -50.0
    assert float(ajustes["OutrosAjustes"]["valor"]) == 100.0
    assert float(rec["soma_ajustes"]) == 50.0
    assert float(rec["nominal_abaixo_ajustado"]) == -150.0  # −200 + 50 (ajustes explicam)
    assert float(rec["divergencia_acima_abaixo"]) == 300.0  # 150 − (−150)
    # Cruzamento com a Sprint 8: sem RGF ingerido ⇒ indisponível, mas o período mapeia.
    assert rec["dcl_sprint8_periodo"] == "2024-Q3"
    assert rec["concilia_com_sprint8"] is None
    assert "não recalculados" in rec["observacao"]


def test_meta_realizado_projecao_e_esforco(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    meta = client.get(
        f"/entes/{cod}/resultado/meta", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert meta["resumo"]["informada"] is True
    assert float(meta["resumo"]["meta_primario"]) == 250.0
    assert float(meta["resumo"]["realizado_primario"]) == 200.0
    assert float(meta["resumo"]["esforco_primario"]) == 50.0  # meta − realizado
    assert meta["resumo"]["atingido_primario"] is False  # 200 < 250
    assert meta["bimestre"] == 6
    assert float(meta["fracao_exercicio"]) == 1.0
    assert float(meta["projecao_primario"]) == 200.0  # B6 ⇒ fração 1


def test_meta_nao_informada(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod, linhas=[x for x in _A6 if "Meta" not in x[1]])  # sem a linha de meta
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    meta = client.get(
        f"/entes/{cod}/resultado/meta", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert meta["resumo"]["informada"] is False
    assert "não informada" in meta["observacao"]


def test_memoria_rastreavel(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    mem = client.get(
        f"/entes/{cod}/resultado/memoria", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert mem["identidade_primario_ok"] is True
    assert mem["identidade_nominal_dcl_ok"] is True
    assert mem["formula_primario"].startswith("resultado_primario")
    # Origem rastreável de cada número (auditável).
    assert "receita_primaria" in mem["fontes"]
    assert "RREO6TotalReceitaPrimaria" in mem["fontes"]["receita_primaria"]


def test_as_of_reproduz_versao_anterior(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod)  # v1
    with SessionLocal() as s:
        s.execute(
            DimEntrega.__table__.update()
            .where(DimEntrega.cod_ibge == cod, DimEntrega.versao_entrega == "1")
            .values(vigente=False)
        )
        s.commit()
    _seed(
        cod,
        versao="2",
        homologada_em=datetime(2025, 3, 1, tzinfo=UTC),
        linhas=[
            (10, "RREO6TotalReceitaPrimaria", REC_REAL, "1100"),
            (20, "RREO6TotalDespesaPrimaria", D_PAGAS, "800"),
            (30, "ResultadoPrimarioComRPPSAcimaDaLinha", VALOR, "300"),
        ],
    )
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    vigente = client.get(
        f"/entes/{cod}/resultado", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert vigente["versao_entrega"] == "2"
    assert float(vigente["valores"]["resultado_primario"]) == 300.0

    hist = client.get(
        f"/entes/{cod}/resultado",
        params={"periodo": PERIODO, "as_of": "2025-02-01T00:00:00Z"},
        headers=auth_header(token),
    ).json()
    assert hist["versao_entrega"] == "1"
    assert float(hist["valores"]["resultado_primario"]) == 200.0


def test_escopo_403_fora_da_carteira(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod)
    fx = make_org(capacidades=["ver"], entes=[])  # carteira vazia
    token = login(client, fx.email, fx.senha)

    resp = client.get(
        f"/entes/{cod}/resultado", params={"periodo": PERIODO}, headers=auth_header(token)
    )
    assert resp.status_code == 403
    assert resp.json()["title"] == "Ente fora do escopo"
