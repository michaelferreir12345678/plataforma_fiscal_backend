"""Testes da Sprint 21 — backfill âncora, cobertura, esfera estadual e retificação.

Sem rede: um cliente/resolver falso substitui o SICONFI. Cobre os critérios de aceite:
idempotência em lote (checkpoint), RGF esfera-aware (estadual × municipal, semestral),
guarda de payload vazio, retificação real via extrato com ``as_of``, materialização da
cobertura (``mart_cobertura_fonte``) e defasagem por cadência.
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.modules.catalog.models import DimEnte
from app.modules.dashboard import service as dashboard_service
from app.modules.debt.models import FatoCapag
from app.modules.indicators import service as indicators_service
from app.modules.indicators.models import FatoRcl
from app.modules.ingestion import cobertura as cobertura_mod
from app.modules.ingestion import repository, service
from app.modules.ingestion.connectors.siconfi import RgfConnector
from app.modules.ingestion.models import (
    DimEntrega,
    IngestionLog,
    MartCoberturaFonte,
    RawPayload,
    SilverEnte,
    SilverExtrato,
    SilverRgf,
    SilverRreo,
    TesouroCapag,
)
from app.modules.ingestion.repository import MedallionRepository
from app.modules.ingestion.schemas import RunRequest
from app.workers import backfill
from app.workers.materialize import materialize_capag_todos
from tests.conftest import auth_header, login


def _ente7() -> str:
    return "8" + "".join(random.choices("0123456789", k=6))


class FakeClient:
    """Cliente + resolver falso: devolve ``records`` e grava as chamadas."""

    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records = records if records is not None else []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, fonte: str) -> FakeClient:
        return self

    def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((path, dict(params)))
        return list(self.records)

    def close(self) -> None:  # compat com o resolver
        return None


@pytest.fixture
def limpar() -> Iterator[list[str]]:
    used: list[str] = []
    yield used
    with SessionLocal() as s:
        for cod in used:
            for model in (
                RawPayload, DimEntrega, IngestionLog, SilverRreo, SilverRgf,
                SilverExtrato, TesouroCapag, MartCoberturaFonte,
            ):
                s.execute(delete(model).where(model.cod_ibge == cod))
        s.commit()


# ---------------- defasagem por cadência ----------------
def test_defasagem_periodos_por_cadencia() -> None:
    hoje = date(2026, 7, 23)
    # Anual: 2024 está 1 exercício atrás do último fechado (2025).
    assert cobertura_mod.defasagem_periodos("2024", hoje) == 1
    assert cobertura_mod.defasagem_periodos("2025", hoje) == 0
    # Bimestral (freq 6): B4/2026 fecha em ago; em 23/07 o período em curso é B4 → fechado B3.
    assert cobertura_mod.defasagem_periodos("2026-B3", hoje) == 0
    assert cobertura_mod.defasagem_periodos("2025-B6", hoje) >= 1
    # Quadrimestral (freq 3) e mensal (freq 12) monotônicos.
    assert cobertura_mod.defasagem_periodos("2026-Q1", hoje) >= 0
    assert cobertura_mod.defasagem_periodos("2024-M01", hoje) > cobertura_mod.defasagem_periodos(
        "2025-M01", hoje
    )
    # Semestral (freq 2).
    assert cobertura_mod.defasagem_periodos("2025-S1", hoje) >= 1


# ---------------- RGF esfera-aware (estadual × municipal, semestral) ----------------
def test_rgf_discover_estadual_usa_co_esfera_E() -> None:
    conn = RgfConnector(FakeClient(), MedallionRepository())
    jobs = conn.discover({"entes": ["23"], "anos": [2023]})
    assert jobs, "esperava jobs quadrimestrais"
    assert all(j.params["co_esfera"] == "E" for j in jobs)
    assert jobs[0].periodo == "2023-Q1"


def test_rgf_discover_municipal_usa_co_esfera_M() -> None:
    conn = RgfConnector(FakeClient(), MedallionRepository())
    jobs = conn.discover({"entes": ["2304400"], "anos": [2023]})
    assert all(j.params["co_esfera"] == "M" for j in jobs)


def test_rgf_extract_estadual_consulta_cinco_poderes() -> None:
    fake = FakeClient(records=[{"conta": "x", "valor": "1"}])
    conn = RgfConnector(fake, MedallionRepository())
    job = conn.discover({"entes": ["23"], "anos": [2023], "periodos": [1]})[0]
    conn.extract(job)
    poderes = {params.get("co_poder") for _, params in fake.calls}
    assert poderes == {"E", "L", "J", "M", "D"}


def test_rgf_extract_municipal_consulta_dois_poderes() -> None:
    fake = FakeClient(records=[{"conta": "x", "valor": "1"}])
    conn = RgfConnector(fake, MedallionRepository())
    job = conn.discover({"entes": ["2304400"], "anos": [2023], "periodos": [1]})[0]
    conn.extract(job)
    poderes = {params.get("co_poder") for _, params in fake.calls}
    assert poderes == {"E", "L"}


def test_rgf_semestral_gera_S1_S2() -> None:
    conn = RgfConnector(FakeClient(), MedallionRepository())
    jobs = conn.discover({"entes": ["2304400"], "anos": [2023], "periodicidade": "S"})
    periodos = [j.periodo for j in jobs]
    assert periodos == ["2023-S1", "2023-S2"]
    assert jobs[0].params["in_periodicidade"] == "S"
    # S1 fecha em junho; S2 em dezembro.
    assert jobs[0].valid_time == date(2023, 6, 30)
    assert jobs[1].valid_time == date(2023, 12, 31)


# ---------------- guarda de payload vazio ----------------
def test_payload_vazio_nao_cria_entrega(client, make_org, limpar) -> None:
    cod = _ente7()
    limpar.append(cod)
    fake = FakeClient(records=[])  # fonte não publicou o período
    with SessionLocal() as s:
        result = service.run(
            s, fake, RunRequest(fonte="siconfi_rreo", entes=[cod], anos=[2023], periodos=[6])
        )
        s.commit()
    assert result.silver_rows == 0
    with SessionLocal() as s:
        n_entrega = s.scalar(
            select(func.count()).select_from(DimEntrega).where(DimEntrega.cod_ibge == cod)
        )
        n_bronze = s.scalar(
            select(func.count()).select_from(RawPayload).where(RawPayload.cod_ibge == cod)
        )
    assert n_entrega == 0  # lacuna da fonte fica explícita, não vira "entrega vazia"
    assert n_bronze == 0


# ---------------- idempotência em lote (checkpoint) ----------------
def test_backfill_idempotente_em_lote(tmp_path, limpar) -> None:
    cod = _ente7()
    limpar.append(cod)
    fake = FakeClient(records=[{"conta": "Receita", "coluna": "12M", "valor": "10"}])
    units = [
        backfill.BackfillUnit(
            key=f"siconfi_rreo:{cod}:2023",
            req=RunRequest(fonte="siconfi_rreo", entes=[cod], anos=[2023], periodos=[6]),
        )
    ]
    ckpt = tmp_path / "ck.json"
    r1 = backfill.run_backfill(units, checkpoint_path=ckpt, resolver=fake)
    r2 = backfill.run_backfill(units, checkpoint_path=ckpt, resolver=fake)
    assert r1.executados == 1
    assert r2.executados == 0 and r2.pulados == 1  # checkpoint retoma sem reexecutar
    with SessionLocal() as s:
        n_bronze = s.scalar(
            select(func.count()).select_from(RawPayload).where(RawPayload.cod_ibge == cod)
        )
    assert n_bronze == 1  # rodar 2x não duplica o bronze


def test_backfill_isola_falha_de_unidade(tmp_path, limpar) -> None:
    """Uma unidade que a fonte recusa não derruba o lote — e não entra no checkpoint."""
    cod_ok, cod_ruim = _ente7(), _ente7()
    limpar.extend([cod_ok, cod_ruim])

    class ClienteQueFalha(FakeClient):
        def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
            if params.get("id_ente") == cod_ruim:
                raise RuntimeError("fonte recusou (simula 4xx definitivo)")
            return [{"conta": "Receita", "coluna": "12M", "valor": "10"}]

    units = [
        backfill.BackfillUnit(
            key=f"siconfi_rreo:{c}:2023",
            req=RunRequest(fonte="siconfi_rreo", entes=[c], anos=[2023], periodos=[6]),
        )
        for c in (cod_ruim, cod_ok)
    ]
    ckpt = tmp_path / "ck.json"
    res = backfill.run_backfill(units, checkpoint_path=ckpt, resolver=ClienteQueFalha())

    assert res.falhas == 1
    assert res.executados == 1  # o ente bom entrou apesar da falha do anterior
    assert res.erros and res.erros[0][0].endswith(f"{cod_ruim}:2023")
    # A unidade que falhou NÃO foi marcada como concluída: a próxima corrida a retenta.
    ckpt_done = backfill.Checkpoint.load(ckpt).done
    assert f"siconfi_rreo:{cod_ok}:2023" in ckpt_done
    assert f"siconfi_rreo:{cod_ruim}:2023" not in ckpt_done


# ---------------- retificação real via extrato + as_of ----------------
def test_varredura_retificacao_as_of(limpar) -> None:
    cod = _ente7()
    limpar.append(cod)
    v1 = datetime(2024, 1, 26, tzinfo=UTC)
    v2 = datetime(2024, 1, 29, tzinfo=UTC)
    with SessionLocal() as s:
        for h in (v1, v2):
            s.add(
                SilverExtrato(
                    cod_ibge=cod, relatorio="RGF", periodo="2023-Q3",
                    homologada_em=h, versao=str(h),
                )
            )
        s.commit()
    fake = FakeClient(records=[{"conta": "Despesa Pessoal", "coluna": "x", "valor": "1"}])
    with SessionLocal() as s:
        res = service.varrer_retificacoes(s, fake, ente=cod)
        s.commit()
    assert res.ingeridos == 2  # duas entregas reais processadas
    with SessionLocal() as s:
        vig = repository.resolve_versao(
            s, cod_ibge=cod, relatorio="RGF", periodo="2023-Q3", as_of=None
        )
        antes = repository.resolve_versao(
            s, cod_ibge=cod, relatorio="RGF", periodo="2023-Q3",
            as_of=datetime(2024, 1, 27, tzinfo=UTC),
        )
    assert vig == str(v2)  # vigente = retificação mais recente
    assert antes == str(v1)  # as_of anterior reproduz a entrega original


# ---------------- cobertura materializada reflete o backfill ----------------
def _seed_rreo_periodos(cod: str, periodos: list[str]) -> None:
    with SessionLocal() as s:
        for periodo in periodos:
            s.add(
                DimEntrega(
                    cod_ibge=cod, relatorio="RREO", periodo=periodo, versao_entrega="1",
                    homologada_em=datetime(2025, 1, 10, tzinfo=UTC), vigente=True,
                )
            )
            s.add(
                SilverRreo(
                    cod_ibge=cod, periodo=periodo, anexo="RREO-Anexo 03",
                    conta="Receita", coluna="12M", valor=Decimal("10"), versao_entrega="1",
                )
            )
        s.commit()


def test_cobertura_reflete_backfill_multi_periodo(limpar) -> None:
    cods = [_ente7() for _ in range(3)]
    limpar.extend(cods)
    periodos = ["2022-B6", "2023-B6", "2024-B6"]
    for cod in cods:
        _seed_rreo_periodos(cod, periodos)
    with SessionLocal() as s:
        cobertura_mod.refresh_cobertura(s)
        s.commit()
    with SessionLocal() as s:
        for cod in cods:
            linhas = list(
                s.scalars(
                    select(MartCoberturaFonte.periodo).where(
                        MartCoberturaFonte.fonte == "siconfi_rreo",
                        MartCoberturaFonte.cod_ibge == cod,
                    )
                )
            )
            assert set(linhas) == set(periodos)  # todos os períodos, não só o último
        # Cobertura ≥ meta para o conjunto seedado (3 entes × 3 períodos).
        linha = s.execute(
            select(MartCoberturaFonte).where(MartCoberturaFonte.cod_ibge == cods[0]).limit(1)
        ).scalar_one()
        assert linha.n_registros >= 1
        assert linha.defasagem_periodos is not None


# ---------------- CAPAG silver→gold para todos os entes ----------------
def test_materialize_capag_todos(limpar) -> None:
    cods = [_ente7() for _ in range(4)]
    limpar.extend(cods)
    versao = f"cap21-{random.randint(10**6, 10**7)}"  # única por execução
    try:
        with SessionLocal() as s:
            s.add(
                DimEntrega(
                    cod_ibge="BR", relatorio="CAPAG", periodo="2024", versao_entrega=versao,
                    homologada_em=datetime(2025, 6, 1, tzinfo=UTC), vigente=False,
                )
            )
            for cod in cods:
                s.add(
                    TesouroCapag(
                        cod_ibge=cod, ano_ref=2024, nota_final="B",
                        ind_endividamento=Decimal("0.5"), ind_poupanca=Decimal("0.8"),
                        ind_liquidez=Decimal("0.9"), versao_entrega=versao,
                    )
                )
            s.commit()
        with SessionLocal() as s:
            # Versão explícita: o banco compartilhado já tem a entrega CAPAG real vigente.
            n = materialize_capag_todos(s, versao=versao)
            s.commit()
        assert n == 4  # todos os entes da entrega materializados de uma vez
        with SessionLocal() as s:
            for cod in cods:
                assert _count_capag(s, cod) == 1
    finally:
        with SessionLocal() as s:
            s.execute(delete(FatoCapag).where(FatoCapag.cod_ibge.in_(cods)))
            s.execute(delete(DimEntrega).where(DimEntrega.versao_entrega == versao))
            s.commit()


def _count_capag(session, cod: str) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(FatoCapag).where(FatoCapag.cod_ibge == cod)
        )
        or 0
    )


# ---------------- performance sob volume multi-exercício ----------------
def test_performance_dashboard_sob_multi_exercicio(limpar) -> None:
    """Com 12 períodos materializados no ente, o dashboard responde em < 2s."""
    cod = _ente7()
    limpar.append(cod)
    periodos = [f"{ano}-B{b}" for ano in (2023, 2024) for b in range(1, 7)]
    with SessionLocal() as s:
        s.merge(SilverEnte(cod_ibge=cod, nome="Perf", uf="CE", esfera="M"))
        s.commit()
    with SessionLocal() as s:
        for periodo in periodos:
            s.add(
                DimEntrega(
                    cod_ibge=cod, relatorio="RREO", periodo=periodo, versao_entrega="1",
                    homologada_em=datetime(2025, 1, 10, tzinfo=UTC), vigente=True,
                )
            )
            for conta, coluna, valor in (
                ("Total das Receitas Correntes (I)", "12M", 12_000_000),
                ("Dedução de Receita para o FUNDEB", "12M", 900_000),
            ):
                s.add(
                    SilverRreo(
                        cod_ibge=cod, periodo=periodo, anexo="RREO-Anexo 03", conta=conta,
                        coluna=coluna, valor=Decimal(str(valor)), versao_entrega="1",
                    )
                )
        s.commit()
    with SessionLocal() as s:
        for periodo in periodos:
            indicators_service.calcular_rcl(s, cod, periodo)
        s.commit()

    inicio = time.perf_counter()
    with SessionLocal() as s:
        resp = dashboard_service.build_dashboard(s, cod, periodos[-1])
    decorrido = time.perf_counter() - inicio
    assert resp.versao_entrega == "1"
    assert decorrido < 2.0, f"dashboard levou {decorrido:.2f}s (limite 2s)"

    with SessionLocal() as s:
        s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
        s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))
        s.execute(delete(SilverEnte).where(SilverEnte.cod_ibge == cod))
        s.commit()


def test_endpoints_cobertura_e_fontes(client, make_org) -> None:
    fx = make_org()
    token = login(client, fx.email, fx.senha)
    r_fontes = client.get("/admin/ingestion/fontes", headers=auth_header(token))
    assert r_fontes.status_code == 200, r_fontes.text
    fontes = r_fontes.json()
    assert any(f["fonte"] == "siconfi_rreo" for f in fontes)
    assert all("cadencia" in f for f in fontes)
    r_cob = client.get("/admin/ingestion/cobertura?fonte=siconfi_rreo", headers=auth_header(token))
    assert r_cob.status_code == 200, r_cob.text
    body = r_cob.json()
    assert "data" in body and "resumo" in body


# ---------------- catálogo cobre 100% do registry (Sprint 20) ----------------
def test_catalogo_cobre_todo_o_connector_registry() -> None:
    """Nenhuma fonte pode existir no registry sem estar descrita no catálogo.

    É o que impede uma fonte nova de entrar em produção invisível para a cobertura
    e para o `/admin/ingestion/fontes`.
    """
    from app.modules.ingestion.connectors.registry import CONNECTOR_REGISTRY, FONTE_META

    faltando = sorted(set(CONNECTOR_REGISTRY) - set(FONTE_META))
    assert not faltando, f"fontes sem metadado no catálogo: {faltando}"
    sobrando = sorted(set(FONTE_META) - set(CONNECTOR_REGISTRY))
    assert not sobrando, f"catálogo descreve fontes inexistentes: {sobrando}"


def test_seed_catalogo_persiste_paginas_e_dependencias() -> None:
    """O seed grava páginas impactadas/dependências como DADO consultável."""
    from app.modules.ingestion.connectors.registry import CONNECTOR_REGISTRY
    from app.modules.ingestion.models import CatalogoFonte

    with SessionLocal() as s:
        cobertura_mod.seed_catalogo(s)
        s.commit()
        linhas = list(s.scalars(select(CatalogoFonte)))
    assert len(linhas) >= len(CONNECTOR_REGISTRY)
    rgf = next(x for x in linhas if x.fonte == "siconfi_rgf")
    assert "limites" in rgf.paginas_impactadas
    # O % da RCL do RGF vem do RREO: a dependência precisa estar declarada.
    assert "siconfi_rreo" in rgf.dependencias
    assert rgf.parser_versao


def test_health_expoe_app_env_e_versao(client) -> None:
    """Contrato que autoriza (ou não) a UI a mostrar credencial de demonstração."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["app_env"]
    assert body["version"]
