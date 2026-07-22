"""Testes da Dívida & Endividamento (Módulo 7) — Sprint 8.

As fixtures reproduzem o formato real do RGF Anexo 02: várias colunas temporais,
subtotal de deduções e disponibilidade bruta/líquida. CAPAG é uma entrega nacional
(``gold.dim_entrega.cod_ibge = 'BR'``); SADIPEM é uma fotografia anual por ente.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update

from app.core.db import SessionLocal
from app.modules.catalog.models import DimEnte
from app.modules.debt.models import (
    DimCredor,
    DimOrigemDivida,
    FatoCapag,
    FatoDivida,
    FatoVencimento,
)
from app.modules.ingestion.models import (
    DimEntrega,
    SadipemCronogramaPgto,
    SadipemOpContratada,
    SilverEnte,
    SilverRgf,
    TesouroCapag,
)
from tests.conftest import auth_header, login

ANO = 2097  # fora dos dados reais/seed: isola também a entrega CAPAG global ``BR``
PERIODO = f"{ANO}-Q3"
PERIODO_SADIPEM = str(ANO)
COL_Q3 = "Até o 3º Quadrimestre"


def _ente() -> str:
    return "8" + "".join(random.choices("0123456789", k=6))


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class DebtCase:
    cod_ibge: str
    prefix: str
    rgf_v1: str
    capag_v1: str
    op_v1: str
    crono_v1: str
    rgf_em: datetime
    capag_em: datetime
    op_em: datetime
    crono_em: datetime
    credor_interno: str
    credor_externo: str


def _entrega(
    session,
    *,
    cod_ibge: str,
    relatorio: str,
    periodo: str,
    versao: str,
    homologada_em: datetime,
    vigente: bool = True,
) -> None:
    session.add(
        DimEntrega(
            cod_ibge=cod_ibge,
            relatorio=relatorio,
            periodo=periodo,
            versao_entrega=versao,
            homologada_em=homologada_em,
            vigente=vigente,
        )
    )


def _rgf_rows(
    cod_ibge: str,
    versao: str,
    *,
    dc_bruta: Decimal = Decimal("950"),
    interno: Decimal = Decimal("500"),
    externo: Decimal = Decimal("300"),
) -> list[SilverRgf]:
    dcl = dc_bruta - Decimal("100") - Decimal("50")
    linhas = [
        # Colunas que jamais podem contaminar o Q3.
        ("DÍVIDA CONSOLIDADA - DC (I)", "SALDO DO EXERCÍCIO ANTERIOR", "9999"),
        ("DÍVIDA CONSOLIDADA - DC (I)", "Até o 1º Quadrimestre", "5000"),
        ("Disponibilidade de Caixa", "Até o 1º Quadrimestre", "3000"),
        # Q3 efetivo. Subtotal e caixa bruta devem ser ignorados.
        ("DÍVIDA CONSOLIDADA - DC (I)", COL_Q3, str(dc_bruta)),
        ("DEDUÇÕES (II)", COL_Q3, "999"),
        ("Disponibilidade de Caixa Bruta", COL_Q3, "400"),
        ("Disponibilidade de Caixa", COL_Q3, "100"),
        ("Demais Haveres Financeiros", COL_Q3, "50"),
        ("DÍVIDA CONSOLIDADA LÍQUIDA (DCL) (III) = (I - II)", COL_Q3, str(dcl)),
        (
            "RECEITA CORRENTE LÍQUIDA AJUSTADA PARA CÁLCULO DOS LIMITES " "DE ENDIVIDAMENTO (VI)",
            COL_Q3,
            "1000",
        ),
        ("Internos", COL_Q3, str(interno)),
        ("Externos", COL_Q3, str(externo)),
    ]
    rows = [
        SilverRgf(
            cod_ibge=cod_ibge,
            periodo=PERIODO,
            anexo="RGF-Anexo 02",
            conta=conta,
            coluna=coluna,
            valor=Decimal(valor),
            versao_entrega=versao,
        )
        for conta, coluna, valor in linhas
    ]
    # Posição conhecida de operações de crédito; garantias e ARO ficam ausentes.
    rows.append(
        SilverRgf(
            cod_ibge=cod_ibge,
            periodo=PERIODO,
            anexo="RGF-Anexo 04",
            conta="TOTAL CONSIDERADO PARA FINS DA APURAÇÃO DO CUMPRIMENTO DO LIMITE",
            coluna="VALOR",
            valor=Decimal("100"),
            versao_entrega=versao,
        )
    )
    return rows


def _seed(case: DebtCase) -> None:
    with SessionLocal() as session:
        session.merge(
            SilverEnte(
                cod_ibge=case.cod_ibge,
                nome="Ente dívida",
                uf="CE",
                esfera="M",
            )
        )
        _entrega(
            session,
            cod_ibge=case.cod_ibge,
            relatorio="RGF",
            periodo=PERIODO,
            versao=case.rgf_v1,
            homologada_em=case.rgf_em,
        )
        session.add_all(_rgf_rows(case.cod_ibge, case.rgf_v1))

        # CAPAG é uma entrega de arquivo nacional, embora a linha silver seja do ente.
        _entrega(
            session,
            cod_ibge="BR",
            relatorio="CAPAG",
            periodo=str(ANO),
            versao=case.capag_v1,
            homologada_em=case.capag_em,
        )
        session.add(
            TesouroCapag(
                cod_ibge=case.cod_ibge,
                ano_ref=ANO,
                nota_final="B",
                ind_endividamento=Decimal("0.95"),  # DC bruta/RCL, não DCL/RCL (0,80)
                ind_poupanca=Decimal("0.10"),
                ind_liquidez=Decimal("1.20"),
                metodologia_versao="Metodologia-v1",
                valid_time=date(ANO, 12, 31),
                versao_entrega=case.capag_v1,
            )
        )

        _entrega(
            session,
            cod_ibge=case.cod_ibge,
            relatorio="SADIPEM-OP",
            periodo=PERIODO_SADIPEM,
            versao=case.op_v1,
            homologada_em=case.op_em,
        )
        session.add_all(
            [
                SadipemOpContratada(
                    id_operacao="op-interna",
                    cod_ibge=case.cod_ibge,
                    tipo_operacao="Operação contratual interna",
                    credor=case.credor_interno,
                    moeda="Real",
                    valor_contratado=Decimal("100"),
                    valid_time=date(ANO, 12, 31),
                    versao_entrega=case.op_v1,
                ),
                SadipemOpContratada(
                    id_operacao="op-externa",
                    cod_ibge=case.cod_ibge,
                    tipo_operacao="Operação contratual externa",
                    credor=case.credor_externo,
                    moeda="Dólar dos EUA",
                    valor_contratado=Decimal("200"),
                    valid_time=date(ANO, 12, 31),
                    versao_entrega=case.op_v1,
                ),
            ]
        )

        _entrega(
            session,
            cod_ibge=case.cod_ibge,
            relatorio="SADIPEM-CRONOGRAMA",
            periodo=PERIODO_SADIPEM,
            versao=case.crono_v1,
            homologada_em=case.crono_em,
        )
        session.add_all(
            [
                SadipemCronogramaPgto(
                    id_operacao="op-interna",
                    cod_ibge=case.cod_ibge,
                    ano=ANO + 1,
                    mes=1,
                    principal=Decimal("100"),
                    juros=Decimal("10"),
                    encargos=Decimal("5"),
                    valid_time=date(ANO, 12, 31),
                    versao_entrega=case.crono_v1,
                ),
                SadipemCronogramaPgto(
                    id_operacao="op-externa",
                    cod_ibge=case.cod_ibge,
                    ano=ANO + 1,
                    mes=6,
                    principal=Decimal("200"),
                    juros=Decimal("20"),
                    encargos=Decimal("10"),
                    valid_time=date(ANO, 12, 31),
                    versao_entrega=case.crono_v1,
                ),
                SadipemCronogramaPgto(
                    id_operacao="op-interna",
                    cod_ibge=case.cod_ibge,
                    ano=ANO + 2,
                    mes=None,
                    principal=Decimal("90"),
                    juros=Decimal("9"),
                    encargos=Decimal("1"),
                    valid_time=date(ANO, 12, 31),
                    versao_entrega=case.crono_v1,
                ),
            ]
        )
        session.commit()


def _seed_retificacao(case: DebtCase) -> tuple[str, str, datetime]:
    rgf_v2 = f"{case.prefix}-rgf-v2"
    capag_v2 = f"{case.prefix}-capag-v2"
    homologada = datetime(2025, 3, 1, tzinfo=UTC)
    with SessionLocal() as session:
        session.execute(
            update(DimEntrega)
            .where(DimEntrega.versao_entrega.in_([case.rgf_v1, case.capag_v1]))
            .values(vigente=False)
        )
        _entrega(
            session,
            cod_ibge=case.cod_ibge,
            relatorio="RGF",
            periodo=PERIODO,
            versao=rgf_v2,
            homologada_em=homologada,
        )
        session.add_all(
            _rgf_rows(
                case.cod_ibge,
                rgf_v2,
                dc_bruta=Decimal("1000"),
                interno=Decimal("550"),
            )
        )
        _entrega(
            session,
            cod_ibge="BR",
            relatorio="CAPAG",
            periodo=str(ANO),
            versao=capag_v2,
            homologada_em=homologada,
        )
        session.add(
            TesouroCapag(
                cod_ibge=case.cod_ibge,
                ano_ref=ANO,
                nota_final="A",
                ind_endividamento=Decimal("0.70"),
                ind_poupanca=Decimal("0.05"),
                ind_liquidez=Decimal("1.50"),
                metodologia_versao="Metodologia-v2",
                valid_time=date(ANO, 12, 31),
                versao_entrega=capag_v2,
            )
        )
        session.commit()
    return rgf_v2, capag_v2, homologada


@pytest.fixture
def debt_case() -> Iterator[DebtCase]:
    cod = _ente()
    prefix = f"debt-{cod}"
    with SessionLocal() as session:
        origens_antes = set(session.scalars(select(DimOrigemDivida.codigo)))
        credores_antes = set(session.scalars(select(DimCredor.codigo)))
    case = DebtCase(
        cod_ibge=cod,
        prefix=prefix,
        rgf_v1=f"{prefix}-rgf-v1",
        capag_v1=f"{prefix}-capag-v1",
        op_v1=f"{prefix}-op-v1",
        crono_v1=f"{prefix}-crono-v1",
        rgf_em=datetime(2025, 1, 10, tzinfo=UTC),
        capag_em=datetime(2025, 1, 15, tzinfo=UTC),
        op_em=datetime(2025, 1, 20, tzinfo=UTC),
        crono_em=datetime(2025, 1, 21, tzinfo=UTC),
        credor_interno=f"Banco Interno {cod}",
        credor_externo=f"Banco Externo {cod}",
    )
    _seed(case)
    yield case

    with SessionLocal() as session:
        # Fatos antes das dimensões referenciadas.
        session.execute(delete(FatoVencimento).where(FatoVencimento.cod_ibge == cod))
        session.execute(delete(FatoCapag).where(FatoCapag.cod_ibge == cod))
        session.execute(delete(FatoDivida).where(FatoDivida.cod_ibge == cod))

        session.execute(delete(SadipemCronogramaPgto).where(SadipemCronogramaPgto.cod_ibge == cod))
        session.execute(delete(SadipemOpContratada).where(SadipemOpContratada.cod_ibge == cod))
        session.execute(delete(TesouroCapag).where(TesouroCapag.cod_ibge == cod))
        session.execute(delete(SilverRgf).where(SilverRgf.cod_ibge == cod))
        session.execute(delete(DimEntrega).where(DimEntrega.versao_entrega.like(f"{prefix}-%")))
        session.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
        session.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))

        # Remove apenas os nós criados pelo caso, filhos antes dos pais (FK auto-referente).
        novos_credores = list(
            session.scalars(
                select(DimCredor)
                .where(DimCredor.codigo.not_in(credores_antes))
                .order_by(DimCredor.nivel.desc())
            )
        )
        for row in novos_credores:
            session.execute(delete(DimCredor).where(DimCredor.codigo == row.codigo))
        novas_origens = list(
            session.scalars(
                select(DimOrigemDivida)
                .where(DimOrigemDivida.codigo.not_in(origens_antes))
                .order_by(DimOrigemDivida.nivel.desc())
            )
        )
        for row in novas_origens:
            session.execute(delete(DimOrigemDivida).where(DimOrigemDivida.codigo == row.codigo))
        session.commit()


def _token(client, make_org, cod_ibge: str) -> str:
    org = make_org(capacidades=["ver"], entes=[cod_ibge])
    return login(client, org.email, org.senha)


def test_dcl_q3_ignora_colunas_caixa_bruta_e_subtotal(
    client, make_org, debt_case: DebtCase
) -> None:
    token = _token(client, make_org, debt_case.cod_ibge)
    memoria = client.get(
        f"/entes/{debt_case.cod_ibge}/divida/memoria",
        params={"periodo": PERIODO},
        headers=auth_header(token),
    )
    assert memoria.status_code == 200, memoria.text
    body = memoria.json()

    assert float(body["dc_bruta"]) == 950.0
    assert float(body["disponibilidades"]) == 100.0
    assert float(body["haveres"]) == 50.0
    assert float(body["dcl"]) == 800.0
    assert float(body["dcl_reportada"]) == 800.0
    assert float(body["pct_rcl"]) == 80.0
    assert body["reconciliacao_ok"] is True
    assert body["detalhes"]["disponibilidade_bruta_ignorada"] is True
    assert body["detalhes"]["deducoes_subtotal_ignorado"] is True
    assert {item["coluna_origem"] for item in body["componentes"]} == {COL_Q3}
    assert body["source_ref"] == {
        "relatorio": "RGF",
        "anexo": "Anexo 02 — DDCL",
        "periodo": PERIODO,
        "versao_entrega": debt_case.rgf_v1,
    }
    assert _as_datetime(body["as_of"]) == debt_case.rgf_em


def test_detalhe_separa_dcl_liquida_de_capag_bruta_e_resolve_capag_br(
    client, make_org, debt_case: DebtCase
) -> None:
    token = _token(client, make_org, debt_case.cod_ibge)
    response = client.get(
        f"/entes/{debt_case.cod_ibge}/divida",
        params={"periodo": PERIODO},
        headers=auth_header(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["dcl"]["rotulo"] == "DCL líquida"
    assert body["dcl"]["natureza"] == "liquida"
    assert float(body["dcl"]["pct_rcl"]) == 80.0
    assert body["dcl"]["source_ref"]["versao_entrega"] == debt_case.rgf_v1
    assert _as_datetime(body["dcl"]["as_of"]) == debt_case.rgf_em

    assert body["capag"]["rotulo"] == "CAPAG — endividamento bruto"
    assert body["capag"]["natureza"] == "bruta"
    assert body["capag"]["nota_final"] == "B"
    assert float(body["capag"]["ind_endividamento"]) == 0.95
    assert float(body["capag"]["endividamento_pct"]) == 95.0
    assert body["capag"]["source_ref"]["versao_entrega"] == debt_case.capag_v1
    assert _as_datetime(body["capag"]["as_of"]) == debt_case.capag_em
    assert _as_datetime(body["as_of"]) == debt_case.rgf_em

    # Não existe DimEntrega CAPAG para o IBGE; o sucesso prova a resolução global BR.
    with SessionLocal() as session:
        capag_ente = session.scalar(
            select(DimEntrega).where(
                DimEntrega.cod_ibge == debt_case.cod_ibge,
                DimEntrega.relatorio == "CAPAG",
            )
        )
        capag_br = session.scalar(
            select(DimEntrega).where(
                DimEntrega.cod_ibge == "BR",
                DimEntrega.versao_entrega == debt_case.capag_v1,
            )
        )
    assert capag_ente is None
    assert capag_br is not None

    capag = client.get(
        f"/entes/{debt_case.cod_ibge}/divida/capag",
        params={"periodo": PERIODO},
        headers=auth_header(token),
    ).json()
    assert capag["memoria"]["base_numerador"].startswith("Dívida Consolidada bruta")
    assert "não usa a DCL líquida" in capag["memoria"]["observacoes"][0]
    assert capag["source_ref"]["versao_entrega"] == debt_case.capag_v1


def test_cronograma_soma_principal_juros_encargos_por_ano(
    client, make_org, debt_case: DebtCase
) -> None:
    token = _token(client, make_org, debt_case.cod_ibge)
    response = client.get(
        f"/entes/{debt_case.cod_ibge}/divida/cronograma",
        params={"periodo": PERIODO},
        headers=auth_header(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["periodo_ref"] == PERIODO_SADIPEM
    assert body["versao_entrega"] == debt_case.crono_v1
    assert _as_datetime(body["as_of"]) == debt_case.crono_em
    assert body["source_ref"]["relatorio"] == "SADIPEM-CRONOGRAMA"
    assert [item["ano"] for item in body["itens"]] == [ANO + 1, ANO + 2]
    primeiro = body["itens"][0]
    assert float(primeiro["principal"]) == 300.0
    assert float(primeiro["juros"]) == 30.0
    assert float(primeiro["encargos"]) == 15.0
    assert float(primeiro["valor"]) == 345.0
    assert primeiro["operacoes"] == 2
    assert float(body["total_principal"]) == 390.0
    assert float(body["total_juros"]) == 39.0
    assert float(body["total_encargos"]) == 16.0
    assert float(body["total_valor"]) == 445.0

    with SessionLocal() as session:
        fatos = list(
            session.scalars(
                select(FatoVencimento).where(FatoVencimento.cod_ibge == debt_case.cod_ibge)
            )
        )
    assert len(fatos) == 3
    assert all(fato.valor == fato.principal + fato.juros + fato.encargos for fato in fatos)


def test_arvore_reconcilia_origem_e_credor(client, make_org, debt_case: DebtCase) -> None:
    token = _token(client, make_org, debt_case.cod_ibge)
    origem = client.get(
        f"/entes/{debt_case.cod_ibge}/divida/arvore",
        params={"periodo": PERIODO, "eixo": "origem", "node": "DIVIDA"},
        headers=auth_header(token),
    )
    assert origem.status_code == 200, origem.text
    origem_body = origem.json()
    medidas_origem = {
        item["codigo"]: float(item["measures"]["saldo_divida"]) for item in origem_body["children"]
    }
    assert medidas_origem == {
        "DIVIDA.EXTERNA": 300.0,
        "DIVIDA.INTERNA": 500.0,
        "DIVIDA.OUTRAS": 150.0,
    }
    assert sum(medidas_origem.values()) == float(origem_body["measures"]["saldo_divida"])
    assert origem_body["source_ref"]["relatorio"] == "RGF"

    credores = client.get(
        f"/entes/{debt_case.cod_ibge}/divida/arvore",
        params={"periodo": PERIODO, "eixo": "credor", "node": "CREDORES"},
        headers=auth_header(token),
    )
    assert credores.status_code == 200, credores.text
    credores_body = credores.json()
    categorias = {item["codigo"]: item for item in credores_body["children"]}
    assert float(categorias["CREDORES.INTERNA"]["measures"]["valor_contratado"]) == 100.0
    assert float(categorias["CREDORES.EXTERNA"]["measures"]["valor_contratado"]) == 200.0
    assert float(credores_body["measures"]["valor_contratado"]) == 300.0
    assert credores_body["source_ref"]["relatorio"] == "SADIPEM-OP"
    assert credores_body["source_ref"]["versao_entrega"] == debt_case.op_v1
    assert _as_datetime(credores_body["as_of"]) == debt_case.op_em

    interna = client.get(
        f"/entes/{debt_case.cod_ibge}/divida/arvore",
        params={
            "periodo": PERIODO,
            "eixo": "credor",
            "node": "CREDORES.INTERNA",
        },
        headers=auth_header(token),
    ).json()
    # A dimensão é global, mas o drill deve expor somente credores ativos
    # nesta entidade/fotografia.
    assert [item["descricao"] for item in interna["children"]] == [debt_case.credor_interno]
    assert [item["codigo"] for item in interna["breadcrumb"]] == ["CREDORES"]


def test_simulacao_limites_desconhecidos_overrides_e_nao_persistencia(
    client, make_org, debt_case: DebtCase
) -> None:
    token = _token(client, make_org, debt_case.cod_ibge)
    # Materializa a posição oficial antes do cenário.
    detalhe = client.get(
        f"/entes/{debt_case.cod_ibge}/divida",
        params={"periodo": PERIODO},
        headers=auth_header(token),
    )
    assert detalhe.status_code == 200, detalhe.text
    with SessionLocal() as session:
        antes = session.scalar(select(FatoDivida).where(FatoDivida.cod_ibge == debt_case.cod_ibge))
        assert antes is not None
        id_antes, dcl_antes = antes.id, antes.dcl

    payload = {"valor_operacao": "60", "valor_garantia": "120", "valor_aro": "50"}
    desconhecido = client.post(
        f"/entes/{debt_case.cod_ibge}/divida/simular-operacao",
        params={"periodo": PERIODO},
        json=payload,
        headers=auth_header(token),
    )
    assert desconhecido.status_code == 200, desconhecido.text
    body = desconhecido.json()
    posicoes = {item["indicador"]: item for item in body["posicoes"]}

    assert body["persistido"] is False
    assert float(body["rcl_ajustada"]) == 1000.0
    assert float(posicoes["divida_consolidada_liquida"]["valor_projetado"]) == 860.0
    assert float(posicoes["divida_consolidada_liquida"]["pct_projetado"]) == 86.0
    assert float(posicoes["divida_consolidada_liquida"]["teto_pct"]) == 120.0
    assert float(posicoes["operacoes_credito"]["valor_projetado"]) == 160.0
    assert float(posicoes["operacoes_credito"]["pct_projetado"]) == 16.0
    assert float(posicoes["operacoes_credito"]["teto_pct"]) == 16.0
    assert posicoes["operacoes_credito"]["faixa_projetada"] == "excedido"
    for indicador, teto in (("garantias", 22.0), ("aro", 7.0)):
        assert posicoes[indicador]["posicao_atual_conhecida"] is False
        assert posicoes[indicador]["valor_projetado"] is None
        assert float(posicoes[indicador]["teto_pct"]) == teto
    assert body["memoria"]["ausencia_na_fonte_permanece_desconhecida"] is True

    com_override = client.post(
        f"/entes/{debt_case.cod_ibge}/divida/simular-operacao",
        params={"periodo": PERIODO},
        json={**payload, "garantias_atuais": "100", "aro_atual": "20"},
        headers=auth_header(token),
    )
    assert com_override.status_code == 200, com_override.text
    override = {item["indicador"]: item for item in com_override.json()["posicoes"]}
    assert float(override["garantias"]["valor_projetado"]) == 220.0
    assert float(override["garantias"]["pct_projetado"]) == 22.0
    assert override["garantias"]["faixa_projetada"] == "excedido"
    assert float(override["aro"]["valor_projetado"]) == 70.0
    assert float(override["aro"]["pct_projetado"]) == 7.0
    assert override["aro"]["faixa_projetada"] == "excedido"

    with SessionLocal() as session:
        depois = session.scalar(select(FatoDivida).where(FatoDivida.cod_ibge == debt_case.cod_ibge))
        fatos = list(
            session.scalars(select(FatoDivida).where(FatoDivida.cod_ibge == debt_case.cod_ibge))
        )
    assert depois is not None
    assert (depois.id, depois.dcl) == (id_antes, dcl_antes)
    assert len(fatos) == 1


def test_as_of_reproduz_rgf_e_capag_historicos(client, make_org, debt_case: DebtCase) -> None:
    rgf_v2, capag_v2, vigente_em = _seed_retificacao(debt_case)
    token = _token(client, make_org, debt_case.cod_ibge)

    vigente = client.get(
        f"/entes/{debt_case.cod_ibge}/divida",
        params={"periodo": PERIODO},
        headers=auth_header(token),
    )
    assert vigente.status_code == 200, vigente.text
    atual = vigente.json()
    assert atual["versao_entrega"] == rgf_v2
    assert float(atual["dcl"]["dcl"]) == 850.0
    assert atual["capag"]["nota_final"] == "A"
    assert atual["capag"]["source_ref"]["versao_entrega"] == capag_v2
    assert _as_datetime(atual["as_of"]) == vigente_em

    solicitado = datetime(2025, 2, 1, tzinfo=UTC)
    historico = client.get(
        f"/entes/{debt_case.cod_ibge}/divida",
        params={"periodo": PERIODO, "as_of": solicitado.isoformat()},
        headers=auth_header(token),
    )
    assert historico.status_code == 200, historico.text
    antigo = historico.json()
    assert antigo["versao_entrega"] == debt_case.rgf_v1
    assert float(antigo["dcl"]["dcl"]) == 800.0
    assert antigo["capag"]["nota_final"] == "B"
    assert antigo["capag"]["source_ref"]["versao_entrega"] == debt_case.capag_v1
    assert _as_datetime(antigo["as_of"]) == solicitado
    assert _as_datetime(antigo["dcl"]["as_of"]) == solicitado
    assert _as_datetime(antigo["capag"]["as_of"]) == solicitado


def test_divida_403_fora_da_carteira(client, make_org) -> None:
    cod = _ente()
    org = make_org(capacidades=["ver"], entes=[])
    token = login(client, org.email, org.senha)
    response = client.get(
        f"/entes/{cod}/divida",
        params={"periodo": PERIODO},
        headers=auth_header(token),
    )
    assert response.status_code == 403
    assert response.json()["title"] == "Ente fora do escopo"
