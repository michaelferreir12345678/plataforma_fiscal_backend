"""Sprint 17 — Assistente de IA: RAG fundamentado, guardrails §9, escopo e uso.

Todos os testes rodam OFFLINE: o provedor de chat é um ``LLMProvider`` falso injetado por
``dependency_overrides`` e o *embedder* do corpo normativo é o local determinístico
(forçado em ``conftest`` via ``ASSISTANT_PROVIDER=local``). Nenhuma chamada de rede.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.db import admin_session
from app.main import app
from app.modules.assistant import retriever
from app.modules.assistant.llm import LLMProviderError, LLMRequest, LLMResult, get_llm_provider
from app.modules.assistant.models import Conversa, ConversaUso
from app.modules.catalog.models import DimEnte
from app.modules.indicators.models import FatoRcl
from app.modules.ingestion.models import DimEntrega
from app.modules.tenancy.models import AuditLog
from tests.conftest import auth_header, login

PERIODO = "2091-B6"
VERSAO = "assist-v1"


def _ente() -> str:
    return "9" + "".join(random.choices("0123456789", k=6))


class FakeProvider:
    """LLMProvider falso: captura a requisição e ecoa os fatos disponíveis (ou falha)."""

    name = "fake"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[LLMRequest] = []

    def chat(self, request: LLMRequest) -> LLMResult:
        self.calls.append(request)
        if self.fail:
            raise LLMProviderError(detail="Gemini indisponível (simulado).")
        nums = "; ".join(f"{f.rotulo}={f.valor_formatado}" for f in request.fatos if f.disponivel)
        return LLMResult(
            texto=f"[fake] Fundamentado em: {nums or 'sem dados'}.",
            modelo="fake-model",
            tokens_entrada=42,
            tokens_saida=7,
        )


@pytest.fixture
def use_fake():
    """Injeta um FakeProvider via dependency_overrides e limpa ao final."""
    provider = FakeProvider()

    def _install(p: FakeProvider) -> FakeProvider:
        app.dependency_overrides[get_llm_provider] = lambda: p
        return p

    _install(provider)
    yield provider, _install
    app.dependency_overrides.pop(get_llm_provider, None)


@dataclass(frozen=True)
class Cenario:
    ente: str


@pytest.fixture
def cenario_com_rcl() -> Iterator[Cenario]:
    """Ente sintético com RREO vigente + RCL materializada (dado real de gold, sem ETL)."""
    cod = _ente()
    # Homologação no passado ⇒ vigente no as_of default (now); período é só rótulo.
    homologada = datetime(2024, 1, 20, tzinfo=UTC)
    with admin_session() as s:
        s.add(
            DimEnte(
                cod_ibge=cod,
                nome="Município Assistente",
                esfera="municipal",
                uf="CE",
                regiao="Nordeste",
                populacao=120_000,
                pib=Decimal("1500000000"),
                rpps=False,
                possui_tcm=False,
            )
        )
        s.add(
            DimEntrega(
                cod_ibge=cod,
                relatorio="RREO",
                periodo=PERIODO,
                versao_entrega=VERSAO,
                homologada_em=homologada,
                vigente=True,
                hash_payload=f"hash-{cod}",
            )
        )
        s.add(
            FatoRcl(
                cod_ibge=cod,
                periodo_ref=PERIODO,
                rcl_12m=Decimal("500000000"),
                receita_corrente=Decimal("560000000"),
                deducoes=Decimal("60000000"),
                versao_entrega=VERSAO,
                memoria={"formula": "receita_corrente - deducoes", "fonte": "SICONFI/RREO"},
            )
        )
    yield Cenario(ente=cod)
    with admin_session() as s:
        s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
        s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
        s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))


def test_perguntar_fundamentado_com_fonte_e_registra_uso(
    client, make_org, use_fake, cenario_com_rcl: Cenario
) -> None:
    provider, _ = use_fake
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))

    resp = client.post(
        "/assistant/perguntar",
        headers=headers,
        json={
            "ente": cenario_com_rcl.ente,
            "pergunta": "Qual é a Receita Corrente Líquida e o que ela significa?",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Fundamentado: dado disponível, número calculado com fonte.
    assert body["recusa"] is False
    assert body["dado_disponivel"] is True
    assert body["periodo"] == PERIODO
    rcl = next(f for f in body["fatos"] if f["codigo"] == "rcl")
    assert rcl["disponivel"] is True
    assert rcl["source_ref"]["relatorio"] == "RREO"
    assert rcl["source_ref"]["versao_entrega"] == VERSAO
    assert rcl["as_of"] is not None  # as_of rastreável por número
    assert rcl["memoria"]  # memória de cálculo presente

    # Nenhuma resposta com número não rastreável: há source_refs e chips de fonte.
    assert body["source_refs"], "toda resposta deve carregar source_ref"
    assert any(c["tipo"] == "indicador" for c in body["fontes"])
    assert any(c["tipo"] == "norma" for c in body["fontes"])  # RAG normativo (LRF art. 2º)

    # O domínio enviou o contexto ESTRUTURADO ao provedor (fatos + normas).
    assert len(provider.calls) == 1
    assert any(f.codigo == "rcl" and f.disponivel for f in provider.calls[0].fatos)
    assert provider.calls[0].normas

    # Uso registrado por organização (op.conversa_uso) + conversa + auditoria.
    with admin_session() as s:
        usos = list(s.scalars(select(ConversaUso).where(ConversaUso.org_id == org.org_id)))
        conversas = list(s.scalars(select(Conversa).where(Conversa.org_id == org.org_id)))
        auditorias = list(
            s.scalars(
                select(AuditLog.acao).where(
                    AuditLog.org_id == org.org_id, AuditLog.acao == "CONSULTA_IA"
                )
            )
        )
    assert len(usos) == 1
    assert usos[0].modelo == "fake-model"
    assert usos[0].tokens_entrada == 42 and usos[0].tokens_saida == 7
    assert len(conversas) == 1 and conversas[0].recusa is False
    assert conversas[0].thread_id == conversas[0].id
    assert conversas[0].parent_id is None
    assert conversas[0].verificacao is not None
    assert conversas[0].verificacao["status"] in {"ok", "sinalizado"}
    assert "CONSULTA_IA" in auditorias


def test_resumo_executivo_rastreavel(client, make_org, use_fake, cenario_com_rcl: Cenario) -> None:
    provider, _ = use_fake
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))

    resp = client.post(
        "/assistant/resumo-executivo",
        headers=headers,
        json={"ente": cenario_com_rcl.ente, "periodo": PERIODO},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tipo"] == "resumo_executivo"
    assert body["titulo"] == "Resumo Executivo"
    # O resumo pede ao provedor o modelo **configurado** para resumo. Era um literal
    # (``gemini-2.5-pro``) e a Sprint IA-7 mostrou por que isso é uma armadilha: o literal
    # continuou passando no teste depois de a API deixar de servir aquele modelo, enquanto
    # todo resumo executivo terminava em 502 em produção. Um teste que fixa o valor da
    # configuração testa a configuração, não o comportamento.
    assert provider.calls[0].modelo == get_settings().assistant_summary_model
    codigos = {f["codigo"] for f in body["fatos"]}
    assert {"rcl", "pessoal_executivo", "divida_consolidada_liquida"} <= codigos
    assert body["source_refs"]
    # RCL disponível; os demais faltam e são sinalizados (não inventados).
    assert any(f["codigo"] == "rcl" and f["disponivel"] for f in body["fatos"])
    assert any(not f["disponivel"] for f in body["fatos"])
    assert body["dados_incompletos"]


def test_recusa_honesta_quando_falta_dado(client, make_org, cenario_com_rcl: Cenario) -> None:
    """Com o provedor local REAL: o indicador ausente é sinalizado, sem inventar número.

    Este teste NÃO injeta o fake — usa o ``LocalGroundedProvider`` determinístico (offline),
    provando que a resposta composta pelo domínio é honesta sobre a lacuna.
    """
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))

    resp = client.post(
        "/assistant/perguntar",
        headers=headers,
        json={
            "ente": cenario_com_rcl.ente,
            "periodo": PERIODO,
            "pergunta": "Qual foi a despesa com pessoal do Executivo neste período?",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    pessoal = next(f for f in body["fatos"] if f["codigo"] == "pessoal_executivo")
    assert pessoal["disponivel"] is False
    assert pessoal["valor"] is None  # não inventou número
    assert "não" in body["resposta"].lower()  # sinaliza a ausência explicitamente
    assert body["dados_incompletos"]


def test_recusa_total_sem_dado_nem_norma_nao_chama_provedor(
    client, make_org, use_fake, monkeypatch
) -> None:
    """Sem dado E sem norma aplicável: recusa honesta, sem chamar o LLM nem registrar uso."""
    provider, _ = use_fake
    # Força ausência de normas para exercitar o caminho de recusa dura.
    monkeypatch.setattr(retriever, "retrieve_normas", lambda *a, **k: [])
    ente = _ente()  # sem DimEnte/gold algum
    org = make_org(entes=[ente])
    headers = auth_header(login(client, org.email, org.senha))

    resp = client.post(
        "/assistant/perguntar",
        headers=headers,
        json={"ente": ente, "pergunta": "Qual a situação fiscal?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recusa"] is True
    assert body["dado_disponivel"] is False
    assert not body["fatos"]
    assert provider.calls == []  # provedor NÃO foi chamado
    with admin_session() as s:
        usos = list(s.scalars(select(ConversaUso).where(ConversaUso.org_id == org.org_id)))
        conversas = list(s.scalars(select(Conversa).where(Conversa.org_id == org.org_id)))
    assert usos == []  # recusa sem chamada não consome cota
    assert len(conversas) == 1 and conversas[0].recusa is True


def test_provedor_indisponivel_erro_claro_sem_resposta_inventada(
    client, make_org, use_fake, cenario_com_rcl: Cenario
) -> None:
    """Falha do Gemini ⇒ RFC 7807, nunca uma resposta sem fonte; nada persistido."""
    _, install = use_fake
    install(FakeProvider(fail=True))
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))

    resp = client.post(
        "/assistant/perguntar",
        headers=headers,
        json={"ente": cenario_com_rcl.ente, "pergunta": "Qual a RCL?"},
    )
    assert resp.status_code == 502, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    problem = resp.json()
    assert problem["title"]
    assert "type" in problem and problem["status"] == 502
    with admin_session() as s:
        conversas = list(s.scalars(select(Conversa).where(Conversa.org_id == org.org_id)))
        usos = list(s.scalars(select(ConversaUso).where(ConversaUso.org_id == org.org_id)))
    assert conversas == [] and usos == []  # nada inventado, nada gravado


def test_escopo_fora_da_carteira_403(client, make_org, use_fake, cenario_com_rcl: Cenario) -> None:
    org = make_org(entes=[])  # carteira vazia: ente não está no escopo
    headers = auth_header(login(client, org.email, org.senha))
    resp = client.post(
        "/assistant/perguntar",
        headers=headers,
        json={"ente": cenario_com_rcl.ente, "pergunta": "Qual a RCL?"},
    )
    assert resp.status_code == 403, resp.text


def test_sem_capacidade_usar_ia_403(client, make_org, use_fake, cenario_com_rcl: Cenario) -> None:
    org = make_org(entes=[cenario_com_rcl.ente], capacidades=["ver", "exportar"])
    headers = auth_header(login(client, org.email, org.senha))
    resp = client.post(
        "/assistant/perguntar",
        headers=headers,
        json={"ente": cenario_com_rcl.ente, "pergunta": "Qual a RCL?"},
    )
    assert resp.status_code == 403, resp.text


def test_uso_mensal_agrega_por_organizacao(
    client, make_org, use_fake, cenario_com_rcl: Cenario
) -> None:
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))
    for _ in range(2):
        r = client.post(
            "/assistant/perguntar",
            headers=headers,
            json={"ente": cenario_com_rcl.ente, "pergunta": "Qual a RCL?"},
        )
        assert r.status_code == 200
    uso = client.get("/assistant/uso", headers=headers)
    assert uso.status_code == 200, uso.text
    data = uso.json()
    assert data["consultas"] == 2
    assert data["tokens_entrada"] == 84  # 2 × 42
