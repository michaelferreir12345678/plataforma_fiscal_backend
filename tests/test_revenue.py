"""Testes da Receita (Módulo 4) — Sprint 5, formato **real** do SICONFI.

O RREO real não traz código numérico: a hierarquia é derivada de ``cod_conta`` (slug do
STN) + ``linha_seq`` (ordem) + caixa do texto (CAIXA ALTA = origem; mista = espécie).
Cobrem os critérios de aceite: agregação por nível, drill completo, indicadores
(realização/dependência) e conciliação RREO × 1B (divergência sinalizada, valor oficial
inalterado).
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.modules.catalog.models import DimEnte
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import (
    DimEntrega,
    FndeFundebRepasse,
    SilverEnte,
    SilverRreo,
    TesouroFpm,
    TransferenciaGenerica,
)
from app.modules.revenue.models import FatoReceita
from tests.conftest import auth_header, login

PERIODO = "2024-B6"
ANEXO01 = "RREO-Anexo 01"
PREV_ATU = "PREVISÃO ATUALIZADA (a)"
ACUM = "Até o Bimestre (c)"

# Árvore de receita no formato real: (linha_seq, cod_conta, descricao, previsto_atu, arrecadado).
# CAIXA ALTA ⇒ origem (nível 2); caixa mista ⇒ espécie (nível 3); slugs de categoria ⇒ nível 1.
_LINHAS: list[tuple[int, str, str, str | None, str | None]] = [
    (1, "ReceitasExcetoIntraOrcamentarias", "RECEITAS (EXCETO INTRA-ORÇAMENTÁRIAS) (I)", "1500", "1200"),  # noqa: E501
    (8, "ReceitasCorrentes", "RECEITAS CORRENTES", "1300", "1100"),
    (15, "ReceitaTributaria", "IMPOSTOS, TAXAS E CONTRIBUIÇÕES DE MELHORIA", "700", "600"),
    (22, "Impostos", "Impostos", None, "400"),
    (29, "Taxas", "Taxas", None, "200"),
    (36, "TransferenciasCorrentes", "TRANSFERÊNCIAS CORRENTES", "600", "500"),
    (43, "TransferenciasDaUniao", "Cota-Parte do FPM", None, "300"),
    (50, "TransferenciasFundeb", "Transferências do FUNDEB", None, "200"),
    (57, "ReceitasDeCapital", "RECEITAS DE CAPITAL", "200", "100"),
    (64, "OperacoesDeCredito", "OPERAÇÕES DE CRÉDITO", "200", "100"),
    (90, "SubtotalDasReceitas", "SUBTOTAL DAS RECEITAS (III) = (I + II)", "1500", "1200"),  # total
]


def _ente() -> str:
    return "6" + "".join(random.choices("0123456789", k=6))


#: A14 (Sprint A5): FPM/FUNDEB/TRANSFERENCIA não têm vigência própria — vem de
#: gold.dim_entrega sob cod_ibge='BR' (ingestão nacional batch, §6.7), compartilhada com
#: o dado real do mesmo (ano, mês). ``homologada_em`` num futuro bem distante garante que
#: esta entrega sintética vence o desempate de "mais recente" mesmo contra uma
#: reingestão real futura — sem isso, o teste ficaria refém de quando o backfill real
#: rodou. A versão ("1") não colide com nenhuma versão real (que usa datas/tags como
#: 'pag2024'), e a limpeza abaixo é escopada por versão — nunca apaga a entrega real.
_HOMOLOGADA_SINTETICA = datetime(2099, 1, 1, tzinfo=UTC)


def _dim_entrega_nacional(s, relatorio: str, ano: int, meses: list[int], versao: str = "1") -> None:
    for mes in meses:
        s.add(
            DimEntrega(
                cod_ibge="BR", relatorio=relatorio, periodo=f"{ano}-M{mes:02d}",
                versao_entrega=versao, homologada_em=_HOMOLOGADA_SINTETICA, vigente=True,
            )
        )


def _limpar_dim_entrega_nacional(s, versao: str = "1") -> None:
    s.execute(
        delete(DimEntrega).where(
            DimEntrega.cod_ibge == "BR",
            DimEntrega.relatorio.in_(["FPM", "FUNDEB", "TRANSFERENCIA"]),
            DimEntrega.versao_entrega == versao,
        )
    )


@pytest.fixture
def limpar() -> Iterator[list[str]]:
    usados: list[str] = []
    yield usados
    with SessionLocal() as s:
        for cod in usados:
            s.execute(delete(FatoReceita).where(FatoReceita.cod_ibge == cod))
            s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
            s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
            s.execute(delete(SilverRreo).where(SilverRreo.cod_ibge == cod))
            s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
            s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
            s.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
            s.execute(delete(TesouroFpm).where(TesouroFpm.cod_ibge == cod))
            s.execute(delete(FndeFundebRepasse).where(FndeFundebRepasse.cod_ibge == cod))
            s.execute(
                delete(TransferenciaGenerica).where(TransferenciaGenerica.cod_ibge == cod)
            )
        _limpar_dim_entrega_nacional(s)
        s.commit()


def _seed_rreo(
    cod: str, *, linhas: list[tuple[int, str, str, str | None, str | None]] | None = None
) -> None:
    """Ente com RREO Anexo 01 (formato real) + Anexo 03 (RCL) na versão 1."""
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente", uf="CE", esfera="M"))
        s.add(
            DimEntrega(
                cod_ibge=cod, relatorio="RREO", periodo=PERIODO, versao_entrega="1",
                homologada_em=datetime(2025, 1, 10, tzinfo=UTC), vigente=True,
            )
        )
        for seq, cod_conta, desc, prev, acum in linhas or _LINHAS:
            for coluna, valor in ((PREV_ATU, prev), (ACUM, acum)):
                if valor is None:
                    continue
                s.add(
                    SilverRreo(
                        cod_ibge=cod, periodo=PERIODO, anexo=ANEXO01,
                        conta=desc, cod_conta=cod_conta, coluna=coluna, linha_seq=seq,
                        valor=Decimal(valor), versao_entrega="1",
                    )
                )
        s.add(
            SilverRreo(
                cod_ibge=cod, periodo=PERIODO, anexo="RREO-Anexo 03",
                conta="Receitas Correntes (I)", coluna="12M",
                valor=Decimal("10000000"), versao_entrega="1",
            )
        )
        s.commit()


def test_arvore_drill_e_agregacao_consistente(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_rreo(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    # Raiz: categorias econômicas (slug), com has_children e medidas.
    raiz = client.get(
        f"/entes/{cod}/receita/arvore", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert raiz["node"] is None
    assert [c["codigo"] for c in raiz["children"]] == ["ReceitasCorrentes", "ReceitasDeCapital"]
    cat1 = raiz["children"][0]
    assert cat1["has_children"] is True
    assert float(cat1["measures"]["arrecadado_acum"]) == 1100.0
    assert raiz["source_ref"]["anexo"] == "Anexo 01"

    # Drill DOWN: soma dos filhos = total do nível pai (aceite).
    n1 = client.get(
        f"/entes/{cod}/receita/arvore",
        params={"periodo": PERIODO, "node": "ReceitasCorrentes"}, headers=auth_header(token),
    ).json()
    soma_filhos = sum(float(c["measures"]["arrecadado_acum"]) for c in n1["children"])
    assert soma_filhos == float(n1["measures"]["arrecadado_acum"]) == 1100.0

    n11 = client.get(
        f"/entes/{cod}/receita/arvore",
        params={"periodo": PERIODO, "node": "ReceitaTributaria"}, headers=auth_header(token),
    ).json()
    soma_folhas = sum(float(c["measures"]["arrecadado_acum"]) for c in n11["children"])
    assert soma_folhas == float(n11["measures"]["arrecadado_acum"]) == 600.0

    # Drill UP: breadcrumb da espécie até a categoria.
    esp = client.get(
        f"/entes/{cod}/receita/arvore",
        params={"periodo": PERIODO, "node": "Impostos"}, headers=auth_header(token),
    ).json()
    assert [b["codigo"] for b in esp["breadcrumb"]] == ["ReceitasCorrentes", "ReceitaTributaria"]
    assert esp["children"] == []


def test_detalhe_realizacao_e_dependencia(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_rreo(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    det = client.get(
        f"/entes/{cod}/receita", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert float(det["totais"]["arrecadado_acum"]) == 1200.0
    assert float(det["totais"]["previsto_atualizado"]) == 1500.0
    assert float(det["realizacao_pct"]) == 80.0
    assert float(det["rcl_12m"]) == 10_000_000.0  # RCL da Sprint 2 reusada
    assert float(det["dependencia"]["transferida"]) == 500.0
    assert float(det["dependencia"]["propria"]) == 700.0
    # U21/U22 (Sprint F2): desdobramento corrente × capital — a fixture só tem
    # TransferenciasCorrentes (FPM + FUNDEB), então toda a transferida é corrente.
    assert float(det["dependencia"]["transferida_corrente"]) == 500.0
    assert float(det["dependencia"]["transferida_capital"]) == 0.0
    assert [c["codigo"] for c in det["composicao"]] == ["ReceitasCorrentes", "ReceitasDeCapital"]
    assert det["serie"][-1]["periodo"] == PERIODO
    assert [b["codigo"] for b in det["periodo_breadcrumb"]] == ["2024"]  # drill temporal UP
    assert det["source_ref"] == {
        "relatorio": "RREO", "anexo": "Anexo 01", "periodo": PERIODO, "versao_entrega": "1",
    }

    rea = client.get(
        f"/entes/{cod}/receita/realizacao",
        params={"periodo": PERIODO}, headers=auth_header(token),
    ).json()
    assert float(rea["total"]["realizacao_pct"]) == 80.0
    cat = {c["codigo"]: c for c in rea["por_categoria"]}
    assert float(cat["ReceitasCorrentes"]["realizacao_pct"]) == pytest.approx(1100 / 1300 * 100)
    assert float(cat["ReceitasDeCapital"]["realizacao_pct"]) == 50.0

    dep = client.get(
        f"/entes/{cod}/receita/dependencia",
        params={"periodo": PERIODO}, headers=auth_header(token),
    ).json()
    assert float(dep["resumo"]["pct_transferida"]) == pytest.approx(500 / 1200 * 100)
    maiores = dep["maiores_transferencias"]
    assert maiores[0]["descricao"] == "Cota-Parte do FPM"  # maior transferência
    assert float(maiores[0]["arrecadado_acum"]) == 300.0
    assert float(maiores[0]["pct_das_transferencias"]) == 60.0


def test_dependencia_desdobra_transferencia_de_capital(client, make_org, limpar) -> None:
    """U21/U22 (Sprint F2): TransferenciasDeCapital soma em `transferida_capital`, não
    fica escondida dentro de um único "transferida" com corrente."""
    cod = _ente()
    limpar.append(cod)
    linhas = [
        *_LINHAS[:-1],  # tudo menos o total (SubtotalDasReceitas)
        (71, "TransferenciasDeCapital", "TRANSFERÊNCIAS DE CAPITAL", "150", "150"),
        (78, "TransferenciasDaUniaoCapital", "Convênio da União", None, "150"),
        (90, "SubtotalDasReceitas", "SUBTOTAL DAS RECEITAS (III) = (I + II)", "1650", "1350"),
    ]
    _seed_rreo(cod, linhas=linhas)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    det = client.get(
        f"/entes/{cod}/receita", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    # transferida = 500 (corrente: FPM 300 + FUNDEB 200) + 150 (capital) = 650.
    assert float(det["dependencia"]["transferida"]) == 650.0
    assert float(det["dependencia"]["transferida_corrente"]) == 500.0
    assert float(det["dependencia"]["transferida_capital"]) == 150.0
    # A soma dos dois desdobramentos nunca pode divergir do total — é o mesmo número,
    # só exibido em duas cores em vez de uma.
    soma = float(det["dependencia"]["transferida_corrente"]) + float(
        det["dependencia"]["transferida_capital"]
    )
    assert soma == float(det["dependencia"]["transferida"])


def test_memoria_hierarquia_e_3_niveis_nao_5(client, make_org, limpar) -> None:
    """U19 (Sprint F2): a árvore só deriva Categoria → Origem → Espécie (3 níveis) — o
    SICONFI não expõe Rubrica/Alínea (sem código numérico pontuado)."""
    cod = _ente()
    limpar.append(cod)
    _seed_rreo(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    mem = client.get(
        f"/entes/{cod}/receita/memoria",
        params={"periodo": PERIODO}, headers=auth_header(token),
    ).json()
    assert mem["hierarquia"] == "Categoria → Origem → Espécie (natureza da receita)"
    assert "Rubrica" not in mem["hierarquia"]
    assert "Alínea" not in mem["hierarquia"]


def test_conciliacao_sinaliza_divergencia_sem_alterar_rreo(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_rreo(cod)
    with SessionLocal() as s:
        # FPM externo = 310 (RREO diz 300 ⇒ divergência +3,33%); FUNDEB bate (200).
        s.add(TesouroFpm(cod_ibge=cod, ano=2024, mes=5, valor_liquido=Decimal("155"),
                         versao_entrega="1"))
        s.add(TesouroFpm(cod_ibge=cod, ano=2024, mes=6, valor_bruto=Decimal("155"),
                         versao_entrega="1"))
        s.add(FndeFundebRepasse(cod_ibge=cod, ano=2024, mes=5, valor_repassado=Decimal("100"),
                                versao_entrega="1"))
        s.add(FndeFundebRepasse(cod_ibge=cod, ano=2024, mes=6, valor_repassado=Decimal("100"),
                                versao_entrega="1"))
        # Transferência sem par no RREO ⇒ sinalizada, nunca inventada.
        s.add(TransferenciaGenerica(cod_ibge=cod, tipo="ICMS", ano=2024, mes=5,
                                    valor=Decimal("50"), versao_entrega="1"))
        _dim_entrega_nacional(s, "FPM", 2024, [5, 6])
        _dim_entrega_nacional(s, "FUNDEB", 2024, [5, 6])
        _dim_entrega_nacional(s, "TRANSFERENCIA", 2024, [5])
        s.commit()
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    con = client.get(
        f"/entes/{cod}/receita/transferencias/conciliacao",
        params={"periodo": PERIODO}, headers=auth_header(token),
    ).json()
    itens = {i["transferencia"]: i for i in con["itens"]}
    assert itens["FPM"]["status"] == "divergente"
    assert float(itens["FPM"]["rreo_acum"]) == 300.0
    assert float(itens["FPM"]["externo_acum"]) == 310.0
    assert float(itens["FPM"]["divergencia_pct"]) == pytest.approx(10 / 300 * 100)
    assert itens["FUNDEB"]["status"] == "conciliado"
    assert itens["ICMS"]["status"] == "sem_par_rreo"
    assert "valor oficial do RREO" in con["observacao"]

    # O valor oficial do RREO permanece intacto após a conciliação.
    fpm = client.get(
        f"/entes/{cod}/receita/arvore",
        params={"periodo": PERIODO, "node": "TransferenciasDaUniao"}, headers=auth_header(token),
    ).json()
    assert float(fpm["measures"]["arrecadado_acum"]) == 300.0


def test_memoria_verifica_agregacao(client, make_org, limpar) -> None:
    # Ente coerente: sem inconsistências.
    cod = _ente()
    limpar.append(cod)
    _seed_rreo(cod)
    # Ente incoerente: categoria declara 1000, filhos somam 1100.
    cod2 = _ente()
    limpar.append(cod2)
    _seed_rreo(
        cod2,
        linhas=[
            (8, "ReceitasCorrentes", "RECEITAS CORRENTES", None, "1000"),
            (15, "ReceitaTributaria", "IMPOSTOS, TAXAS E CONTRIBUIÇÕES DE MELHORIA", None, "600"),
            (36, "TransferenciasCorrentes", "TRANSFERÊNCIAS CORRENTES", None, "500"),
        ],
    )
    fx = make_org(capacidades=["ver"], entes=[cod, cod2])
    token = login(client, fx.email, fx.senha)

    mem = client.get(
        f"/entes/{cod}/receita/memoria", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert mem["inconsistencias"] == []
    assert mem["formula_realizacao"].startswith("realizacao_pct")
    assert mem["source_ref"]["versao_entrega"] == "1"

    mem2 = client.get(
        f"/entes/{cod2}/receita/memoria", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    achado = next(i for i in mem2["inconsistencias"] if i["codigo"] == "ReceitasCorrentes")
    assert achado["medida"] == "arrecadado_acum"
    assert float(achado["valor_no"]) == 1000.0
    assert float(achado["soma_filhos"]) == 1100.0


def test_as_of_reproduz_versao_anterior(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_rreo(cod)
    with SessionLocal() as s:
        # Retificação: v2 supera a v1 (sem apagar) com arrecadação maior.
        s.execute(
            DimEntrega.__table__.update()
            .where(DimEntrega.cod_ibge == cod, DimEntrega.versao_entrega == "1")
            .values(vigente=False)
        )
        s.add(
            DimEntrega(
                cod_ibge=cod, relatorio="RREO", periodo=PERIODO, versao_entrega="2",
                homologada_em=datetime(2025, 3, 1, tzinfo=UTC), vigente=True,
            )
        )
        for seq, cod_conta, desc, coluna, valor in [
            (8, "ReceitasCorrentes", "RECEITAS CORRENTES", ACUM, "1150"),
            (8, "ReceitasCorrentes", "RECEITAS CORRENTES", PREV_ATU, "1300"),
            (57, "ReceitasDeCapital", "RECEITAS DE CAPITAL", ACUM, "100"),
        ]:
            s.add(
                SilverRreo(
                    cod_ibge=cod, periodo=PERIODO, anexo=ANEXO01,
                    conta=desc, cod_conta=cod_conta, coluna=coluna, linha_seq=seq,
                    valor=Decimal(valor), versao_entrega="2",
                )
            )
        s.commit()
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    vigente = client.get(
        f"/entes/{cod}/receita", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert vigente["versao_entrega"] == "2"
    assert float(vigente["totais"]["arrecadado_acum"]) == 1250.0

    historico = client.get(
        f"/entes/{cod}/receita",
        params={"periodo": PERIODO, "as_of": "2025-02-01T00:00:00Z"},
        headers=auth_header(token),
    ).json()
    assert historico["versao_entrega"] == "1"
    assert float(historico["totais"]["arrecadado_acum"]) == 1200.0


def test_escopo_403_fora_da_carteira(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_rreo(cod)
    fx = make_org(capacidades=["ver"], entes=[])  # carteira vazia
    token = login(client, fx.email, fx.senha)

    resp = client.get(
        f"/entes/{cod}/receita", params={"periodo": PERIODO}, headers=auth_header(token)
    )
    assert resp.status_code == 403
    assert resp.json()["title"] == "Ente fora do escopo"
