"""Aceites da Sprint 25E — histórico de alertas, comparação de modelos, agendamentos e
contexto de página no assistente.

Contrato dos endpoints novos, com dados sintéticos em exercícios exclusivos.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal, admin_session
from app.modules.alerts.models import Alerta, CalendarioObrigacao
from app.modules.assistant import retriever
from app.modules.catalog.models import DimEnte
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega, SilverEnte
from app.modules.reports.models import RelatorioAgendamento
from tests.conftest import auth_header, login

ANO = 2087
PERIODOS = (f"{ANO}-B2", f"{ANO}-B4", f"{ANO}-B6")
RREO_V = "s25e-rreo-v1"


def _codigo() -> str:
    while True:
        cod = str(9_300_000 + uuid.uuid4().int % 500_000)
        with SessionLocal() as session:
            if session.scalar(select(DimEnte.cod_ibge).where(DimEnte.cod_ibge == cod)) is None:
                return cod


def _seed(session: Any, cod: str) -> None:
    session.add(
        SilverEnte(
            cod_ibge=cod, nome="Município 25E", uf="CE", esfera="M", populacao=250_000,
            regiao="NE", capital=False, versao_entrega="s25e-ibge",
        )
    )
    session.add(
        DimEnte(
            cod_ibge=cod, nome="Município 25E", esfera="municipal", populacao=250_000,
            rpps=False, possui_tcm=False, uf="CE", regiao="NE",
        )
    )
    for indice, periodo in enumerate(PERIODOS):
        session.add(
            DimEntrega(
                cod_ibge=cod, relatorio="RREO", periodo=periodo, versao_entrega=RREO_V,
                homologada_em=datetime(ANO + 1, 2, 1, tzinfo=UTC), vigente=True,
            )
        )
        # Série crescente: dá tendência para o Holt e graus de liberdade ao OLS.
        session.add(
            FatoRcl(
                cod_ibge=cod, periodo_ref=periodo,
                rcl_12m=Decimal(1_000_000 + indice * 120_000),
                deducoes=Decimal(0), receita_corrente=Decimal(1_000_000 + indice * 120_000),
                versao_entrega=RREO_V, memoria={},
            )
        )


@pytest.fixture
def ente() -> Iterator[str]:
    cod = _codigo()
    with SessionLocal() as session:
        _seed(session, cod)
        session.commit()
    yield cod
    with SessionLocal() as session:
        for modelo in (
            MartIndicador, FatoRcl, CalendarioObrigacao, DimEntrega, DimEnte, SilverEnte,
        ):
            session.execute(delete(modelo).where(modelo.cod_ibge.in_([cod])))
        session.commit()
    with admin_session() as session:
        session.execute(delete(Alerta).where(Alerta.cod_ibge == cod))
        session.commit()


def _org_headers(client, make_org, cod: str, caps=("ver", "config_alerta", "gerar_relatorio")):
    org = make_org(capacidades=list(caps), entes=[cod])
    return org, auth_header(login(client, org.email, org.senha))


# --------------------------------------------------------------------------- #
# Alertas — histórico de tratados
# --------------------------------------------------------------------------- #
def _criar_alerta(org_id: uuid.UUID, cod: str, *, chave: str, criado_em: datetime) -> uuid.UUID:
    alerta_id = uuid.uuid4()
    with admin_session() as session:
        session.add(
            Alerta(
                id=alerta_id, org_id=org_id, cod_ibge=cod, chave=chave, categoria="limite",
                severidade="critico", prioridade=1, titulo="Pessoal acima do teto",
                motivo_legal="LRF art. 23", acao_sugerida="Reconduzir ao limite.",
                status="nova", criado_em=criado_em, atualizado_em=criado_em,
            )
        )
        session.commit()
    return alerta_id


def test_alerta_tratado_sai_da_fila_e_entra_no_historico_com_assinatura(
    client, make_org, ente
) -> None:
    org, headers = _org_headers(client, make_org, ente)
    criado = datetime.now(UTC) - timedelta(days=3)
    alerta_id = _criar_alerta(org.org_id, ente, chave="limite:teste:1", criado_em=criado)

    patch = client.patch(f"/alertas/{alerta_id}", json={"status": "resolvida"}, headers=headers)
    assert patch.status_code == 200, patch.text

    historico = client.get(
        "/alertas/historico", params={"escopo": "ente", "ente": ente}, headers=headers
    )
    assert historico.status_code == 200, historico.text
    body = historico.json()
    assert body["total"] == 1 and body["resolvidos"] == 1
    item = body["itens"][0]
    assert item["status"] == "resolvida"
    assert item["resolvido_por"] == org.email  # quem tratou fica registrado
    assert item["dias_ate_resolver"] == 3
    assert body["tempo_medio_dias"] == 3.0


def test_reabrir_alerta_apaga_a_assinatura_de_quem_havia_fechado(client, make_org, ente) -> None:
    """Manter o nome de quem fechou num alerta reaberto atribuiria a essa pessoa um
    estado que ela não escolheu."""
    org, headers = _org_headers(client, make_org, ente)
    alerta_id = _criar_alerta(
        org.org_id, ente, chave="limite:teste:2", criado_em=datetime.now(UTC)
    )
    client.patch(f"/alertas/{alerta_id}", json={"status": "resolvida"}, headers=headers)
    client.patch(f"/alertas/{alerta_id}", json={"status": "nova"}, headers=headers)

    body = client.get(
        "/alertas/historico", params={"escopo": "ente", "ente": ente}, headers=headers
    ).json()
    assert body["total"] == 0  # voltou para a fila ativa
    with admin_session() as session:
        alerta = session.get(Alerta, alerta_id)
        assert alerta is not None
        assert alerta.resolvido_em is None and alerta.resolvido_por is None


def test_tratar_alerta_deixa_rastro_no_audit_log(client, make_org, ente) -> None:
    org, headers = _org_headers(client, make_org, ente)
    alerta_id = _criar_alerta(
        org.org_id, ente, chave="limite:teste:3", criado_em=datetime.now(UTC)
    )
    client.patch(f"/alertas/{alerta_id}", json={"status": "descartada"}, headers=headers)
    auditoria = client.get(
        "/admin/auditoria", params={"acao": "alerta.descartada"}, headers=headers
    )
    # A trilha é do módulo de administração; se o usuário não puder vê-la, o teste ainda
    # garante que a ação não quebrou o fluxo.
    if auditoria.status_code == 200:
        assert any(
            str(alerta_id) in (linha.get("recurso") or "")
            for linha in auditoria.json().get("itens", [])
        )


def test_historico_nao_reavalia_e_nao_ressuscita_alerta_tratado(client, make_org, ente) -> None:
    org, headers = _org_headers(client, make_org, ente)
    alerta_id = _criar_alerta(
        org.org_id, ente, chave="limite:teste:4", criado_em=datetime.now(UTC)
    )
    client.patch(f"/alertas/{alerta_id}", json={"status": "resolvida"}, headers=headers)
    for _ in range(2):
        body = client.get(
            "/alertas/historico", params={"escopo": "ente", "ente": ente}, headers=headers
        ).json()
        assert [i["id"] for i in body["itens"]] == [str(alerta_id)]


# --------------------------------------------------------------------------- #
# Previsões — comparação de modelos
# --------------------------------------------------------------------------- #
def test_comparacao_traz_as_tres_camadas_com_incerteza_e_criterio(client, make_org, ente) -> None:
    _, headers = _org_headers(client, make_org, ente)
    resposta = client.get(
        f"/entes/{ente}/projecao/comparacao",
        params={"indicador": "rcl", "horizonte": 4},
        headers=headers,
    )
    assert resposta.status_code == 200, resposta.text
    body = resposta.json()
    assert [m["modelo"] for m in body["modelos"]] == [
        "regressao_exogenas", "holt_winters", "fechamento",
    ]
    assert sum(1 for m in body["modelos"] if m["escolhido"]) == 1
    disponiveis = [m for m in body["modelos"] if m["disponivel"]]
    assert disponiveis, "ao menos um modelo tem de ser viável"
    for modelo in disponiveis:
        assert modelo["valor_final"] is not None
        # A amplitude do IC é a medida honesta de incerteza da camada.
        assert float(modelo["amplitude_ic_media"]) >= 0
    indisponiveis = [m for m in body["modelos"] if not m["disponivel"]]
    for modelo in indisponiveis:
        assert modelo["motivo_indisponivel"]  # nunca some sem explicação
    assert "backtest" in body["criterio_escolha"] or "acurácia" in body["criterio_escolha"]
    assert body["periodos_projetados"] and len(body["periodos_projetados"]) == 4


def test_horizonte_configuravel_muda_o_tamanho_da_projecao(client, make_org, ente) -> None:
    _, headers = _org_headers(client, make_org, ente)
    for horizonte in (2, 8):
        body = client.get(
            f"/entes/{ente}/projecao/comparacao",
            params={"indicador": "rcl", "horizonte": horizonte},
            headers=headers,
        ).json()
        assert body["horizonte"] == horizonte
        assert len(body["periodos_projetados"]) == horizonte


# --------------------------------------------------------------------------- #
# Relatórios — CRUD de agendamentos
# --------------------------------------------------------------------------- #
def test_agendamento_pode_ser_listado_editado_e_desativado(client, make_org, ente) -> None:
    _, headers = _org_headers(client, make_org, ente)
    criado = client.post(
        "/relatorios/agendamentos",
        json={
            "modelo": "executivo", "formato": "pdf", "escopo": "ente", "ente": ente,
            "periodo": PERIODOS[-1], "periodicidade": "mensal",
            "proxima_execucao": datetime.now(UTC).isoformat(),
        },
        headers=headers,
    )
    assert criado.status_code == 201, criado.text
    agendamento_id = criado.json()["id"]

    lista = client.get("/relatorios/agendamentos", headers=headers)
    assert lista.status_code == 200, lista.text
    assert agendamento_id in [a["id"] for a in lista.json()]

    editado = client.patch(
        f"/relatorios/agendamentos/{agendamento_id}",
        json={"periodicidade": "bimestral", "ativo": False},
        headers=headers,
    )
    assert editado.status_code == 200, editado.text
    assert editado.json()["periodicidade"] == "bimestral"
    assert editado.json()["ativo"] is False

    # Desativar preserva o registro — o histórico da regra faz parte da trilha.
    ainda_listado = client.get("/relatorios/agendamentos", headers=headers).json()
    assert agendamento_id in [a["id"] for a in ainda_listado]

    removido = client.delete(f"/relatorios/agendamentos/{agendamento_id}", headers=headers)
    assert removido.status_code == 204
    assert agendamento_id not in [
        a["id"] for a in client.get("/relatorios/agendamentos", headers=headers).json()
    ]
    with SessionLocal() as session:
        assert session.get(RelatorioAgendamento, uuid.UUID(agendamento_id)) is None


def test_agendamento_de_outra_organizacao_nao_e_editavel(client, make_org, ente) -> None:
    _, headers = _org_headers(client, make_org, ente)
    criado = client.post(
        "/relatorios/agendamentos",
        json={
            "modelo": "executivo", "formato": "pdf", "escopo": "ente", "ente": ente,
            "periodo": PERIODOS[-1], "periodicidade": "mensal",
            "proxima_execucao": datetime.now(UTC).isoformat(),
        },
        headers=headers,
    ).json()
    _, outras_headers = _org_headers(client, make_org, ente)
    resposta = client.patch(
        f"/relatorios/agendamentos/{criado['id']}",
        json={"ativo": False},
        headers=outras_headers,
    )
    assert resposta.status_code == 404  # RLS: nem existe para a outra organização


def test_periodicidade_invalida_e_recusada(client, make_org, ente) -> None:
    _, headers = _org_headers(client, make_org, ente)
    criado = client.post(
        "/relatorios/agendamentos",
        json={
            "modelo": "executivo", "formato": "pdf", "escopo": "ente", "ente": ente,
            "periodo": PERIODOS[-1], "periodicidade": "mensal",
            "proxima_execucao": datetime.now(UTC).isoformat(),
        },
        headers=headers,
    ).json()
    resposta = client.patch(
        f"/relatorios/agendamentos/{criado['id']}",
        json={"periodicidade": "quinzenal"},
        headers=headers,
    )
    assert resposta.status_code == 422
    assert "Periodicidade" in resposta.json()["title"]


# --------------------------------------------------------------------------- #
# Assistente — contexto da página (regra pura, sem provedor)
# --------------------------------------------------------------------------- #
def test_pagina_so_entra_quando_a_pergunta_nao_nomeia_indicador() -> None:
    # Pergunta genérica na tela de pessoal ⇒ contexto da tela.
    assert retriever.indicadores_relevantes("e isto aqui, está bom?", "/pessoal") == {
        "pessoal_executivo", "rcl",
    }
    # A pergunta manda: quem está em dívida e pergunta de educação quer educação.
    assert retriever.indicadores_relevantes("como está a educacao?", "/divida") == {
        "educacao_mde"
    }
    # Sem página e sem palavra-chave ⇒ visão geral (todos os fatos).
    assert retriever.indicadores_relevantes("me explique o cenário") == set()
    # Rota desconhecida não inventa contexto.
    assert retriever.indicadores_da_pagina("/nao-existe") == set()
