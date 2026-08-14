"""Sprint IA-5 — IA nas telas: as quatro capacidades da §4 do plano de MCP.

O que estes testes fixam, em ordem de importância:

1. **Nenhuma resposta publica número sem lastro** (G1/G6). Provado nos dois sentidos: com
   o provedor extrativo o laudo sai ``ok``; com um provedor que inventa um valor, o laudo
   sai ``sinalizado`` e o aviso vai **no corpo** da resposta.
2. **Ausência é ausência declarada, nunca prosa vaga** (G3): sem dado, o modelo não é
   chamado — e isso é provado com um provedor que estoura se for chamado, não por
   inspeção do texto.
3. **Escopo, licença e ``as_of`` valem** — as quatro capacidades passam pelo envelope, e
   os dois 403 continuam distinguíveis (A22/E1).
4. **A ordenação da fila de alertas continua determinística**: a explicação repete a
   ordem de ``alerts/rules.py``; a IA não reordena.

Tudo offline: ``ASSISTANT_PROVIDER=local`` no ``conftest``; onde o teste precisa de um
comportamento específico do provedor, ele é injetado por ``dependency_overrides``.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.db import admin_session
from app.main import app
from app.modules.alerts import rules as alert_rules
from app.modules.alerts import service as alerts_service
from app.modules.alerts.models import Alerta, CalendarioObrigacao
from app.modules.assistant.llm import LLMProviderError, LLMResult, get_llm_provider
from app.modules.assistant.models import ConversaUso, IaToolCall
from app.modules.catalog.models import DimEnte
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega
from app.modules.insights import service as insights_service
from app.modules.tenancy.models import AuditLog

from .conftest import auth_header, login

PERIODO = "2090-B4"
VERSAO_ANTIGA = "ia5-v1"
VERSAO_VIGENTE = "ia5-v2"
HOMOLOGADA_ANTIGA = datetime(2024, 1, 20, tzinfo=UTC)
HOMOLOGADA_VIGENTE = datetime(2024, 6, 10, tzinfo=UTC)
AS_OF_RETROATIVO = datetime(2024, 3, 1, tzinfo=UTC)

#: Pessoal estava dentro do teto na entrega superada e passou a estourar na retificação —
#: o par que prova que a explicação respeita a bitemporalidade em vez de responder sempre
#: pelo número de hoje. A faixa vigente é ``excedido`` de propósito: é ela que faz o motor
#: de alertas (Sprint 15) produzir um alerta **crítico de limite**, que é o caso que a
#: explicação da fila precisa exercitar (o primeiro da fila com providência legal).
PCT_PESSOAL_ANTIGO = Decimal("48.10")
PCT_PESSOAL_VIGENTE = Decimal("56.20")
RCL = Decimal("400000000")


@dataclass(frozen=True)
class Cenario:
    ente: str


def _cod_ente() -> str:
    return "9" + "".join(random.choices("0123456789", k=6))


@pytest.fixture
def cenario() -> Iterator[Cenario]:
    """Município sintético com RREO retificado e ``pessoal_executivo`` nas duas versões."""
    cod = _cod_ente()
    with admin_session() as s:
        s.add(
            DimEnte(
                cod_ibge=cod,
                nome="Município da Tela",
                esfera="municipal",
                uf="CE",
                regiao="Nordeste",
                populacao=120_000,
                pib=Decimal("1200000000"),
                rpps=False,
                possui_tcm=False,
            )
        )
        for versao, homologada, vigente in (
            (VERSAO_ANTIGA, HOMOLOGADA_ANTIGA, False),
            (VERSAO_VIGENTE, HOMOLOGADA_VIGENTE, True),
        ):
            s.add(
                DimEntrega(
                    cod_ibge=cod,
                    relatorio="RREO",
                    periodo=PERIODO,
                    versao_entrega=versao,
                    homologada_em=homologada,
                    vigente=vigente,
                    hash_payload=f"hash-{cod}-{versao}",
                )
            )
            s.add(
                FatoRcl(
                    cod_ibge=cod,
                    periodo_ref=PERIODO,
                    rcl_12m=RCL,
                    receita_corrente=Decimal("450000000"),
                    deducoes=Decimal("50000000"),
                    versao_entrega=versao,
                    memoria={"formula": "receita_corrente - deducoes"},
                )
            )
        for versao, pct, faixa in (
            (VERSAO_ANTIGA, PCT_PESSOAL_ANTIGO, "normal"),
            (VERSAO_VIGENTE, PCT_PESSOAL_VIGENTE, "excedido"),
        ):
            s.add(
                MartIndicador(
                    cod_ibge=cod,
                    periodo=PERIODO,
                    indicador="pessoal_executivo",
                    valor_rs=(pct / Decimal(100)) * RCL,
                    valor_pct_rcl=pct,
                    faixa=faixa,
                    teto_pct=Decimal("54"),
                    denominador="rcl",
                    base_valor=RCL,
                    versao_entrega=versao,
                    source_ref={
                        "relatorio": "RGF",
                        "anexo": "Anexo 01",
                        "periodo": PERIODO,
                        "versao_entrega": versao,
                        "indicador": "pessoal_executivo",
                        "esfera": "municipal",
                    },
                )
            )
    yield Cenario(ente=cod)
    alerts_service.esquecer_avaliacoes()
    with admin_session() as s:
        s.execute(delete(Alerta).where(Alerta.cod_ibge == cod))
        s.execute(delete(CalendarioObrigacao).where(CalendarioObrigacao.cod_ibge == cod))
        s.execute(delete(MartIndicador).where(MartIndicador.cod_ibge == cod))
        s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == cod))
        s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == cod))
        s.execute(delete(DimEnte).where(DimEnte.cod_ibge == cod))


class ProvedorQueExplode:
    """Provedor que reprova o teste se for chamado — a prova de que a recusa não o aciona."""

    name = "explode"

    def chat(self, request):  # noqa: ANN001, ANN201
        raise AssertionError(
            "O provedor foi acionado num caminho de ausência declarada (G3 violado)."
        )


class ProvedorQueInventa:
    """Provedor que cita um número que nenhuma ferramenta devolveu (para exercitar o G6)."""

    name = "inventa"

    def chat(self, request):  # noqa: ANN001
        return LLMResult(
            texto=(
                "A despesa com pessoal do ente está em 91,37% da RCL, o que exige "
                "providência imediata."
            ),
            modelo="fake-inventa",
            tokens_entrada=10,
            tokens_saida=10,
        )


class ProvedorIndisponivel:
    """Provedor fora do ar — tem de virar RFC 7807, nunca resposta sem fonte (§9)."""

    name = "indisponivel"

    def chat(self, request):  # noqa: ANN001
        raise LLMProviderError(detail="Provedor fora do ar no teste.")


def _com_provedor(provedor):  # noqa: ANN001, ANN201
    app.dependency_overrides[get_llm_provider] = lambda: provedor


def _sem_override() -> None:
    app.dependency_overrides.pop(get_llm_provider, None)


@pytest.fixture(autouse=True)
def _limpar_override() -> Iterator[None]:
    yield
    _sem_override()


def _post(client, tok, rota: str, body: dict):  # noqa: ANN001, ANN201
    return client.post(f"/ia/{rota}", json=body, headers=auth_header(tok))


def _chamadas(org_id: uuid.UUID) -> list[IaToolCall]:
    with admin_session() as s:
        return list(
            s.scalars(
                select(IaToolCall)
                .where(IaToolCall.org_id == org_id)
                .order_by(IaToolCall.criado_em)
            )
        )


def _titulos(corpo: dict) -> list[str]:
    return [nota["titulo"] for nota in corpo["notas"]]


# --------------------------------------------------------------------------- #
# 1. "Explique este número"
# --------------------------------------------------------------------------- #
def test_explicacao_traz_linhagem_memoria_base_legal_e_o_que_muda_a_faixa(
    client, make_org, cenario
) -> None:
    """O item 1 da §4 inteiro: os quatro pedaços numa resposta só, com fonte."""
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)

    resposta = _post(
        client,
        tok,
        "explicar-numero",
        {"ente": cenario.ente, "indicador": "pessoal_executivo", "periodo": PERIODO},
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()

    assert corpo["capacidade"] == "explicar_numero"
    assert corpo["disponivel"] is True
    titulos = _titulos(corpo)
    assert "Memória de cálculo" in titulos
    assert "De onde vem este número (linhagem)" in titulos
    assert "O que mudaria a faixa" in titulos
    assert any(t.startswith("Providência legal") for t in titulos)

    # Critério de aceite: source_ref visível — no fato e na lista de fontes da resposta.
    assert corpo["source_refs"], "resposta sem nenhuma procedência"
    fato = corpo["fatos"][0]
    assert fato["source_ref"]["relatorio"] == "RGF"
    assert fato["source_ref"]["versao_entrega"] == VERSAO_VIGENTE
    assert corpo["fontes"], "sem chip de fonte para exibir na tela"
    # E a cadeia de ferramentas que a sustentou está declarada.
    assert set(corpo["ferramentas"]) == {
        "indicador_do_ente",
        "limites_do_ente",
        "linhagem_do_indicador",
    }


def test_explicacao_nao_publica_numero_sem_lastro(client, make_org, cenario) -> None:
    """G6 — o provedor extrativo não inventa, e o laudo diz isso com números."""
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)
    corpo = _post(
        client,
        tok,
        "explicar-numero",
        {"ente": cenario.ente, "indicador": "pessoal_executivo", "periodo": PERIODO},
    ).json()
    assert corpo["verificacao"]["status"] == "ok"
    assert corpo["verificacao"]["sem_lastro"] == []
    assert corpo["verificacao"]["total_citados"] >= 1


def test_numero_forjado_pelo_modelo_e_sinalizado_no_corpo(client, make_org, cenario) -> None:
    """Um número que nenhuma ferramenta devolveu é sinalizado — não publicado em silêncio."""
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)
    _com_provedor(ProvedorQueInventa())

    corpo = _post(
        client,
        tok,
        "explicar-numero",
        {"ente": cenario.ente, "indicador": "pessoal_executivo", "periodo": PERIODO},
    ).json()
    assert corpo["verificacao"]["status"] == "sinalizado"
    assert "91,37%" in corpo["verificacao"]["sem_lastro"]
    assert "G6" in corpo["resposta"], "o aviso tem de ir no corpo, não só num campo"


def test_indicador_nao_materializado_e_ausencia_declarada_sem_chamar_o_modelo(
    client, make_org, cenario
) -> None:
    """G3 herdado: sem dado, a resposta é a ausência — e o provedor sequer é acionado."""
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)
    _com_provedor(ProvedorQueExplode())

    resposta = _post(
        client,
        tok,
        "explicar-numero",
        {"ente": cenario.ente, "indicador": "operacoes_credito", "periodo": PERIODO},
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["disponivel"] is False
    assert corpo["ausencia"]
    assert "não estima" in corpo["ausencia"]
    assert corpo["uso"]["modelo"] == "n/a"
    assert corpo["verificacao"] is None
    assert corpo["dados_incompletos"][0]["codigo"] == "operacoes_credito"


def test_as_of_retroativo_explica_a_versao_de_entao(client, make_org, cenario) -> None:
    """§6.5: a explicação histórica descreve a faixa daquela entrega, não a de hoje."""
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)

    vigente = _post(
        client,
        tok,
        "explicar-numero",
        {"ente": cenario.ente, "indicador": "pessoal_executivo", "periodo": PERIODO},
    ).json()
    retroativo = _post(
        client,
        tok,
        "explicar-numero",
        {
            "ente": cenario.ente,
            "indicador": "pessoal_executivo",
            "periodo": PERIODO,
            "as_of": AS_OF_RETROATIVO.isoformat(),
        },
    ).json()

    assert vigente["fatos"][0]["valor"] == str(PCT_PESSOAL_VIGENTE)
    assert vigente["fatos"][0]["faixa"] == "excedido"
    assert retroativo["fatos"][0]["valor"] == str(PCT_PESSOAL_ANTIGO)
    assert retroativo["fatos"][0]["faixa"] == "normal"
    assert retroativo["fatos"][0]["source_ref"]["versao_entrega"] == VERSAO_ANTIGA


def test_ente_fora_da_carteira_e_ente_sem_licenca_sao_403_distintos(
    client, make_org, cenario
) -> None:
    """A garantia mora na ferramenta: a tela nova herda os dois 403 da E1, sem código novo."""
    fora = make_org(entes=[])
    tok_fora = login(client, fora.email, fora.senha)
    negado = _post(
        client,
        tok_fora,
        "explicar-numero",
        {"ente": cenario.ente, "indicador": "pessoal_executivo"},
    )
    assert negado.status_code == 403

    sem_licenca = make_org(entes=[cenario.ente], licenciar=False)
    tok_sem = login(client, sem_licenca.email, sem_licenca.senha)
    negado_licenca = _post(
        client,
        tok_sem,
        "explicar-numero",
        {"ente": cenario.ente, "indicador": "pessoal_executivo"},
    )
    assert negado_licenca.status_code == 403
    assert negado.json()["type"] != negado_licenca.json()["type"]


def test_sem_capacidade_usar_ia_a_tela_nao_responde(client, make_org, cenario) -> None:
    org = make_org(entes=[cenario.ente], capacidades=["ver"])
    tok = login(client, org.email, org.senha)
    resposta = _post(
        client, tok, "explicar-numero", {"ente": cenario.ente, "indicador": "pessoal_executivo"}
    )
    assert resposta.status_code == 403


def test_falha_do_provedor_vira_rfc7807_e_nao_resposta_sem_fonte(
    client, make_org, cenario
) -> None:
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)
    _com_provedor(ProvedorIndisponivel())
    resposta = _post(
        client,
        tok,
        "explicar-numero",
        {"ente": cenario.ente, "indicador": "pessoal_executivo", "periodo": PERIODO},
    )
    assert resposta.status_code == 502
    assert resposta.json()["type"].endswith("llm-provider-unavailable")


def test_cada_chamada_de_tela_deixa_trilha_de_ferramenta_e_de_consumo(
    client, make_org, cenario
) -> None:
    """G7 + cota: a cadeia vai para ``op.ia_tool_call`` e o consumo para ``op.conversa_uso``."""
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)
    _post(
        client,
        tok,
        "explicar-numero",
        {"ente": cenario.ente, "indicador": "pessoal_executivo", "periodo": PERIODO},
    )

    chamadas = _chamadas(org.org_id)
    assert chamadas, "nenhuma chamada de ferramenta auditada"
    assert {c.origem for c in chamadas} == {"tela"}
    assert {c.ferramenta for c in chamadas} >= {"indicador_do_ente", "linhagem_do_indicador"}
    assert all(c.status == "ok" for c in chamadas)

    with admin_session() as s:
        usos = list(s.scalars(select(ConversaUso).where(ConversaUso.org_id == org.org_id)))
        trilha = list(
            s.scalars(
                select(AuditLog).where(
                    AuditLog.org_id == org.org_id, AuditLog.acao == "INSIGHT_IA"
                )
            )
        )
    assert len(usos) == 1, "o consumo da tela precisa contar para a cota da organização"
    assert usos[0].conversa_id is None, "IA de tela não é conversa"
    assert trilha and "insight:explicar_numero" in trilha[0].recurso


# --------------------------------------------------------------------------- #
# 2. Explicação da fila de alertas (ordenação continua determinística)
# --------------------------------------------------------------------------- #
def test_explicacao_da_fila_preserva_a_ordem_da_regra(client, make_org, cenario) -> None:
    """A IA explica a fila que ``alerts/rules.py`` ordenou — e não a reordena."""
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)
    alerts_service.esquecer_avaliacoes()
    fila = client.get(
        "/alertas", params={"escopo": "ente", "ente": cenario.ente}, headers=auth_header(tok)
    )
    assert fila.status_code == 200, fila.text
    titulos_fila = [a["titulo"] for a in fila.json()["alertas"]]
    assert titulos_fila, "o cenário precisa gerar ao menos um alerta"

    alerts_service.esquecer_avaliacoes()
    corpo = _post(client, tok, "explicar-alertas", {"ente": cenario.ente}).json()
    assert corpo["disponivel"] is True
    nota_fila = next(n for n in corpo["notas"] if n["titulo"] == "Fila completa, na ordem da regra")
    ordem_explicada = [linha.split("] ", 1)[1] for linha in nota_fila["linhas"]]
    assert ordem_explicada == titulos_fila

    criterio = next(n for n in corpo["notas"] if n["titulo"] == "Como a fila foi ordenada")
    assert criterio["origem"] == "alerts/rules.py::prioridade"
    assert criterio["linhas"] == list(alert_rules.criterio_ordenacao())


def test_explicacao_da_fila_traz_providencia_legal_e_fonte(client, make_org, cenario) -> None:
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)
    alerts_service.esquecer_avaliacoes()
    corpo = _post(client, tok, "explicar-alertas", {"ente": cenario.ente}).json()

    assert any(n["origem"] == "gold.dim_providencia_legal" for n in corpo["notas"])
    assert corpo["source_refs"], "a explicação da fila também declara procedência"
    assert corpo["verificacao"]["status"] == "ok"


def test_fila_vazia_e_ausencia_declarada_sem_chamar_o_modelo(client, make_org) -> None:
    """Ente sem alerta: a resposta diz isso, e o provedor não é acionado."""
    org = make_org(entes=["9999999"])
    tok = login(client, org.email, org.senha)
    _com_provedor(ProvedorQueExplode())
    corpo = _post(client, tok, "explicar-alertas", {"ente": "9999999"}).json()
    assert corpo["disponivel"] is False
    assert corpo["ausencia"]
    assert corpo["uso"]["modelo"] == "n/a"


# --------------------------------------------------------------------------- #
# 3. Narrativa do relatório
# --------------------------------------------------------------------------- #
def test_narrativa_usa_os_mesmos_numeros_e_fontes_do_documento(
    client, make_org, cenario
) -> None:
    """Nenhum número novo: todo fato da narrativa vem do documento, com a fonte dele."""
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)
    corpo = _post(
        client,
        tok,
        "narrar-relatorio",
        {"ente": cenario.ente, "periodo": PERIODO, "modelo": "executivo"},
    ).json()

    assert corpo["disponivel"] is True
    assert corpo["ferramentas"] == ["documento_do_relatorio"]
    disponiveis = [f for f in corpo["fatos"] if f["disponivel"]]
    assert disponiveis, "o relatório executivo tem de trazer ao menos a RCL"
    assert all(f["source_ref"] and f["source_ref"]["relatorio"] for f in disponiveis)
    assert corpo["verificacao"]["status"] == "ok"
    # As ausências do modelo continuam declaradas — o relatório não omite item.
    assert any(i["tipo"] == "ausente" for i in corpo["dados_incompletos"])
    assert any(n["titulo"] == "Ausências declaradas pelo relatório" for n in corpo["notas"])


def test_narrativa_de_ente_sem_metrica_e_ausencia_declarada(client, make_org) -> None:
    org = make_org(entes=["9999998"])
    tok = login(client, org.email, org.senha)
    _com_provedor(ProvedorQueExplode())
    corpo = _post(client, tok, "narrar-relatorio", {"ente": "9999998"}).json()
    assert corpo["disponivel"] is False
    assert corpo["ausencia"]
    assert corpo["uso"]["modelo"] == "n/a"


def test_narrativa_recusa_modelo_desconhecido(client, make_org, cenario) -> None:
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)
    resposta = _post(
        client,
        tok,
        "narrar-relatorio",
        {"ente": cenario.ente, "periodo": PERIODO, "modelo": "inexistente"},
    )
    assert resposta.status_code == 422


# --------------------------------------------------------------------------- #
# 4. Busca em linguagem natural na Central de Dados
# --------------------------------------------------------------------------- #
def test_pergunta_sobre_saude_e_respondida_por_cobertura_qualidade_e_calendario(
    client, make_org, cenario
) -> None:
    """"Por que Saúde está vazia para meu município?" — as três pernas na resposta."""
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)
    corpo = _post(
        client,
        tok,
        "central-dados",
        {
            "ente": cenario.ente,
            "pergunta": "Por que a página de saúde está vazia para o meu município?",
        },
    ).json()

    assert corpo["disponivel"] is True
    assert corpo["ferramentas"] == [
        "cobertura_do_ente",
        "qualidade_do_ente",
        "calendario_do_ente",
    ]
    origens = {n["origem"] for n in corpo["notas"]}
    assert origens == {"mart_cobertura_fonte", "data_quality_check", "gold.calendario_obrigacao"}
    cobertura = next(n for n in corpo["notas"] if n["origem"] == "mart_cobertura_fonte")
    assert cobertura["titulo"] == "Cobertura da página 'saude-educacao'"
    assert corpo["verificacao"]["status"] == "ok"


def test_pergunta_sem_pagina_deduzivel_recusa_util_sem_chamar_o_modelo(
    client, make_org, cenario
) -> None:
    """Recusa útil (§6.1): "não sei, e este é o catálogo do que sei responder"."""
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)
    _com_provedor(ProvedorQueExplode())
    corpo = _post(
        client,
        tok,
        "central-dados",
        {"ente": cenario.ente, "pergunta": "quem ganhou o campeonato de 1997?"},
    ).json()
    assert corpo["disponivel"] is False
    assert "saude-educacao" in corpo["ausencia"]
    assert corpo["uso"]["modelo"] == "n/a"


def test_roteamento_de_pergunta_para_pagina() -> None:
    """O roteamento é vocabulário, não interpretação — e página desconhecida é ``None``."""
    assert insights_service.pagina_da_pergunta("cadê os dados de saúde?") == "saude-educacao"
    assert insights_service.pagina_da_pergunta("e a dívida consolidada?") == "divida"
    assert insights_service.pagina_da_pergunta("gasto com folha") == "limites"
    assert insights_service.pagina_da_pergunta("qualquer coisa aleatória") is None
    assert "saude-educacao" in insights_service.paginas_conhecidas()


def test_pagina_explicita_vence_a_deducao(client, make_org, cenario) -> None:
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)
    corpo = _post(
        client,
        tok,
        "central-dados",
        {"ente": cenario.ente, "pergunta": "e aqui?", "pagina": "limites"},
    ).json()
    cobertura = next(n for n in corpo["notas"] if n["origem"] == "mart_cobertura_fonte")
    assert cobertura["titulo"] == "Cobertura da página 'limites'"


# --------------------------------------------------------------------------- #
# Contrato comum às quatro capacidades
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("rota", "corpo"),
    [
        ("explicar-numero", {"indicador": "pessoal_executivo"}),
        ("explicar-alertas", {}),
        ("narrar-relatorio", {}),
        ("central-dados", {"pergunta": "e a saúde?"}),
    ],
)
def test_as_quatro_capacidades_respondem_com_o_mesmo_envelope(
    client, make_org, cenario, rota: str, corpo: dict
) -> None:
    """Um envelope só: quem sabe exibir uma superfície sabe exibir as quatro."""
    org = make_org(entes=[cenario.ente])
    tok = login(client, org.email, org.senha)
    alerts_service.esquecer_avaliacoes()
    resposta = _post(client, tok, rota, {"ente": cenario.ente, **corpo})
    assert resposta.status_code == 200, resposta.text
    dados = resposta.json()
    for campo in (
        "capacidade",
        "titulo",
        "ente",
        "pergunta",
        "resposta",
        "disponivel",
        "fatos",
        "notas",
        "fontes",
        "source_refs",
        "ferramentas",
        "uso",
        "gerado_em",
    ):
        assert campo in dados, f"{rota} não devolveu '{campo}'"
    assert dados["pergunta"].strip(), "cada superfície declara a pergunta que responde"
    assert dados["resposta"].strip()


@pytest.mark.parametrize(
    ("rota", "corpo"),
    [
        ("explicar-numero", {"indicador": "pessoal_executivo"}),
        ("explicar-alertas", {}),
        ("narrar-relatorio", {}),
        ("central-dados", {"pergunta": "e a saúde?"}),
    ],
)
def test_nenhuma_capacidade_entrega_ente_de_outra_organizacao(
    client, make_org, cenario, rota: str, corpo: dict
) -> None:
    """A matriz de isolamento vale para as quatro superfícies novas, não só para uma."""
    intruso = make_org(entes=[])
    tok = login(client, intruso.email, intruso.senha)
    resposta = _post(client, tok, rota, {"ente": cenario.ente, **corpo})
    assert resposta.status_code == 403, resposta.text
    assert cenario.ente not in resposta.text or "escopo" in resposta.text.lower()
