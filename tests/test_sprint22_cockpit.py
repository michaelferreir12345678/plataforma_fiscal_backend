"""Testes da Sprint 22 — Cockpit Executivo, seletor de ente/período e aritmética de período.

Cobre os critérios de aceite: contrato das 7 camadas com ``source_ref`` por bloco;
explicador = diff **real** entre períodos; comparação sem base ⇒ "sem base" (nunca zero);
escopo do ``/entes``; e o período default = o mais recente **com dado**.
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
from app.modules.dashboard import cockpit_service
from app.modules.indicators import service as indicators_service
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega, SilverEnte, SilverRreo
from app.shared import periodo as periodo_util
from tests.conftest import auth_header, login

PERIODO = "2024-B6"
ANTERIOR = "2024-B5"


def _ente() -> str:
    return "6" + "".join(random.choices("0123456789", k=6))


@pytest.fixture
def limpar() -> Iterator[list[str]]:
    usados: list[str] = []
    yield usados
    with SessionLocal() as s:
        for cod in usados:
            for model in (MartIndicador, FatoRcl, SilverRreo, DimEntrega, DimEnte, SilverEnte):
                s.execute(delete(model).where(model.cod_ibge == cod))
        s.commit()


def _seed_periodo(cod: str, periodo: str, receita: int, deducoes: int) -> None:
    """RREO Anexo 03 de um período (base da RCL) + entrega vigente."""
    with SessionLocal() as s:
        s.add(
            DimEntrega(
                cod_ibge=cod, relatorio="RREO", periodo=periodo, versao_entrega="1",
                homologada_em=datetime(2025, 1, 10, tzinfo=UTC), vigente=True,
            )
        )
        for conta, valor in (
            ("Total das Receitas Correntes (I)", receita),
            ("Dedução de Receita para o FUNDEB", deducoes),
        ):
            s.add(
                SilverRreo(
                    cod_ibge=cod, periodo=periodo, anexo="RREO-Anexo 03", conta=conta,
                    coluna="12M", valor=Decimal(str(valor)), versao_entrega="1",
                )
            )
        s.commit()


# ---------------- aritmética de período (base do seletor e dos explicadores) -------------
def test_periodo_anterior_atravessa_exercicio() -> None:
    assert periodo_util.anterior("2024-B6") == "2024-B5"
    assert periodo_util.anterior("2024-B1") == "2023-B6"  # atravessa o exercício
    assert periodo_util.anterior("2024-Q1") == "2023-Q3"
    assert periodo_util.anterior("2024-M01") == "2023-M12"
    assert periodo_util.anterior("2024") == "2023"


def test_mesmo_periodo_exercicio_anterior() -> None:
    assert periodo_util.mesmo_periodo_exercicio_anterior("2024-B6") == "2023-B6"
    assert periodo_util.mesmo_periodo_exercicio_anterior("2024-Q2") == "2023-Q2"


def test_mais_recente_compara_granularidades() -> None:
    assert periodo_util.mais_recente(["2023-B6", "2024-B1", "2024-B5"]) == "2024-B5"
    assert periodo_util.mais_recente([]) is None


# ---------------- contrato das 7 camadas ----------------
def _principal_admin(session, org_id):  # type: ignore[no-untyped-def]
    from app.core.deps import Principal
    from app.modules.tenancy.models import CAPACIDADES

    return Principal(
        usuario_id=None, org_id=org_id, papel="admin",
        capacidades=frozenset(CAPACIDADES), escopo_ibges=None,
    )


def test_cockpit_tem_as_sete_camadas_e_source_ref(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente Cockpit", uf="CE", esfera="M"))
        s.commit()
    _seed_periodo(cod, ANTERIOR, 10_000_000, 500_000)
    _seed_periodo(cod, PERIODO, 12_000_000, 900_000)
    with SessionLocal() as s:
        indicators_service.calcular_rcl(s, cod, ANTERIOR)
        indicators_service.calcular_rcl(s, cod, PERIODO)
        s.commit()

    fx = make_org(entes=[cod])
    token = login(client, fx.email, fx.senha)
    resp = client.get(
        f"/entes/{cod}/cockpit", params={"periodo": PERIODO}, headers=auth_header(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # As sete camadas existem no payload.
    for camada in (
        "resumo", "criticos", "tendencias", "explicadores",
        "comparacoes", "riscos", "qualidade",
    ):
        assert camada in body, f"camada ausente: {camada}"
    assert body["source_ref"]["relatorio"] == "RREO"
    assert body["resumo"]["source_ref"] is not None
    # Sem indicador apurado, o farol diz 'sem_dados' — não inventa 'conforme'.
    assert body["resumo"]["farol"] in {"sem_dados", "conforme", "alerta", "prudencial", "critico"}
    # Toda comparação indisponível traz o motivo (nunca zero silencioso).
    for c in body["comparacoes"]:
        if not c["disponivel"]:
            assert c["motivo_indisponivel"]
            assert c["valor_base"] is None


def test_cockpit_comparacao_sem_base_nao_vira_zero(client, make_org, limpar) -> None:
    """Ente com um único período: toda comparação deve dizer 'sem base', não 0."""
    cod = _ente()
    limpar.append(cod)
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente Sozinho", uf="CE", esfera="M"))
        s.commit()
    _seed_periodo(cod, PERIODO, 12_000_000, 900_000)
    with SessionLocal() as s:
        indicators_service.calcular_rcl(s, cod, PERIODO)
        s.commit()

    fx = make_org(entes=[cod])
    token = login(client, fx.email, fx.senha)
    body = client.get(
        f"/entes/{cod}/cockpit", params={"periodo": PERIODO}, headers=auth_header(token)
    ).json()
    for c in body["comparacoes"]:
        assert c["disponivel"] is False
        assert c["valor_base"] is None
        assert "sem base" in (c["motivo_indisponivel"] or "").lower()


def test_cockpit_explicador_usa_diff_real_entre_periodos(limpar) -> None:
    """O explicador compara os MESMOS componentes em dois períodos e ordena por |Δ|."""
    atual = {"A": ("Origem A", Decimal("300")), "B": ("Origem B", Decimal("100"))}
    anterior = {"A": ("Origem A", Decimal("100")), "B": ("Origem B", Decimal("90"))}
    comps = cockpit_service._top_variacoes(atual, anterior)
    assert [c.codigo for c in comps] == ["A", "B"]  # maior variação primeiro
    assert comps[0].delta_abs == Decimal("200")
    assert comps[0].delta_pct == Decimal("200")
    assert comps[1].delta_abs == Decimal("10")


# A separação receita × despesa do Anexo 01 é garantida na **origem** (materialização),
# não aqui: ver tests/test_correcoes_origem.py. O cockpit apenas lê o fato_receita.


# ---------------- escopo do /entes e períodos ----------------
def test_busca_entes_respeita_escopo(client, make_org, limpar) -> None:
    dentro, fora = _ente(), _ente()
    limpar.extend([dentro, fora])
    with SessionLocal() as s:
        for cod, nome in ((dentro, "Ente Dentro"), (fora, "Ente Fora")):
            s.merge(SilverEnte(cod_ibge=cod, nome=nome, uf="CE", esfera="M"))
            s.merge(DimEnte(cod_ibge=cod, nome=nome, uf="CE", esfera="municipal"))
        s.commit()

    fx = make_org(entes=[dentro])  # só 'dentro' está na carteira
    token = login(client, fx.email, fx.senha)
    body = client.get("/entes", params={"q": "Ente "}, headers=auth_header(token)).json()
    codigos = {e["cod_ibge"] for e in body["data"]}
    assert dentro in codigos
    assert fora not in codigos  # fora do escopo não é sequer encontrável
    assert body["escopo_total"] >= 1


def test_periodos_do_ente_default_e_o_mais_recente(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente Períodos", uf="CE", esfera="M"))
        s.commit()
    _seed_periodo(cod, "2023-B6", 9_000_000, 400_000)
    _seed_periodo(cod, PERIODO, 12_000_000, 900_000)

    fx = make_org(entes=[cod])
    token = login(client, fx.email, fx.senha)
    body = client.get(
        f"/entes/{cod}/periodos", params={"relatorio": "RREO"}, headers=auth_header(token)
    ).json()
    assert body["default"] == PERIODO  # nunca um período fixo por env
    assert {p["periodo"] for p in body["periodos"]} == {"2023-B6", PERIODO}


def test_periodos_de_ente_fora_do_escopo_da_403(client, make_org, limpar) -> None:
    cod = _ente()
    limpar.append(cod)
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Ente Alheio", uf="CE", esfera="M"))
        s.commit()
    fx = make_org(entes=[])
    token = login(client, fx.email, fx.senha)
    resp = client.get(f"/entes/{cod}/periodos", headers=auth_header(token))
    assert resp.status_code == 403
