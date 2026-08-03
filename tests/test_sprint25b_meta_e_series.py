"""Sprint 25B — meta fiscal declarada, PVL/CDP e séries comparáveis (Pessoal/Dívida/Caixa).

Aceites cobertos:
- meta da LDO cadastrada pela organização entra **só** na tela do ente (`/resultado/meta`),
  nunca no detalhe consumido por agregados e relatórios (decisão §11.5 da auditoria);
- RLS: a meta de uma organização não vaza para outra;
- cadastro exige capacidade de administrar e é auditado;
- meta oficial do Anexo 6 tem precedência sobre a manual;
- séries de pessoal/dívida/caixa/resultado trazem real (IPCA) e per capita;
- PVL/CDP declara a lacuna de ingestão em vez de sugerir "nenhum pedido".
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal, admin_session
from app.modules.catalog.models import DimEnte
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import (
    BcbIndice,
    DimEntrega,
    IbgePopulacao,
    SadipemPvl,
    SilverEnte,
    SilverRreo,
)
from app.modules.result.models import FatoAjusteMetodologico, FatoResultado, MetaFiscal
from app.modules.tenancy.models import AuditLog
from tests.conftest import auth_header, login

PERIODO = "2024-B6"
ANEXO = "RREO-Anexo 06"
REC_REAL = "RECEITAS REALIZADAS (a)"
D_PAGAS = "DESPESAS PAGAS (a)"
VALOR = "VALOR"
VERSAO_IPCA_TESTE = "9999-sprint25b"

# Anexo 6 mínimo e coerente, **sem** a linha de meta (o caso real de Fortaleza).
_A6_SEM_META: list[tuple[int, str, str, str]] = [
    (10, "RREO6TotalReceitaPrimaria", REC_REAL, "1000"),
    (20, "RREO6TotalDespesaPrimaria", D_PAGAS, "800"),
    (30, "ResultadoPrimarioComRPPSAcimaDaLinha", VALOR, "200"),
    (31, "ResultadoNominalAcimaDaLinhaSemRPPS", VALOR, "150"),
]
_LINHA_META = (60, "RREO6MetaDeResultadoPrimarioFixadaNoAn", VALOR, "250")


def _ente() -> str:
    return "3" + "".join(random.choices("0123456789", k=6))


@pytest.fixture
def limpar() -> Iterator[list[str]]:
    usados: list[str] = []
    yield usados
    with admin_session() as s:
        for cod in usados:
            s.execute(delete(MetaFiscal).where(MetaFiscal.cod_ibge == cod))
        s.commit()
    with SessionLocal() as s:
        for cod in usados:
            s.execute(delete(FatoAjusteMetodologico).where(FatoAjusteMetodologico.cod_ibge == cod))
            s.execute(delete(FatoResultado).where(FatoResultado.cod_ibge == cod))
            s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
            s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
            s.execute(delete(SilverRreo).where(SilverRreo.cod_ibge == cod))
            s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
            s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
            s.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
            s.execute(delete(IbgePopulacao).where(IbgePopulacao.cod_ibge == cod))
            s.execute(delete(SadipemPvl).where(SadipemPvl.cod_ibge == cod))
        s.execute(delete(BcbIndice).where(BcbIndice.versao_entrega == VERSAO_IPCA_TESTE))
        s.commit()


def _seed_a6(cod: str, *, com_meta: bool = False, periodo: str = PERIODO) -> None:
    linhas = list(_A6_SEM_META) + ([_LINHA_META] if com_meta else [])
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente", uf="CE", esfera="M"))
        s.add(
            DimEntrega(
                cod_ibge=cod, relatorio="RREO", periodo=periodo, versao_entrega="1",
                homologada_em=datetime(2025, 1, 10, tzinfo=UTC), vigente=True,
            )
        )
        for seq, cod_conta, coluna, valor in linhas:
            s.add(
                SilverRreo(
                    cod_ibge=cod, periodo=periodo, anexo=ANEXO, cod_conta=cod_conta,
                    conta=cod_conta, coluna=coluna, linha_seq=seq, valor=Decimal(valor),
                    versao_entrega="1",
                )
            )
        s.commit()


# --- meta fiscal declarada pela organização (§11.5) ---
def test_meta_manual_aparece_na_tela_do_ente(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_a6(cod)
    fx = make_org(capacidades=["ver", "administrar"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    antes = client.get(
        f"/entes/{cod}/resultado/meta", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert antes["origem"] == "ausente"
    assert antes["resumo"]["informada"] is False

    salvo = client.put(
        f"/entes/{cod}/meta-fiscal",
        json={
            "exercicio": 2024,
            "indicador": "primario",
            "valor": "250",
            "fonte_declarada": "LDO 2024, Anexo de Metas Fiscais",
        },
        headers=auth_header(token),
    )
    assert salvo.status_code == 200

    depois = client.get(
        f"/entes/{cod}/resultado/meta", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert depois["origem"] == "manual"
    assert depois["restrita_ao_ente"] is True
    assert depois["resumo"]["informada"] is True
    assert float(depois["resumo"]["meta_primario"]) == 250.0
    assert float(depois["resumo"]["esforco_primario"]) == 50.0  # 250 − 200 realizado
    assert depois["resumo"]["atingido_primario"] is False
    assert depois["cadastros"][0]["fonte_declarada"] == "LDO 2024, Anexo de Metas Fiscais"
    assert "não entra em comparações agregadas" in depois["observacao"]


def test_meta_manual_nao_vaza_para_o_detalhe_consumido_por_agregados(
    client, make_org, limpar
) -> None:
    """O `/resultado` alimenta relatório institucional e agregados: só meta oficial."""
    cod = _ente()
    limpar.append(cod)
    _seed_a6(cod)
    fx = make_org(capacidades=["ver", "administrar"], entes=[cod])
    token = login(client, fx.email, fx.senha)
    client.put(
        f"/entes/{cod}/meta-fiscal",
        json={
            "exercicio": 2024, "indicador": "primario", "valor": "250",
            "fonte_declarada": "LDO 2024",
        },
        headers=auth_header(token),
    )

    detalhe = client.get(
        f"/entes/{cod}/resultado", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert detalhe["meta"]["informada"] is False
    assert detalhe["meta"]["meta_primario"] is None


def test_meta_oficial_do_a6_tem_precedencia_sobre_a_manual(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_a6(cod, com_meta=True)  # A6 publica meta 250
    fx = make_org(capacidades=["ver", "administrar"], entes=[cod])
    token = login(client, fx.email, fx.senha)
    client.put(
        f"/entes/{cod}/meta-fiscal",
        json={
            "exercicio": 2024, "indicador": "primario", "valor": "999",
            "fonte_declarada": "LDO 2024 (cadastro que não deve prevalecer)",
        },
        headers=auth_header(token),
    )

    meta = client.get(
        f"/entes/{cod}/resultado/meta", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert meta["origem"] == "a6"
    assert meta["restrita_ao_ente"] is False
    assert float(meta["resumo"]["meta_primario"]) == 250.0  # oficial, não 999


def test_meta_manual_nao_vaza_entre_organizacoes(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_a6(cod)
    dona = make_org(capacidades=["ver", "administrar"], entes=[cod])
    vizinha = make_org(capacidades=["ver", "administrar"], entes=[cod])

    token_dona = login(client, dona.email, dona.senha)
    client.put(
        f"/entes/{cod}/meta-fiscal",
        json={"exercicio": 2024, "indicador": "primario", "valor": "250", "fonte_declarada": "LDO"},
        headers=auth_header(token_dona),
    )

    token_vizinha = login(client, vizinha.email, vizinha.senha)
    meta = client.get(
        f"/entes/{cod}/resultado/meta",
        params={"periodo": PERIODO}, headers=auth_header(token_vizinha),
    ).json()
    assert meta["origem"] == "ausente"
    assert (
        client.get(f"/entes/{cod}/meta-fiscal", headers=auth_header(token_vizinha)).json() == []
    )


def test_cadastro_de_meta_exige_administrar_e_e_auditado(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_a6(cod)
    leitor = make_org(capacidades=["ver"], entes=[cod])
    token_leitor = login(client, leitor.email, leitor.senha)
    negado = client.put(
        f"/entes/{cod}/meta-fiscal",
        json={"exercicio": 2024, "indicador": "primario", "valor": "1", "fonte_declarada": "LDO"},
        headers=auth_header(token_leitor),
    )
    assert negado.status_code == 403

    admin = make_org(capacidades=["ver", "administrar"], entes=[cod])
    token_admin = login(client, admin.email, admin.senha)
    client.put(
        f"/entes/{cod}/meta-fiscal",
        json={
            "exercicio": 2024, "indicador": "primario", "valor": "250",
            "fonte_declarada": "LDO 2024, art. 3º",
        },
        headers=auth_header(token_admin),
    )
    with admin_session() as s:
        acoes = list(
            s.scalars(
                select(AuditLog.recurso).where(
                    AuditLog.org_id == admin.org_id, AuditLog.acao == "meta_fiscal.salvar"
                )
            )
        )
    assert acoes and cod in acoes[0] and "LDO 2024, art. 3º" in acoes[0]


def test_meta_excluida_volta_a_ausente(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_a6(cod)
    fx = make_org(capacidades=["ver", "administrar"], entes=[cod])
    token = login(client, fx.email, fx.senha)
    salvo = client.put(
        f"/entes/{cod}/meta-fiscal",
        json={"exercicio": 2024, "indicador": "primario", "valor": "250", "fonte_declarada": "LDO"},
        headers=auth_header(token),
    ).json()

    apagado = client.delete(
        f"/entes/{cod}/meta-fiscal/{salvo['id']}", headers=auth_header(token)
    )
    assert apagado.status_code == 204
    meta = client.get(
        f"/entes/{cod}/resultado/meta", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    assert meta["origem"] == "ausente"


def test_upsert_atualiza_em_vez_de_duplicar(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_a6(cod)
    fx = make_org(capacidades=["ver", "administrar"], entes=[cod])
    token = login(client, fx.email, fx.senha)
    for valor in ("250", "300"):
        client.put(
            f"/entes/{cod}/meta-fiscal",
            json={
                "exercicio": 2024, "indicador": "primario", "valor": valor,
                "fonte_declarada": "LDO 2024",
            },
            headers=auth_header(token),
        )
    cadastros = client.get(f"/entes/{cod}/meta-fiscal", headers=auth_header(token)).json()
    assert len(cadastros) == 1
    assert float(cadastros[0]["valor"]) == 300.0


# --- série comparável do resultado (real + per capita) ---
def test_serie_do_resultado_traz_real_e_per_capita(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_a6(cod, periodo="2023-B6")
    _seed_a6(cod)
    with SessionLocal() as s:
        for mes in range(1, 13):
            s.add(BcbIndice(
                codigo_serie=433, data_ref=date(2024, mes, 1), valor=Decimal("1"),
                versao_entrega=VERSAO_IPCA_TESTE,
            ))
        s.add(IbgePopulacao(
            cod_ibge=cod, ano_ref=2023, populacao=100_000, fonte="IBGE", versao_entrega="1"
        ))
        s.add(IbgePopulacao(
            cod_ibge=cod, ano_ref=2024, populacao=200_000, fonte="IBGE", versao_entrega="1"
        ))
        s.commit()
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)
    # materializa os dois exercícios (o job da Sprint 21 faz isso em produção)
    for periodo in ("2023-B6", PERIODO):
        client.get(
            f"/entes/{cod}/resultado", params={"periodo": periodo}, headers=auth_header(token)
        )

    body = client.get(
        f"/entes/{cod}/resultado", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    serie = {s["periodo"]: s for s in body["serie"]}
    assert body["serie_ajuste"]["base_periodo"] == PERIODO
    assert float(serie["2023-B6"]["resultado_primario_real"]) == pytest.approx(
        200 * 1.01**12, rel=1e-6
    )
    assert float(serie[PERIODO]["resultado_primario_real"]) == 200.0  # base não é deflacionada
    assert float(serie["2023-B6"]["resultado_primario_per_capita"]) == pytest.approx(200 / 100_000)


# --- PVL/CDP ---
def test_pvl_declara_lacuna_de_ingestao(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    _seed_a6(cod)
    fx = make_org(capacidades=["ver"], entes=[cod])
    token = login(client, fx.email, fx.senha)

    vazio = client.get(f"/entes/{cod}/divida/pvl", headers=auth_header(token)).json()
    assert vazio["itens"] == []
    assert "não foi ingerida" in (vazio["observacao"] or "")

    with SessionLocal() as s:
        s.add(SadipemPvl(
            cod_ibge=cod, id_pvl="PVL-1", tipo_operacao="Operação de crédito interna",
            valor=Decimal("1000"), status="Deferido", finalidade="Infraestrutura",
            credor="Caixa Econômica Federal", num_pvl="PVL02.000001/2024-11",
            data_analise=date(2024, 5, 10), versao_entrega="2024-06-01",
        ))
        s.commit()

    com_dado = client.get(f"/entes/{cod}/divida/pvl", headers=auth_header(token)).json()
    assert len(com_dado["itens"]) == 1
    assert com_dado["itens"][0]["status"] == "Deferido"
    assert float(com_dado["total_valor"]) == 1000.0
    assert com_dado["observacao"] is None
