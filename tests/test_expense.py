"""Testes da Despesa (Módulo 5) — Sprint 6, formato **real** do SICONFI.

Função (Anexo 02): a hierarquia vem do **nome** (Portaria 42) + ordem — subfunção é a
linha seguinte até a próxima função. Natureza (Anexo 01, lado despesa): vem do slug
estável ``cod_conta``. Cobrem os aceites: invariante empenhado≥liquidado≥pago; lacuna
empenhado−pago = potencial RAP; drill por função E por natureza; agregação consistente.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.modules.expense.models import FatoDespesa
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega, SilverEnte, SilverRreo
from tests.conftest import auth_header, login

PERIODO = "2024-B6"
ANEXO02 = "RREO-Anexo 02"
ANEXO01 = "RREO-Anexo 01"
COD_FUNCAO = "RREO2TotalDespesas"  # cod_conta genérico do Anexo 02 (a hierarquia vem do nome)

# Colunas reais (Anexo 02 = função; Anexo 01 = natureza, sufixo diferente).
F_DOT_ATU = "DOTAÇÃO ATUALIZADA (a)"
F_EMP = "DESPESAS EMPENHADAS ATÉ O BIMESTRE (b)"
F_LIQ = "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (d)"
F_RAP = "INSCRITAS EM RESTOS A PAGAR NÃO PROCESSADOS (f)"
F_EMP_NO_BIM = "DESPESAS EMPENHADAS NO BIMESTRE"
N_DOT_ATU = "DOTAÇÃO ATUALIZADA (e)"
N_EMP = "DESPESAS EMPENHADAS ATÉ O BIMESTRE (f)"
N_LIQ = "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)"
N_PAG = "DESPESAS PAGAS ATÉ O BIMESTRE (j)"
N_RAP = "INSCRITAS EM RESTOS A PAGAR NÃO PROCESSADOS (k)"

# Anexo 02 (função): (linha_seq, conta, dot_atu, empenhado, liquidado, inscrito_rap).
_A02: list[tuple[int, str, str | None, str | None, str | None, str | None]] = [
    (1, "DESPESAS (EXCETO INTRA-ORÇAMENTÁRIAS) (I)", "900", "700", "600", None),  # total → fora
    (2, "Saúde", "500", "400", "350", "100"),
    (3, "Atenção Básica", None, "250", "220", None),
    (4, "Assistência Hospitalar e Ambulatorial", None, "150", "130", None),
    (5, "Educação", "400", "300", "250", "50"),
    (6, "Ensino Fundamental", None, "300", "250", None),
    (7, "TOTAL (III) = (I + II)", "900", "700", "600", None),  # total → fora
]

# Anexo 01 (natureza): (cod_conta, conta, dot_atu, empenhado, liquidado, pago, inscrito_rap).
_A01: list[tuple[str, str, str | None, str | None, str | None, str | None, str | None]] = [
    ("DespesasExcetoIntraOrcamentarias", "DESPESAS (EXCETO INTRA-ORÇAMENTÁRIAS) (VIII)",
     "900", "700", "600", "500", None),  # total → fora (não está em NATUREZA_PARENT)
    ("DespesasCorrentes", "DESPESAS CORRENTES", "660", "550", "515", "470", "80"),
    ("PessoalEEncargosSociais", "PESSOAL E ENCARGOS SOCIAIS", "400", "350", "340", "330", "20"),
    ("JurosEEncargosDaDivida", "JUROS E ENCARGOS DA DÍVIDA", "60", "50", "45", "40", "10"),
    ("OutrasDespesasCorrentes", "OUTRAS DESPESAS CORRENTES", "200", "150", "130", "100", "50"),
    ("DespesasDeCapital", "DESPESAS DE CAPITAL", "240", "150", "85", "30", "70"),
    ("Investimentos", "INVESTIMENTOS", "200", "120", "75", "25", "60"),
    ("AmortizacaoDaDivida", "AMORTIZAÇÃO DA DÍVIDA", "40", "30", "10", "5", "10"),
    ("SubtotalDasDespesas", "SUBTOTAL DAS DESPESAS (X)", "900", "700", "600", "500", None),  # fora
]


def _ente() -> str:
    return "5" + "".join(random.choices("0123456789", k=6))


@pytest.fixture
def limpar() -> Iterator[list[str]]:
    usados: list[str] = []
    yield usados
    with SessionLocal() as s:
        for cod in usados:
            s.execute(delete(FatoDespesa).where(FatoDespesa.cod_ibge == cod))
            s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
            s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
            s.execute(delete(SilverRreo).where(SilverRreo.cod_ibge == cod))
            s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
            s.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
        s.commit()


def _seed_despesa(
    cod: str,
    *,
    a02: list[tuple[int, str, str | None, str | None, str | None, str | None]] | None = None,
    a01: list[tuple[str, str, str | None, str | None, str | None, str | None, str | None]]
    | None = None,
    periodo: str = PERIODO,
    versao: str = "1",
    homologada_em: datetime | None = None,
    nova_entrega: bool = True,
) -> None:
    """Ente com RREO Anexo 02 (função), Anexo 01 (natureza) e Anexo 03 (RCL contexto)."""
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente", uf="CE", esfera="M"))
        if nova_entrega:
            s.add(
                DimEntrega(
                    cod_ibge=cod, relatorio="RREO", periodo=periodo, versao_entrega=versao,
                    homologada_em=homologada_em or datetime(2025, 1, 10, tzinfo=UTC),
                    vigente=True,
                )
            )
        for seq, conta, dot, emp, liq, rap in a02 or _A02:
            for coluna, valor in (
                (F_DOT_ATU, dot), (F_EMP, emp), (F_LIQ, liq), (F_RAP, rap)
            ):
                if valor is None:
                    continue
                s.add(SilverRreo(
                    cod_ibge=cod, periodo=periodo, anexo=ANEXO02, cod_conta=COD_FUNCAO,
                    conta=conta, coluna=coluna, linha_seq=seq, valor=Decimal(valor),
                    versao_entrega=versao,
                ))
        for cc, conta, dot, emp, liq, pago, rap in a01 or _A01:
            for coluna, valor in (
                (N_DOT_ATU, dot), (N_EMP, emp), (N_LIQ, liq), (N_PAG, pago), (N_RAP, rap)
            ):
                if valor is None:
                    continue
                s.add(SilverRreo(
                    cod_ibge=cod, periodo=periodo, anexo=ANEXO01, cod_conta=cc,
                    conta=conta, coluna=coluna, valor=Decimal(valor), versao_entrega=versao,
                ))
        s.add(SilverRreo(
            cod_ibge=cod, periodo=periodo, anexo="RREO-Anexo 03",
            conta="Receitas Correntes (I)", coluna="12M", valor=Decimal("10000000"),
            versao_entrega=versao,
        ))
        s.commit()


def _codigo_por_descricao(children: list[dict], descricao: str) -> str:
    return next(c["codigo"] for c in children if c["descricao"] == descricao)


def test_arvore_drill_por_funcao_e_por_natureza(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_despesa(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    def arvore(node: str | None, eixo: str) -> dict:
        params = {"periodo": PERIODO, "eixo": eixo}
        if node is not None:
            params["node"] = node
        return client.get(
            f"/entes/{cod}/despesa/arvore", params=params, headers=auth_header(token)
        ).json()

    # --- Eixo FUNÇÃO (Anexo 02) ---
    raiz_f = arvore(None, "funcao")
    funcs = {c["descricao"]: c for c in raiz_f["children"]}
    assert set(funcs) == {"Saúde", "Educação"}
    assert float(funcs["Saúde"]["measures"]["empenhado"]) == 400.0
    assert raiz_f["source_ref"]["anexo"] == "Anexo 02"

    n_saude = arvore("10", "funcao")  # Saúde = código 10 (Portaria 42)
    soma = sum(float(c["measures"]["empenhado"]) for c in n_saude["children"])
    assert soma == float(n_saude["measures"]["empenhado"]) == 400.0  # pai = soma dos filhos
    sub = _codigo_por_descricao(n_saude["children"], "Atenção Básica")
    folha = arvore(sub, "funcao")
    assert [b["codigo"] for b in folha["breadcrumb"]] == ["10"]  # drill UP até a função
    assert folha["children"] == []

    # --- Eixo NATUREZA (Anexo 01) ---
    raiz_n = arvore(None, "natureza")
    assert [c["codigo"] for c in raiz_n["children"]] == ["DespesasCorrentes", "DespesasDeCapital"]
    assert float(raiz_n["children"][0]["measures"]["empenhado"]) == 550.0
    assert raiz_n["source_ref"]["anexo"] == "Anexo 01"

    n3 = arvore("DespesasCorrentes", "natureza")
    soma_n = sum(float(c["measures"]["empenhado"]) for c in n3["children"])
    assert soma_n == float(n3["measures"]["empenhado"]) == 550.0


def test_detalhe_totais_e_eixo(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_despesa(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    det = client.get(
        f"/entes/{cod}/despesa", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert det["eixo"] == "funcao"
    assert float(det["totais"]["empenhado"]) == 700.0
    assert float(det["totais"]["dotacao_atualizada"]) == 900.0
    assert float(det["rcl_12m"]) == 10_000_000.0
    assert det["source_ref"]["anexo"] == "Anexos 01 e 02"

    det_n = client.get(
        f"/entes/{cod}/despesa",
        params={"periodo": PERIODO, "eixo": "natureza"}, headers=auth_header(token),
    ).json()
    assert float(det_n["totais"]["empenhado"]) == 700.0  # eixos reconciliam
    assert float(det_n["potencial_rap"]) == 200.0  # empenhado − pago (natureza tem pago)
    assert [c["codigo"] for c in det_n["composicao"]] == ["DespesasCorrentes", "DespesasDeCapital"]


def test_estagios_cascata_e_lacunas(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_despesa(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    # Eixo natureza tem todos os estágios (inclui pago).
    est = client.get(
        f"/entes/{cod}/despesa/estagios",
        params={"periodo": PERIODO, "eixo": "natureza"}, headers=auth_header(token),
    ).json()
    lac = {item["nome"]: item for item in est["lacunas"]}
    assert float(lac["potencial_rap"]["valor"]) == 200.0  # empenhado − pago = potencial RAP
    assert lac["potencial_rap"]["formula"] == "empenhado − pago"
    assert float(lac["a_liquidar"]["valor"]) == 100.0  # 700 − 600
    assert float(lac["a_pagar"]["valor"]) == 100.0  # 600 − 500


def test_execucao_ritmo_vs_calendario(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_despesa(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    exe = client.get(
        f"/entes/{cod}/despesa/execucao", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert exe["bimestre"] == 6
    assert float(exe["esperado_pct"]) == 100.0
    emp = {e["estagio"]: e for e in exe["estagios"]}["empenhado"]
    assert float(emp["base_dotacao"]) == 900.0
    assert emp["status"] == "atrasado"  # 77,8% empenhado ante 100% esperado


def test_rigidez_por_grupo_de_natureza(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_despesa(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    rig = client.get(
        f"/entes/{cod}/despesa/rigidez", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert float(rig["despesa_total"]) == 700.0
    assert float(rig["rigida"]) == 430.0  # pessoal 350 + juros 50 + amortização 30
    assert float(rig["discricionaria"]) == 120.0  # investimentos
    assert float(rig["semivariavel"]) == 150.0  # outras despesas correntes
    comps = {c["grupo"]: c for c in rig["componentes"]}
    assert comps["PessoalEEncargosSociais"]["tipo"] == "rigida"
    assert comps["Investimentos"]["tipo"] == "discricionaria"
    assert comps["OutrasDespesasCorrentes"]["tipo"] == "semivariavel"


def test_memoria_reconcilia_eixos_e_verifica_agregacao(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_despesa(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    mem = client.get(
        f"/entes/{cod}/despesa/memoria", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert mem["reconciliacao_eixos_ok"] is True
    assert float(mem["diferenca_eixos"]) == 0.0
    assert mem["inconsistencias"] == []
    assert mem["violacoes_estagio"] == []
    assert float(mem["totais_funcao"]["empenhado"]) == 700.0
    assert float(mem["totais_natureza"]["empenhado"]) == 700.0


def test_memoria_flagra_violacao_de_estagio(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_despesa(
        cod,
        a02=[(2, "Administração", "200", "100", "150", None)],  # liquidado > empenhado
        a01=[
            ("DespesasCorrentes", "DESPESAS CORRENTES", "200", "100", "150", "80", None),
            ("PessoalEEncargosSociais", "PESSOAL E ENCARGOS SOCIAIS", "200", "100", "150", "80",
             None),
        ],
    )
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    mem = client.get(
        f"/entes/{cod}/despesa/memoria", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    violacoes = {(v["eixo"], v["codigo"]) for v in mem["violacoes_estagio"]}
    assert ("funcao", "04") in violacoes  # Administração = código 04
    assert ("natureza", "PessoalEEncargosSociais") in violacoes


def test_as_of_reproduz_versao_anterior(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_despesa(cod)  # v1 vigente
    with SessionLocal() as s:
        s.execute(
            DimEntrega.__table__.update()
            .where(DimEntrega.cod_ibge == cod, DimEntrega.versao_entrega == "1")
            .values(vigente=False)
        )
        s.commit()
    _seed_despesa(
        cod,
        versao="2",
        homologada_em=datetime(2025, 3, 1, tzinfo=UTC),
        a02=[(2, "Saúde", "600", "500", "400", None), (5, "Educação", "400", "300", "250", None)],
        a01=[("DespesasCorrentes", "DESPESAS CORRENTES", "900", "800", "650", "500", None)],
    )
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    vigente = client.get(
        f"/entes/{cod}/despesa", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert vigente["versao_entrega"] == "2"
    assert float(vigente["totais"]["empenhado"]) == 800.0

    historico = client.get(
        f"/entes/{cod}/despesa",
        params={"periodo": PERIODO, "as_of": "2025-02-01T00:00:00Z"},
        headers=auth_header(token),
    ).json()
    assert historico["versao_entrega"] == "1"
    assert float(historico["totais"]["empenhado"]) == 700.0


def test_eixo_invalido_retorna_422(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_despesa(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    resp = client.get(
        f"/entes/{cod}/despesa/arvore",
        params={"periodo": PERIODO, "eixo": "poder"}, headers=auth_header(token),
    )
    assert resp.status_code == 422


def test_escopo_403_fora_da_carteira(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_despesa(cod)
    fx = make_org(capacidades=["ver"], entes=[])  # carteira vazia
    token = login(client, fx.email, fx.senha)

    resp = client.get(
        f"/entes/{cod}/despesa", params={"periodo": PERIODO}, headers=auth_header(token)
    )
    assert resp.status_code == 403
    assert resp.json()["title"] == "Ente fora do escopo"
