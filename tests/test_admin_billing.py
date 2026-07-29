"""Sprint 18 — Administração, Carteira avançada & Billing (Módulo 17).

Cobre: cobrança refletindo a métrica (por ente / por população, com memória + source_ref),
faturas expondo empenho/contrato, auditoria filtrável, escopo (capacidade + RLS entre orgs)
e o toggle de op.integracao pausando efetivamente o conector.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.core.db import admin_session
from app.modules.catalog.models import DimEnte
from app.modules.ingestion import integracoes
from app.modules.ingestion import repository as ing_repo
from app.modules.ingestion import service as ing_service
from app.modules.ingestion.schemas import RunRequest
from app.modules.tenancy import repository as tenancy_repo
from app.modules.tenancy.models import AuditLog, Fatura, Licenca
from tests.conftest import auth_header, login


def _ente() -> str:
    return "8" + "".join(random.choices("0123456789", k=6))


@dataclass(frozen=True)
class Entes:
    a: str
    b: str
    pop_a: int
    pop_b: int


@pytest.fixture
def entes_pop() -> Iterator[Entes]:
    """Dois entes sintéticos com população real (IBGE) em gold.dim_ente."""
    a, b = _ente(), _ente()
    pop_a, pop_b = 120_000, 45_000
    with admin_session() as s:
        for cod, pop in ((a, pop_a), (b, pop_b)):
            s.add(
                DimEnte(
                    cod_ibge=cod,
                    nome=f"Município {cod}",
                    esfera="municipal",
                    uf="CE",
                    populacao=pop,
                    pop_ano_ref=2022,
                    pop_source_ref={"relatorio": "IBGE-POP", "periodo": "2022"},
                )
            )
    yield Entes(a=a, b=b, pop_a=pop_a, pop_b=pop_b)
    with admin_session() as s:
        s.execute(delete(DimEnte).where(DimEnte.cod_ibge.in_([a, b])))


class DummyResolver:
    """Resolver que falha se acionado — prova que o conector pausado NÃO roda."""

    def __init__(self) -> None:
        self.get_called = False

    def get(self, fonte: str) -> Any:
        self.get_called = True
        raise AssertionError("resolver.get não deveria ser chamado com a integração pausada")


def _assinatura_control_plane(
    org_id: uuid.UUID,
    *,
    metrica: str,
    preco: str,
    status: str = "ativa",
) -> None:
    with admin_session() as session:
        tenancy_repo.upsert_assinatura(
            session,
            org_id=org_id,
            plano="padrao",
            metrica_cobranca=metrica,
            preco_unitario=Decimal(preco),
            moeda="BRL",
            ciclo="mensal",
            status=status,
            inicio_vigencia=None,
            fim_vigencia=None,
        )


# --------------------------------------------------------------------------- #
# Billing — a cobrança reflete a metrica_cobranca
# --------------------------------------------------------------------------- #
def test_cobranca_por_ente(client, make_org, entes_pop: Entes) -> None:
    org = make_org(entes=[entes_pop.a, entes_pop.b])
    h = auth_header(login(client, org.email, org.senha))

    _assinatura_control_plane(
        org.org_id, metrica="por_ente", preco="150.00"
    )

    overview = client.get("/billing", headers=h).json()
    assert overview["metrica_cobranca"] == "por_ente"
    assert Decimal(overview["preview"]["quantidade"]) == Decimal("2")
    assert Decimal(overview["preview"]["valor_total"]) == Decimal("300.00")
    assert overview["preview"]["ja_emitida"] is False

    emitida = client.post(
        "/billing/faturas",
        headers=h,
        json={
            "competencia": "2026-05",
            "empenho_ref": "2026NE000123",
            "contrato_ref": "CT-9/2026",
        },
    )
    assert emitida.status_code == 201, emitida.text
    fatura = emitida.json()
    assert fatura["metrica_cobranca"] == "por_ente"
    assert Decimal(fatura["quantidade"]) == Decimal("2")
    assert Decimal(fatura["valor_total"]) == Decimal("300.00")
    assert fatura["empenho_ref"] == "2026NE000123"  # compra pública exposta
    assert fatura["contrato_ref"] == "CT-9/2026"
    assert fatura["memoria"]["base"] == "op.carteira_ente"


def test_cobranca_por_populacao_rastreavel(client, make_org, entes_pop: Entes) -> None:
    org = make_org(entes=[entes_pop.a, entes_pop.b])
    h = auth_header(login(client, org.email, org.senha))
    _assinatura_control_plane(
        org.org_id, metrica="por_populacao", preco="0.10"
    )
    overview = client.get("/billing", headers=h).json()
    esperado_qtd = Decimal(entes_pop.pop_a + entes_pop.pop_b)
    assert Decimal(overview["preview"]["quantidade"]) == esperado_qtd
    assert Decimal(overview["preview"]["valor_total"]) == (esperado_qtd * Decimal("0.10")).quantize(
        Decimal("0.01")
    )
    # Memória e source_ref rastreáveis (população vem do IBGE via gold.dim_ente).
    memoria = overview["preview"]["memoria"]
    assert memoria["base"].startswith("gold.dim_ente")
    assert memoria["n_entes"] == 2
    srefs = overview["preview"]["source_refs"]
    assert any(s.get("relatorio") == "IBGE-POP" for s in srefs)


def test_billing_isolado_por_organizacao(client, make_org, entes_pop: Entes) -> None:
    org_a = make_org(entes=[entes_pop.a])
    org_b = make_org(entes=[entes_pop.b])
    ha = auth_header(login(client, org_a.email, org_a.senha))
    hb = auth_header(login(client, org_b.email, org_b.senha))
    _assinatura_control_plane(
        org_a.org_id, metrica="por_ente", preco="10"
    )
    fatura_a = client.post("/billing/faturas", headers=ha, json={"competencia": "2026-06"}).json()

    overview_b = client.get("/billing", headers=hb).json()
    ids_b = {f["id"] for f in overview_b["faturas"]}
    assert fatura_a["id"] not in ids_b  # RLS: B não vê a fatura de A
    assert overview_b["faturas"] == []


# --------------------------------------------------------------------------- #
# Auditoria filtrável
# --------------------------------------------------------------------------- #
def test_auditoria_filtravel(client, make_org, entes_pop: Entes) -> None:
    org = make_org(entes=[entes_pop.a])
    h = auth_header(login(client, org.email, org.senha))
    _assinatura_control_plane(
        org.org_id, metrica="por_ente", preco="10"
    )
    client.post("/billing/faturas", headers=h, json={"competencia": "2026-07"})

    todos = client.get("/admin/auditoria", headers=h).json()
    assert todos["total"] >= 1
    filtrado = client.get("/admin/auditoria", headers=h, params={"acao": "EMITIR_FATURA"}).json()
    assert filtrado["total"] >= 1
    assert all(item["acao"] == "EMITIR_FATURA" for item in filtrado["itens"])
    # Filtro que não casa nada.
    vazio = client.get("/admin/auditoria", headers=h, params={"acao": "INEXISTENTE_XYZ"}).json()
    assert vazio["total"] == 0 and vazio["itens"] == []


# --------------------------------------------------------------------------- #
# Escopo / capacidade
# --------------------------------------------------------------------------- #
def test_admin_requer_capacidade(client, make_org) -> None:
    org = make_org(entes=[], capacidades=["ver"])  # sem 'administrar'
    h = auth_header(login(client, org.email, org.senha))
    assert client.get("/billing", headers=h).status_code == 403
    assert client.get("/admin/auditoria", headers=h).status_code == 403
    assert client.get("/admin/integracoes", headers=h).status_code == 403
    lote = client.post("/carteira/lote", headers=h, json={"adicionar": [], "remover": []})
    assert lote.status_code == 403


def test_tenant_admin_nao_reativa_propria_assinatura(client, make_org) -> None:
    org = make_org(entes=[])
    _assinatura_control_plane(
        org.org_id,
        metrica="por_ente",
        preco="10",
        status="suspensa",
    )
    h = auth_header(login(client, org.email, org.senha))

    response = client.post(
        "/billing/assinatura",
        headers=h,
        json={
            "metrica_cobranca": "por_ente",
            "preco_unitario": "0",
            "status": "ativa",
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["type"].endswith("platform-admin-required")
    with admin_session() as session:
        assinatura = tenancy_repo.get_assinatura(session, org_id=org.org_id)
        assert assinatura is not None
        assert assinatura.status == "suspensa"
        assert assinatura.preco_unitario == Decimal("10")


# --------------------------------------------------------------------------- #
# Carteira em lote
# --------------------------------------------------------------------------- #
def _licenciar(org_id, cod_ibge: str) -> None:
    """Licencia um ente avulso (Sprint 19): a carteira só aceita o que a licença cobre."""
    with admin_session() as session:
        tenancy_repo.add_licenca(
            session,
            Licenca(
                org_id=org_id,
                tipo="ente",
                cod_ibge=cod_ibge,
                vigencia_inicio=date.today(),
                status="ativa",
            ),
        )


def test_carteira_lote_idempotente(client, make_org, entes_pop: Entes) -> None:
    org = make_org(entes=[entes_pop.a])
    _licenciar(org.org_id, entes_pop.b)
    h = auth_header(login(client, org.email, org.senha))
    r = client.post(
        "/carteira/lote",
        headers=h,
        json={"adicionar": [{"cod_ibge": entes_pop.b}, {"cod_ibge": entes_pop.a}], "remover": []},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["adicionados"] == [entes_pop.b]  # b novo; a já existia
    assert entes_pop.a in body["ignorados"]
    assert body["total_carteira"] == 2
    # Remoção
    r2 = client.post(
        "/carteira/lote", headers=h, json={"adicionar": [], "remover": [entes_pop.a]}
    ).json()
    assert r2["removidos"] == [entes_pop.a]
    assert r2["total_carteira"] == 1


def test_carteira_lote_recusa_ente_fora_da_licenca_sem_derrubar_o_resto(
    client, make_org, entes_pop: Entes
) -> None:
    """Um ente fora da licença não pode invalidar a importação inteira (Sprint 19).

    O administrador precisa ver o que passou **e** o que não passou — por isso os
    recusados voltam em ``nao_licenciados``, lista distinta de ``ignorados`` (que é
    duplicidade ou inexistência, causa cadastral e não comercial).
    """
    org = make_org(entes=[])
    _licenciar(org.org_id, entes_pop.a)
    h = auth_header(login(client, org.email, org.senha))

    r = client.post(
        "/carteira/lote",
        headers=h,
        json={"adicionar": [{"cod_ibge": entes_pop.a}, {"cod_ibge": entes_pop.b}], "remover": []},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["adicionados"] == [entes_pop.a]
    assert body["nao_licenciados"] == [entes_pop.b]
    assert body["total_carteira"] == 1


# --------------------------------------------------------------------------- #
# Integração: o toggle pausa efetivamente o conector
# --------------------------------------------------------------------------- #
def test_integracoes_listagem_e_mutacao_global_restrita(client, make_org) -> None:
    org = make_org(entes=[])
    h = auth_header(login(client, org.email, org.senha))
    lst = client.get("/admin/integracoes", headers=h).json()
    assert {i["codigo"] for i in lst} >= {
        "SICONFI", "SADIPEM", "BCB", "IBGE", "FPM", "FUNDEB", "CAPAG", "SIOPS", "SIOPE"
    }
    assert all(i["ativo"] for i in lst)
    bloqueado = client.patch(
        "/admin/integracoes/SICONFI", headers=h, json={"ativo": False}
    )
    assert bloqueado.status_code == 403, bloqueado.text
    assert bloqueado.json()["type"].endswith("platform-admin-required")


def test_desligar_integracao_pausa_conector(make_org) -> None:
    """O control plane desliga SICONFI e o conector não chega à rede."""
    make_org(entes=[])
    try:
        # Mutação global feita diretamente pela sessão do control plane.
        with admin_session() as s:
            ing_repo.set_integracao_ativo(
                s, codigo="SICONFI", ativo=False, atualizado_por=None
            )

        # Executa o backfill do conector: deve pausar sem tocar a rede.
        resolver = DummyResolver()
        with admin_session() as s:
            result = ing_service.run(
                s,
                resolver,  # type: ignore[arg-type]
                RunRequest(fonte="siconfi_rreo", entes=["2304400"], anos=[2024]),
            )
        assert result.pausado is True
        assert result.ingeridos == 0 and result.total_jobs == 0
        assert resolver.get_called is False  # conector NÃO foi acionado
        assert integracoes.integracao_ativa is not None  # gate existe

        # Regate: com a integração ativa, o gate liberaria (não executamos rede aqui).
        with admin_session() as s:
            assert integracoes.integracao_ativa(s, "siconfi_rreo") is False
    finally:
        # op.integracao é global: restaura para não afetar outros testes / o servidor.
        with admin_session() as s:
            ing_repo.set_integracao_ativo(s, codigo="SICONFI", ativo=True, atualizado_por=None)


def test_fatura_persistida_com_empenho(client, make_org, entes_pop: Entes) -> None:
    org = make_org(entes=[entes_pop.a])
    h = auth_header(login(client, org.email, org.senha))
    _assinatura_control_plane(
        org.org_id, metrica="fixo", preco="999.90"
    )
    client.post(
        "/billing/faturas",
        headers=h,
        json={"competencia": "2026-08", "empenho_ref": "2026NE777", "contrato_ref": "CT-1"},
    )
    with admin_session() as s:
        row = s.scalar(
            select(Fatura).where(Fatura.org_id == org.org_id, Fatura.competencia == "2026-08")
        )
        assert row is not None
        assert row.empenho_ref == "2026NE777" and row.contrato_ref == "CT-1"
        assert row.metrica_cobranca == "fixo"
        assert Decimal(row.valor_total) == Decimal("999.90")
        acoes = list(
            s.scalars(select(AuditLog.acao).where(AuditLog.org_id == org.org_id))
        )
    assert "EMITIR_FATURA" in acoes
