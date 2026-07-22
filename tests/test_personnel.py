"""Testes da Despesa com Pessoal (Módulo 6) — Sprint 7.

Cobrem os critérios de aceite: exclusões corretas **com/sem RPPS** (art. 19, §1º);
faixa por poder correta (Executivo 54% município / 49% estado); memória rastreável;
drill por poder; e reprodução bitemporal (as_of). RGF é quadrimestral (2024-Q3); a RCL
e a faixa vêm do RREO do bimestre correspondente (B6).
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.modules.catalog.models import DimEnte
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega, SilverEnte, SilverRgf, SilverRreo
from app.modules.personnel.models import FatoPessoal
from app.shared.source_ref import COMPOSITE_VERSION_PREFIX
from tests.conftest import auth_header, login

PERIODO = "2024-Q3"  # RGF quadrimestral
PERIODO_RREO = "2024-B6"  # bimestre correspondente (base da RCL/faixa)
ANEXO = "RGF-Anexo 01"
COL = "DESPESAS LIQUIDADAS (a)"
RCL = Decimal("10000")

# RGF Anexo 1 (Despesa com Pessoal): Executivo (E) com exclusões, Legislativo (L) enxuto.
_RGF_DEFAULT: list[tuple[str, str, str]] = [
    ("E", "DESPESA BRUTA COM PESSOAL (I)", "6000"),
    ("E", "Indenizações por Demissão e Incentivos à Demissão Voluntária", "100"),
    ("E", "Decorrentes de Decisão Judicial de período anterior ao da apuração", "50"),
    ("E", "Despesas de Exercícios Anteriores de período anterior ao da apuração", "50"),
    ("E", "Inativos e Pensionistas com Recursos Vinculados", "800"),  # exclusão só com RPPS
    ("E", "DESPESAS NÃO COMPUTADAS (§ 1º do art. 19 da LRF) (II)", "1000"),
    ("E", "DESPESA LÍQUIDA COM PESSOAL (III) = (I - II)", "5000"),
    ("L", "DESPESA BRUTA COM PESSOAL (I)", "700"),
    ("L", "DESPESA LÍQUIDA COM PESSOAL (III) = (I - II)", "700"),
]

# Sem a linha de líquida/DTP reportada ⇒ a apuração recai no cálculo (bruta − exclusões),
# que é sensível a RPPS (inativos vinculados). Usado no teste de sensibilidade a RPPS.
_RGF_COMPUTADO = [r for r in _RGF_DEFAULT if "LÍQUIDA COM PESSOAL" not in r[1]]


def _ente() -> str:
    return "4" + "".join(random.choices("0123456789", k=6))


@pytest.fixture
def limpar() -> Iterator[list[str]]:
    usados: list[str] = []
    yield usados
    with SessionLocal() as s:
        for cod in usados:
            s.execute(delete(FatoPessoal).where(FatoPessoal.cod_ibge == cod))
            s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
            s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
            s.execute(delete(SilverRgf).where(SilverRgf.cod_ibge == cod))
            s.execute(delete(SilverRreo).where(SilverRreo.cod_ibge == cod))
            s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
            s.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
            s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
        s.commit()


def _seed(
    cod: str,
    *,
    esfera: str = "municipal",
    rpps: bool = False,
    versao: str = "1",
    homologada_em: datetime | None = None,
    nova_entrega: bool = True,
    rgf_rows: list[tuple[str, str, str]] | None = None,
    periodo: str = PERIODO,
    seed_rreo: bool = True,
) -> None:
    """Ente (com flag rpps) + RGF Anexo 1 (por poder) + RREO Anexo 3 (RCL) para o bimestre."""
    homolog = homologada_em or datetime(2025, 1, 10, tzinfo=UTC)
    silver_esfera = "E" if esfera == "estadual" else "M"
    with SessionLocal() as s:
        # dim_ente carrega o rpps (refresh_dim_ente preserva rpps e conforma esfera do silver).
        s.merge(DimEnte(cod_ibge=cod, nome="Ente", esfera=esfera, rpps=rpps))
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente", uf="CE", esfera=silver_esfera))
        if nova_entrega:
            s.add(
                DimEntrega(
                    cod_ibge=cod, relatorio="RGF", periodo=periodo, versao_entrega=versao,
                    homologada_em=homolog, vigente=True,
                )
            )
        for poder, conta, valor in rgf_rows or _RGF_DEFAULT:
            s.add(
                SilverRgf(
                    cod_ibge=cod, periodo=periodo, anexo=ANEXO, poder=poder,
                    conta=conta, coluna=COL, valor=Decimal(valor), versao_entrega=versao,
                )
            )
        # RREO do bimestre correspondente (para a RCL e a faixa do Executivo).
        if seed_rreo:
            s.add(
                DimEntrega(
                    cod_ibge=cod, relatorio="RREO", periodo=PERIODO_RREO, versao_entrega="1",
                    homologada_em=homolog, vigente=True,
                )
            )
            s.add(
                SilverRreo(
                    cod_ibge=cod, periodo=PERIODO_RREO, anexo="RREO-Anexo 03",
                    conta="Receitas Correntes (I)", coluna="12M", valor=RCL, versao_entrega="1",
                )
            )
        s.commit()


def test_exclusoes_sensiveis_a_rpps(client, make_org, limpar) -> None:
    # Mesmo RGF; a flag RPPS muda a exclusão (inativos vinculados) e, logo, a líquida/faixa.
    com = _ente()
    sem = _ente()
    limpar.extend([com, sem])
    _seed(com, rpps=True, rgf_rows=_RGF_COMPUTADO)
    _seed(sem, rpps=False, rgf_rows=_RGF_COMPUTADO)
    fx = make_org(capacidades=["ver"], entes=[com, sem])
    token = login(client, fx.email, fx.senha)

    dcom = client.get(
        f"/entes/{com}/pessoal", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    dsem = client.get(
        f"/entes/{sem}/pessoal", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()

    # Com RPPS: exclui os 800 de inativos ⇒ Executivo líquido 5000 (50% da RCL) ⇒ 'alerta'.
    ex_com = dcom["executivo"]
    assert float(ex_com["despesa_bruta"]) == 6000.0
    assert float(ex_com["exclusoes"]) == 1000.0
    assert float(ex_com["despesa_liquida"]) == 5000.0
    assert float(ex_com["pct_rcl"]) == 50.0
    assert float(ex_com["teto_pct"]) == 54.0  # município
    assert ex_com["faixa"] == "alerta"

    # Sem RPPS: não exclui os 800 ⇒ líquido 5800 (58%) ⇒ acima do teto ⇒ 'excedido'.
    ex_sem = dsem["executivo"]
    assert float(ex_sem["exclusoes"]) == 200.0
    assert float(ex_sem["despesa_liquida"]) == 5800.0
    assert float(ex_sem["pct_rcl"]) == 58.0
    assert ex_sem["faixa"] == "excedido"
    assert dcom["rpps"] is True and dsem["rpps"] is False


def test_faixa_por_esfera_estadual(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod, esfera="estadual", rpps=True)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    pp = client.get(
        f"/entes/{cod}/pessoal/por-poder", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert pp["esfera"] == "estadual"
    executivo = next(i for i in pp["itens"] if i["poder_codigo"] == "ENTE.EXEC")
    assert float(executivo["teto_pct"]) == 49.0  # estado: Executivo 49%
    assert executivo["faixa"] == "excedido"  # 50% > 49%
    assert executivo["indicador"] == "pessoal_executivo"
    # Legislativo não tem teto cadastrado no MVP: sem faixa, mas com pct.
    legislativo = next(i for i in pp["itens"] if i["poder_codigo"] == "ENTE.LEG")
    assert legislativo["faixa"] is None
    assert float(legislativo["pct_rcl"]) == 7.0  # 700 / 10000


def test_arvore_drill_por_poder(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod, rpps=True)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    raiz = client.get(
        f"/entes/{cod}/pessoal/arvore", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert raiz["node"] is None
    assert [c["codigo"] for c in raiz["children"]] == ["ENTE"]  # raiz = consolidado do ente
    # Drill DOWN no consolidado (node=ENTE) = poderes; soma dos filhos = total.
    ente = client.get(
        f"/entes/{cod}/pessoal/arvore",
        params={"periodo": PERIODO, "node": "ENTE"}, headers=auth_header(token),
    ).json()
    soma = sum(float(c["measures"]["despesa_liquida"]) for c in ente["children"])
    assert soma == float(ente["measures"]["despesa_liquida"]) == 5700.0  # 5000 + 700
    assert ente["source_ref"]["relatorio"] == "RGF"

    # Drill UP: breadcrumb do poder até o ente.
    exe = client.get(
        f"/entes/{cod}/pessoal/arvore",
        params={"periodo": PERIODO, "node": "ENTE.EXEC"}, headers=auth_header(token),
    ).json()
    assert [b["codigo"] for b in exe["breadcrumb"]] == ["ENTE"]
    assert exe["children"] == []
    assert float(exe["measures"]["despesa_liquida"]) == 5000.0


def test_memoria_rastreavel_com_rpps(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod, rpps=True)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    mem = client.get(
        f"/entes/{cod}/pessoal/memoria", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert mem["rpps"] is True
    assert float(mem["despesa_bruta"]) == 6700.0  # Exec 6000 + Leg 700
    assert float(mem["exclusoes"]) == 1000.0
    assert float(mem["despesa_liquida"]) == 5700.0
    # Cross-check com a linha reportada do RGF (III) consolidada = 5000 + 700.
    assert float(mem["despesa_liquida_reportada"]) == 5700.0
    assert float(mem["rcl_12m"]) == 10000.0
    detalhe = {e["componente"]: e for e in mem["exclusoes_detalhe"]}
    rpps_item = detalhe["inativos_pensionistas_rpps"]
    assert rpps_item["incluida"] is True
    assert float(rpps_item["valor"]) == 800.0
    assert mem["formula_liquida"].startswith("despesa_liquida = despesa_bruta")
    assert mem["source_ref"]["anexo"] == "Anexo 01"


def test_memoria_sem_rpps_nao_exclui_inativos(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod, rpps=False, rgf_rows=_RGF_COMPUTADO)  # sem líquida reportada ⇒ cálculo
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    mem = client.get(
        f"/entes/{cod}/pessoal/memoria", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    detalhe = {e["componente"]: e for e in mem["exclusoes_detalhe"]}
    assert detalhe["inativos_pensionistas_rpps"]["incluida"] is False
    assert float(mem["exclusoes"]) == 200.0
    assert float(mem["despesa_liquida"]) == 6500.0  # 6700 − 200


def test_por_poder_consolidado_soma_poderes(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod, rpps=True)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    pp = client.get(
        f"/entes/{cod}/pessoal/por-poder", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert [i["poder_codigo"] for i in pp["itens"]] == ["ENTE.EXEC", "ENTE.LEG"]
    assert float(pp["consolidado"]["despesa_liquida"]) == 5700.0
    assert float(pp["consolidado"]["pct_rcl"]) == 57.0  # 50 + 7


def test_semaforo_pessoal_integra_mart(client, make_org, limpar) -> None:
    # Ao apurar pessoal, o mart_indicador do Executivo é persistido e alimenta o semáforo.
    cod = _ente()
    limpar.append(cod)
    _seed(cod, rpps=True)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    client.get(
        f"/entes/{cod}/pessoal/por-poder", params={"periodo": PERIODO}, headers=auth_header(token)
    )
    dash = client.get(
        f"/entes/{cod}/dashboard", params={"periodo": PERIODO_RREO}, headers=auth_header(token)
    ).json()
    pessoal = next(s for s in dash["semaforo"] if s["indicador"] == "pessoal_executivo")
    assert pessoal["faixa"] == "alerta"
    assert pessoal["cor"] == "amarelo"


def test_as_of_reproduz_versao_anterior(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod, rpps=True)  # RGF v1 vigente
    with SessionLocal() as s:
        s.execute(
            DimEntrega.__table__.update()
            .where(
                DimEntrega.cod_ibge == cod,
                DimEntrega.relatorio == "RGF",
                DimEntrega.versao_entrega == "1",
            )
            .values(vigente=False)
        )
        s.commit()
    # Retificação: v2 com bruta maior no Executivo.
    _seed(
        cod,
        rpps=True,
        versao="2",
        homologada_em=datetime(2025, 3, 1, tzinfo=UTC),
        seed_rreo=False,  # RREO/RCL já semeados na v1 (evita duplicar Anexo 03)
        rgf_rows=[
            ("E", "DESPESA BRUTA COM PESSOAL (I)", "7000"),
            ("E", "Inativos e Pensionistas com Recursos Vinculados", "800"),
        ],
    )
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    vigente = client.get(
        f"/entes/{cod}/pessoal", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert vigente["versao_entrega"] == "2"
    assert float(vigente["executivo"]["despesa_liquida"]) == 6200.0  # 7000 − 800

    historico = client.get(
        f"/entes/{cod}/pessoal",
        params={"periodo": PERIODO, "as_of": "2025-02-01T00:00:00Z"},
        headers=auth_header(token),
    ).json()
    assert historico["versao_entrega"] == "1"
    assert float(historico["executivo"]["despesa_liquida"]) == 5000.0

    with SessionLocal() as s:
        marts = list(
            s.scalars(
                select(MartIndicador).where(
                    MartIndicador.cod_ibge == cod,
                    MartIndicador.periodo == PERIODO_RREO,
                    MartIndicador.indicador == "pessoal_executivo",
                )
            )
        )

    # A consulta histórica cria sua linha composta, sem voltar a projeção simples
    # usada pelo dashboard para a entrega vigente do RREO.
    projection = next(row for row in marts if row.versao_entrega == "1")
    assert projection.valor_rs == Decimal("6200")
    assert projection.source_ref["tipo_registro"] == "projecao_vigente"

    historicos = [
        row for row in marts if row.versao_entrega.startswith(COMPOSITE_VERSION_PREFIX)
    ]
    assert len(historicos) == 2
    valores_por_rgf = {
        next(
            ref["versao_entrega"]
            for ref in row.source_ref["source_refs_componentes"]
            if ref["relatorio"] == "RGF"
        ): row.valor_rs
        for row in historicos
    }
    assert valores_por_rgf == {"1": Decimal("5000"), "2": Decimal("6200")}
    assert all(
        row.source_ref["tipo_registro"] == "historico_composto" for row in historicos
    )


def test_escopo_403_fora_da_carteira(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed(cod, rpps=True)
    fx = make_org(capacidades=["ver"], entes=[])  # carteira vazia
    token = login(client, fx.email, fx.senha)

    resp = client.get(
        f"/entes/{cod}/pessoal", params={"periodo": PERIODO}, headers=auth_header(token)
    )
    assert resp.status_code == 403
    assert resp.json()["title"] == "Ente fora do escopo"
