"""Sprint IA-7 — a resposta que o gestor lê: conversa multi-turno, escopo e legibilidade.

Três coisas são provadas aqui, e nenhuma delas é sobre o modelo:

1. **Continuidade** — "e por que isso aconteceu?" é respondida no contexto do turno
   anterior, sem o cliente repetir ente nem período.
2. **Isolamento** — histórico não atravessa organização (404) nem ente que saiu do escopo
   (o turno é descartado, e a conversa segue). Conversa carrega assunto, nunca permissão.
3. **Redação** — as seis regras invioláveis do §9 continuam no ``SYSTEM_PROMPT``, e a
   legibilidade passa a ser medida por régua objetiva, não por impressão.

Tudo offline: provedor falso injetado por ``dependency_overrides``, *embedder* local.
"""

# Fixtures importadas de ``test_assistant`` reaparecem como argumentos dos testes.
# ruff: noqa: F811

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.core.db import admin_session
from app.main import app
from app.modules.assistant import didatica, retriever
from app.modules.assistant import llm as llm
from app.modules.assistant import service as assistant_service
from app.modules.assistant.llm import (
    ExecutorFerramenta,
    FatoContexto,
    LLMRequest,
    LLMResult,
    ToolSpec,
    get_llm_provider,
)
from app.modules.assistant.models import Conversa, IaToolCall
from app.modules.assistant.schemas import FatoResposta
from app.modules.assistant.service import (
    MAX_TURNOS_CONTEXTO,
    SYSTEM_PROMPT,
)
from app.modules.catalog.models import DimEnte
from app.modules.indicators.models import FatoRcl
from app.modules.ingestion.models import DimEntrega
from app.modules.tenancy import repository as tenancy_repo
from app.modules.tenancy.models import Licenca, MembershipEscopo
from app.shared.tooling import verificacao
from tests.conftest import auth_header, login
from tests.test_assistant import (  # noqa: F401 — fixtures reusadas (mesmo cenário gold)
    PERIODO,
    Cenario,
    FakeProvider,
    cenario_com_rcl,
    use_fake,
)

PERGUNTA_1 = "Qual é a Receita Corrente Líquida e o que ela significa?"
ACOMPANHAMENTO = "E por que isso aconteceu?"


class ProvedorQueRepeteValor:
    """Provedor que reafirma um valor do turno anterior — o caso que o G6 poderia acusar."""

    name = "repete-valor"

    def __init__(self, texto: str) -> None:
        self.texto = texto
        self.calls: list[LLMRequest] = []

    def chat(self, request: LLMRequest) -> LLMResult:
        self.calls.append(request)
        return LLMResult(texto=self.texto, modelo="fake-model", tokens_entrada=10, tokens_saida=5)


class ProvedorQueChamaQualidade:
    """Expõe os argumentos finais que o serviço entrega ao envelope governado."""

    name = "chama-qualidade"

    def __init__(self, argumentos: dict) -> None:
        self.argumentos = argumentos

    def chat(self, request: LLMRequest) -> LLMResult:  # pragma: no cover - ramo inválido
        raise AssertionError("provedor com function calling não deve cair em chat()")

    def chat_com_ferramentas(
        self,
        request: LLMRequest,
        ferramentas: list[ToolSpec],
        executar: ExecutorFerramenta,
    ) -> LLMResult:
        executar("qualidade_do_ente", self.argumentos)
        return LLMResult(texto="ok", modelo=self.name, tokens_entrada=1, tokens_saida=1)


def _argumentos_finais_da_qualidade(
    monkeypatch: pytest.MonkeyPatch,
    *,
    contexto: retriever.GroundedContext,
    argumentos_do_modelo: dict,
) -> dict:
    capturados: dict = {}

    def invoke_falso(
        _tool_ctx: object,
        _registro: object,
        nome: str,
        argumentos: dict,
    ) -> SimpleNamespace:
        assert nome == "qualidade_do_ente"
        capturados.update(argumentos)
        return SimpleNamespace(payload={"observacao": "sem checks"})

    monkeypatch.setattr(assistant_service.tooling, "invoke", invoke_falso)
    assistant_service._chamar_provedor(
        object(),  # type: ignore[arg-type] - unidade pura: o envelope foi substituído acima
        object(),  # type: ignore[arg-type] - idem para o principal
        ProvedorQueChamaQualidade(argumentos_do_modelo),
        LLMRequest(system="s", pergunta="p"),
        cod_ibge=contexto.ente,
        ctx=contexto,
    )
    return capturados


def test_function_call_herda_as_of_e_periodo_ausentes_do_contexto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corte = datetime(2025, 1, 10, 12, 30, tzinfo=UTC)
    contexto = retriever.GroundedContext(
        ente="2304400",
        ente_nome="Fortaleza",
        esfera="municipal",
        periodo="2024-B6",
        as_of=corte,
        as_of_fixo=True,
    )

    argumentos = _argumentos_finais_da_qualidade(
        monkeypatch, contexto=contexto, argumentos_do_modelo={}
    )

    assert argumentos["ente"] == contexto.ente
    assert argumentos["as_of"] == corte
    assert argumentos["periodo"] == contexto.periodo


def test_function_call_sobrescreve_as_of_e_preserva_periodo_explicito(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corte = datetime(2025, 1, 10, 12, 30, tzinfo=UTC)
    contexto = retriever.GroundedContext(
        ente="2304400",
        ente_nome="Fortaleza",
        esfera="municipal",
        periodo="2024-B6",
        as_of=corte,
        as_of_fixo=True,
    )
    periodo_comparado = "2023-B6"

    argumentos = _argumentos_finais_da_qualidade(
        monkeypatch,
        contexto=contexto,
        argumentos_do_modelo={
            "ente": "1100015",
            "as_of": datetime(2099, 12, 31, tzinfo=UTC),
            "periodo": periodo_comparado,
        },
    )

    assert argumentos["ente"] == contexto.ente
    assert argumentos["as_of"] == corte
    assert argumentos["periodo"] == periodo_comparado


def _perguntar(client, headers, **body):
    return client.post("/assistant/perguntar", headers=headers, json=body)


# --------------------------------------------------------------------------- #
# 1. Continuidade
# --------------------------------------------------------------------------- #
def test_acompanhamento_herda_ente_e_periodo_sem_repetir_na_pergunta(
    client,
    make_org,
    use_fake,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """O critério de aceite da ficha, literal: pergunta de acompanhamento sem ente."""
    provider, _ = use_fake
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))

    primeiro = _perguntar(client, headers, ente=cenario_com_rcl.ente, pergunta=PERGUNTA_1)
    assert primeiro.status_code == 200, primeiro.text
    t1 = primeiro.json()
    assert t1["turnos_no_contexto"] == 0  # pergunta isolada: nada antes dela

    # O corpo NÃO repete ente nem período — é isto que não existia antes da IA-7.
    segundo = _perguntar(client, headers, pergunta=ACOMPANHAMENTO, conversa_id=t1["conversa_id"])
    assert segundo.status_code == 200, segundo.text
    t2 = segundo.json()

    assert t2["ente"] == cenario_com_rcl.ente, "o ente foi herdado do turno anterior"
    assert t2["periodo"] == PERIODO, "o período foi herdado do turno anterior"
    assert t2["turnos_no_contexto"] == 1
    assert t2["turnos_descartados"] == 0
    assert t2["thread_id"] == t1["thread_id"] == t1["conversa_id"]
    assert t2["parent_id"] == t1["conversa_id"]

    # O provedor recebeu a conversa — não uma pergunta solta sem sujeito.
    historico = provider.calls[-1].historico
    assert len(historico) == 1
    assert historico[0].pergunta == PERGUNTA_1
    assert historico[0].ente == cenario_com_rcl.ente
    assert provider.calls[-1].pergunta == ACOMPANHAMENTO  # a pergunta chega como escrita

    # Os dois turnos são o mesmo fio em op.conversa.
    with admin_session() as s:
        linhas = list(
            s.scalars(
                select(Conversa).where(Conversa.org_id == org.org_id).order_by(Conversa.criado_em)
            )
        )
    assert len(linhas) == 2
    assert linhas[0].thread_id == linhas[0].id
    assert linhas[1].thread_id == linhas[0].id
    assert linhas[0].parent_id is None
    assert linhas[1].parent_id == linhas[0].id

    historico = client.get("/assistant/conversas", headers=headers)
    assert historico.status_code == 200, historico.text
    item = next(i for i in historico.json()["itens"] if i["id"] == t2["conversa_id"])
    assert item["thread_id"] == t1["conversa_id"]
    assert item["parent_id"] == t1["conversa_id"]
    assert item["as_of"] is not None
    assert item["source_refs"]
    assert item["verificacao"]["status"] in {"ok", "sinalizado"}


def test_ancora_antiga_nao_enxerga_turno_de_outro_ramo(
    client,
    make_org,
    use_fake,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """``conversa_id`` é causal: dois ramos de T1 não viram uma sequência falsa."""
    provider, _ = use_fake
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))

    t1 = _perguntar(client, headers, ente=cenario_com_rcl.ente, pergunta=PERGUNTA_1).json()
    t2 = _perguntar(
        client, headers, pergunta="Primeiro ramo: por quê?", conversa_id=t1["conversa_id"]
    ).json()
    t3 = _perguntar(
        client, headers, pergunta="Segundo ramo: o que fazer?", conversa_id=t1["conversa_id"]
    ).json()

    assert [turno.pergunta for turno in provider.calls[-1].historico] == [PERGUNTA_1]
    assert t2["parent_id"] == t1["conversa_id"]
    assert t3["parent_id"] == t1["conversa_id"]
    assert t2["thread_id"] == t3["thread_id"] == t1["thread_id"]


def test_acompanhamento_herda_as_of_bitemporal(
    client,
    make_org,
    use_fake,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """O segundo turno não troca silenciosamente a fotografia histórica pela vigente."""
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))
    fotografia = datetime(2025, 1, 10, 12, 30, tzinfo=UTC)

    t1 = _perguntar(
        client,
        headers,
        ente=cenario_com_rcl.ente,
        pergunta=PERGUNTA_1,
        as_of=fotografia.isoformat(),
    ).json()
    t2 = _perguntar(client, headers, pergunta=ACOMPANHAMENTO, conversa_id=t1["conversa_id"]).json()

    assert datetime.fromisoformat(t2["as_of"].replace("Z", "+00:00")) == fotografia


def test_terceiro_turno_recupera_ultimo_ancestral_que_nomeou_assunto(
    client,
    make_org,
    use_fake,
    cenario_com_rcl: Cenario,
    monkeypatch,  # noqa: F811
) -> None:
    """Duas perguntas elípticas seguidas não apagam o indicador nomeado em T1."""
    from app.modules.assistant import retriever

    recuperacoes: list[str | None] = []
    original = retriever.build_context

    def capturar(*args, **kwargs):
        recuperacoes.append(kwargs.get("pergunta_recuperacao"))
        return original(*args, **kwargs)

    monkeypatch.setattr(retriever, "build_context", capturar)
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))
    t1 = _perguntar(client, headers, ente=cenario_com_rcl.ente, pergunta=PERGUNTA_1).json()
    t2 = _perguntar(client, headers, pergunta=ACOMPANHAMENTO, conversa_id=t1["conversa_id"]).json()
    _perguntar(
        client,
        headers,
        pergunta="E o que o gestor pode fazer?",
        conversa_id=t2["conversa_id"],
    )

    assert recuperacoes[-1] is not None
    assert PERGUNTA_1 in recuperacoes[-1]


def test_acompanhamento_sem_indicador_nomeado_mantem_o_assunto(
    client,
    make_org,
    use_fake,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """ "E por que isso aconteceu?" não nomeia indicador — o assunto vem do turno anterior."""
    provider, _ = use_fake
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))

    t1 = _perguntar(client, headers, ente=cenario_com_rcl.ente, pergunta=PERGUNTA_1).json()
    _perguntar(client, headers, pergunta=ACOMPANHAMENTO, conversa_id=t1["conversa_id"])

    fatos_do_acompanhamento = {f.codigo for f in provider.calls[-1].fatos}
    assert (
        "rcl" in fatos_do_acompanhamento
    ), "o acompanhamento recuperou o indicador do turno anterior, não uma visão genérica"


@pytest.mark.parametrize(
    ("pagina", "esperados"),
    [
        ("receita", {"rcl"}),
        ("benchmarking", {"rcl_per_capita"}),
        ("previsoes", {"rcl", "pessoal_executivo", "divida_consolidada_liquida"}),
        ("cockpit", {"pessoal_executivo", "saude_minimo", "educacao_mde"}),
        ("saude-educacao", {"saude_minimo", "educacao_mde", "fundeb_profissionais"}),
    ],
)
def test_contexto_da_pagina_aceita_chave_com_ou_sem_barra(pagina: str, esperados: set[str]) -> None:
    sem_barra = retriever.indicadores_da_pagina(pagina)
    assert sem_barra == retriever.indicadores_da_pagina(f"/{pagina}/")
    assert esperados <= sem_barra


def test_indicador_da_tela_fora_do_pacote_executivo_vai_para_ferramenta(
    client,
    make_org,
    use_fake,
    cenario_com_rcl: Cenario,
    monkeypatch,
) -> None:
    pedidos: list[str] = []

    def capturar(*args, **kwargs):
        pedidos.extend(kwargs["codigos"])
        return [], [], []

    monkeypatch.setattr(retriever, "fatos_por_ferramenta", capturar)
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))
    resposta = _perguntar(
        client,
        headers,
        ente=cenario_com_rcl.ente,
        pagina="caixa",
        pergunta="Como interpretar esta tela?",
    )
    assert resposta.status_code == 200, resposta.text
    assert "disponibilidade" in pedidos


def test_teto_de_turnos_limita_o_contexto(
    client,
    make_org,
    use_fake,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """Conversa longa não vira dossiê: o contexto para no teto declarado."""
    provider, _ = use_fake
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))

    resposta = _perguntar(client, headers, ente=cenario_com_rcl.ente, pergunta=PERGUNTA_1).json()
    for indice in range(MAX_TURNOS_CONTEXTO + 2):
        resposta = _perguntar(
            client,
            headers,
            pergunta=f"E o que isso implica, parte {indice}?",
            conversa_id=resposta["conversa_id"],
        ).json()

    assert resposta["turnos_no_contexto"] == MAX_TURNOS_CONTEXTO
    assert len(provider.calls[-1].historico) == MAX_TURNOS_CONTEXTO


# --------------------------------------------------------------------------- #
# 2. Isolamento — o histórico não é passe livre
# --------------------------------------------------------------------------- #
def test_conversa_de_outra_organizacao_nao_e_recuperada(
    client,
    make_org,
    use_fake,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """``conversa_id`` de outro tenant é 404: nem histórico, nem confirmação de existência."""
    org_a = make_org(entes=[cenario_com_rcl.ente])
    org_b = make_org(entes=[cenario_com_rcl.ente])
    headers_a = auth_header(login(client, org_a.email, org_a.senha))
    headers_b = auth_header(login(client, org_b.email, org_b.senha))

    t1 = _perguntar(client, headers_a, ente=cenario_com_rcl.ente, pergunta=PERGUNTA_1).json()

    invasao = _perguntar(client, headers_b, pergunta=ACOMPANHAMENTO, conversa_id=t1["conversa_id"])
    assert invasao.status_code == 404, invasao.text
    assert "conversa" in invasao.json()["title"].lower()


def test_listagem_nao_expoe_ente_fora_do_membership_ou_da_licenca(
    client,
    make_org,
    use_fake,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """GET /conversas aplica o mesmo escopo efetivo do drill por ente."""
    org = make_org(entes=[cenario_com_rcl.ente, "1100015"])
    headers = auth_header(login(client, org.email, org.senha))
    turno = _perguntar(client, headers, ente=cenario_com_rcl.ente, pergunta=PERGUNTA_1).json()

    with admin_session() as s:
        tenancy_repo.set_membership_escopo(
            s, membership_id=org.membership_id, cods_ibge=["1100015"]
        )
    restrito = client.get("/assistant/conversas", headers=headers)
    assert restrito.status_code == 200, restrito.text
    assert turno["conversa_id"] not in {i["id"] for i in restrito.json()["itens"]}

    # Registros legados sem classificação de ente também fecham, em vez de virarem
    # conteúdo org-wide para qualquer membro.
    with admin_session() as s:
        conversa = s.scalar(select(Conversa).where(Conversa.id == turno["conversa_id"]))
        assert conversa is not None
        conversa.cod_ibge = None
    sem_classificacao = client.get("/assistant/conversas", headers=headers)
    assert sem_classificacao.status_code == 200, sem_classificacao.text
    assert turno["conversa_id"] not in {i["id"] for i in sem_classificacao.json()["itens"]}
    retomada_sem_vazamento = _perguntar(
        client,
        headers,
        ente="1100015",
        pergunta="O que significa Receita Corrente Liquida?",
        conversa_id=turno["conversa_id"],
    )
    assert retomada_sem_vazamento.status_code == 200, retomada_sem_vazamento.text
    assert retomada_sem_vazamento.json()["turnos_no_contexto"] == 0
    assert retomada_sem_vazamento.json()["turnos_descartados"] == 1

    with admin_session() as s:
        conversa = s.scalar(select(Conversa).where(Conversa.id == turno["conversa_id"]))
        assert conversa is not None
        conversa.cod_ibge = cenario_com_rcl.ente
        s.execute(
            delete(MembershipEscopo).where(MembershipEscopo.membership_id == org.membership_id)
        )
        licenca = s.scalar(
            select(Licenca).where(
                Licenca.org_id == org.org_id,
                Licenca.cod_ibge == cenario_com_rcl.ente,
            )
        )
        assert licenca is not None
        licenca.status = "suspensa"
    sem_licenca = client.get("/assistant/conversas", headers=headers)
    assert sem_licenca.status_code == 200, sem_licenca.text
    assert turno["conversa_id"] not in {i["id"] for i in sem_licenca.json()["itens"]}


def test_turno_de_ente_fora_do_escopo_e_descartado_do_contexto(
    client,
    make_org,
    use_fake,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """O ente saiu do escopo entre um turno e outro: o turno some do contexto, e a conversa segue.

    É o modo de falha que a ficha nomeia — "multi-turno vira vetor de vazamento se o
    histórico atravessar ente fora de escopo". A revalidação acontece **a cada turno**,
    porque escopo é estado de agora, não de quando a conversa começou.
    """
    provider, _ = use_fake
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))

    t1 = _perguntar(client, headers, ente=cenario_com_rcl.ente, pergunta=PERGUNTA_1).json()

    # O usuário perde o município: escopo do membership restrito a outro ente qualquer.
    with admin_session() as s:
        tenancy_repo.set_membership_escopo(
            s, membership_id=org.membership_id, cods_ibge=["1100015"]
        )

    segundo = _perguntar(client, headers, pergunta=ACOMPANHAMENTO, conversa_id=t1["conversa_id"])
    # Sem ente herdável (o do turno anterior saiu do escopo) e sem ente no corpo: 400 claro.
    assert segundo.status_code == 400, segundo.text
    assert "ente" in segundo.json()["title"].lower()

    # Devolvendo o escopo, a mesma pergunta volta a funcionar — a recusa era de escopo.
    with admin_session() as s:
        s.execute(
            delete(MembershipEscopo).where(MembershipEscopo.membership_id == org.membership_id)
        )
    terceiro = _perguntar(client, headers, pergunta=ACOMPANHAMENTO, conversa_id=t1["conversa_id"])
    assert terceiro.status_code == 200, terceiro.text
    assert terceiro.json()["turnos_no_contexto"] == 1


def test_pergunta_sem_ente_e_sem_conversa_e_recusada_na_borda(client, make_org) -> None:
    """Sem ``conversa_id``, o ente continua obrigatório — o contrato antigo não afrouxou."""
    org = make_org(entes=["2304400"])
    headers = auth_header(login(client, org.email, org.senha))
    resp = _perguntar(client, headers, pergunta="E agora?")
    assert resp.status_code == 422, resp.text


def test_continuacao_informa_quantos_ancestrais_foram_descartados(
    client,
    make_org,
    use_fake,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """Um ancestral proibido e removido sem ocultar isso nem bloquear a ancora permitida."""
    outro_ente = "1100015"
    org = make_org(entes=[cenario_com_rcl.ente, outro_ente])
    headers = auth_header(login(client, org.email, org.senha))
    t1 = _perguntar(client, headers, ente=cenario_com_rcl.ente, pergunta=PERGUNTA_1).json()
    t2 = _perguntar(
        client,
        headers,
        ente=outro_ente,
        pergunta="O que significa Receita Corrente Liquida?",
        conversa_id=t1["conversa_id"],
    )
    assert t2.status_code == 200, t2.text

    with admin_session() as s:
        tenancy_repo.set_membership_escopo(
            s, membership_id=org.membership_id, cods_ibge=[outro_ente]
        )

    t3 = _perguntar(
        client,
        headers,
        pergunta="E o que isso implica?",
        conversa_id=t2.json()["conversa_id"],
    )
    assert t3.status_code == 200, t3.text
    assert t3.json()["turnos_no_contexto"] == 1
    assert t3.json()["turnos_descartados"] == 1


# --------------------------------------------------------------------------- #
# 3. G6 continua valendo — e o lastro da própria conversa evita o falso positivo
# --------------------------------------------------------------------------- #
def test_valor_do_turno_anterior_tem_lastro_e_nao_e_sinalizado(
    client,
    make_org,
    cenario_com_rcl: Cenario,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reafirmar um número **que a plataforma entregou nesta conversa** não é alucinação.

    O fato corrente é ocultado entre os turnos, mas a fonte é rederivada. Assim o segundo
    turno só consegue sustentar o valor pelo fato gravado no primeiro **e** demonstra a
    condição nova: o histórico só entra no G6 quando a consulta atual confirma a mesma
    procedência. Se o lastro da conversa não contasse, o G6 acusaria justamente a
    continuidade — e o gestor leria um aviso de número sem fonte sobre o número mais bem
    fundamentado da tela.
    """
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))

    provider = FakeProvider()
    app.dependency_overrides[get_llm_provider] = lambda: provider
    try:
        t1 = _perguntar(client, headers, ente=cenario_com_rcl.ente, pergunta=PERGUNTA_1).json()
        rcl = next(f for f in t1["fatos"] if f["codigo"] == "rcl")
        assert rcl["disponivel"] is True
        valor_formatado = rcl["valor_formatado"]

        # A consulta atual ainda rederiva a fonte da entrega, mas não entrega o fato ao
        # provedor. Isso evita que o próprio contexto corrente esconda se o G6 realmente
        # está usando o lastro histórico permitido.
        build_context_original = retriever.build_context

        def contexto_sem_fatos(*args, **kwargs):
            contexto = build_context_original(*args, **kwargs)
            contexto.fatos = []
            return contexto

        monkeypatch.setattr(retriever, "build_context", contexto_sem_fatos)

        repetidor = ProvedorQueRepeteValor(
            "Retomando o que já foi apurado nesta conversa: a Receita Corrente Líquida "
            f"informada era de {valor_formatado}."
        )
        app.dependency_overrides[get_llm_provider] = lambda: repetidor
        segundo = _perguntar(
            client, headers, pergunta=ACOMPANHAMENTO, conversa_id=t1["conversa_id"]
        )
        assert segundo.status_code == 200, segundo.text
        corpo = segundo.json()
        assert corpo["verificacao"]["total_citados"] >= 1
        assert corpo["verificacao"]["status"] == "ok", corpo["verificacao"]["sem_lastro"]
        assert rcl["source_ref"] in corpo["source_refs"]
        assert any(
            fonte["tipo"] == "indicador_historico" and fonte["source_ref"] == rcl["source_ref"]
            for fonte in corpo["fontes"]
        )
        assert "Verificação automática (G6)" not in corpo["resposta"]
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


def test_retificacao_so_reaproveita_lastro_historico_na_mesma_fotografia(
    client,
    make_org,
    cenario_com_rcl: Cenario,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G6 aceita V1 herdada, mas não deixa V1 lastrear uma consulta que rederivou V2."""
    antes_da_retificacao = datetime(2024, 2, 1, tzinfo=UTC)
    depois_da_retificacao = datetime(2024, 4, 1, tzinfo=UTC)
    versao_nova = "ia7-retificada-v2"
    with admin_session() as s:
        entrega_antiga = s.scalar(
            select(DimEntrega).where(
                DimEntrega.cod_ibge == cenario_com_rcl.ente,
                DimEntrega.relatorio == "RREO",
                DimEntrega.periodo == PERIODO,
            )
        )
        assert entrega_antiga is not None
        entrega_antiga.vigente = False
        s.add(
            DimEntrega(
                cod_ibge=cenario_com_rcl.ente,
                relatorio="RREO",
                periodo=PERIODO,
                versao_entrega=versao_nova,
                homologada_em=datetime(2024, 3, 1, tzinfo=UTC),
                vigente=True,
                hash_payload="ia7-retificacao",
            )
        )
        s.add(
            FatoRcl(
                cod_ibge=cenario_com_rcl.ente,
                periodo_ref=PERIODO,
                rcl_12m=Decimal("620000000"),
                receita_corrente=Decimal("680000000"),
                deducoes=Decimal("60000000"),
                versao_entrega=versao_nova,
                memoria={"fonte": "retificação IA-7"},
            )
        )

    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))
    inicial = FakeProvider()
    app.dependency_overrides[get_llm_provider] = lambda: inicial
    try:
        t1_resp = _perguntar(
            client,
            headers,
            ente=cenario_com_rcl.ente,
            pergunta=PERGUNTA_1,
            as_of=antes_da_retificacao.isoformat(),
        )
        assert t1_resp.status_code == 200, t1_resp.text
        t1 = t1_resp.json()
        rcl_antiga = next(f for f in t1["fatos"] if f["codigo"] == "rcl")
        assert rcl_antiga["source_ref"]["versao_entrega"] != versao_nova

        repetidor = ProvedorQueRepeteValor(
            f"Na fotografia anterior, a Receita Corrente Líquida era de "
            f"{rcl_antiga['valor_formatado']}."
        )
        app.dependency_overrides[get_llm_provider] = lambda: repetidor

        # Não passamos o fato novamente ao provedor; a fonte V1 continua sendo calculada
        # pela consulta herdada, e por isso o lastro do ancestral é legítimo.
        build_context_original = retriever.build_context

        def contexto_sem_fatos(*args, **kwargs):
            contexto = build_context_original(*args, **kwargs)
            contexto.fatos = []
            return contexto

        monkeypatch.setattr(retriever, "build_context", contexto_sem_fatos)
        herdada_resp = _perguntar(
            client, headers, pergunta=ACOMPANHAMENTO, conversa_id=t1["conversa_id"]
        )
        assert herdada_resp.status_code == 200, herdada_resp.text
        herdada = herdada_resp.json()
        assert herdada["verificacao"]["status"] == "ok", herdada["verificacao"]
        assert any(
            fonte["tipo"] == "indicador_historico"
            and fonte["source_ref"] == rcl_antiga["source_ref"]
            for fonte in herdada["fontes"]
        )

        # Uma escolha explícita de corte após a retificação rederiva V2. O número de V1
        # continua auditável no ancestral, mas já não pode passar pelo G6 desta resposta.
        monkeypatch.setattr(retriever, "build_context", build_context_original)
        atual_resp = _perguntar(
            client,
            headers,
            pergunta=ACOMPANHAMENTO,
            conversa_id=t1["conversa_id"],
            as_of=depois_da_retificacao.isoformat(),
        )
        assert atual_resp.status_code == 200, atual_resp.text
        atual = atual_resp.json()
        assert atual["verificacao"]["status"] == "sinalizado"
        assert not any(fonte["tipo"] == "indicador_historico" for fonte in atual["fontes"])
        assert any(
            ref.get("versao_entrega") == versao_nova for ref in atual["source_refs"]
        )
        assert not any(
            ref.get("versao_entrega") == rcl_antiga["source_ref"]["versao_entrega"]
            for ref in atual["source_refs"]
        )
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


def test_troca_de_ente_herda_as_of_sem_herdar_periodo_ou_lastro(
    client,
    make_org,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """Um fio pode comparar entes, mas não mistura fatos nem séries entre eles."""
    outro_ente = f"8{cenario_com_rcl.ente[1:]}"
    periodo_mais_recente = "2092-B6"
    fotografia = datetime(2025, 1, 10, 12, 30, tzinfo=UTC)
    with admin_session() as s:
        s.add(
            DimEnte(
                cod_ibge=outro_ente,
                nome="Município Comparado",
                esfera="municipal",
                uf="CE",
                regiao="Nordeste",
                populacao=130_000,
                pib=Decimal("1600000000"),
                rpps=False,
                possui_tcm=False,
            )
        )
        valores = (
            (PERIODO, Decimal("610000000")),
            (periodo_mais_recente, Decimal("630000000")),
        )
        for periodo, valor in valores:
            s.add(
                DimEntrega(
                    cod_ibge=outro_ente,
                    relatorio="RREO",
                    periodo=periodo,
                    # Igual à versão de A de propósito: a fonte não pode ser a única
                    # barreira contra o vazamento entre entes.
                    versao_entrega="assist-v1",
                    homologada_em=datetime(2024, 1, 20, tzinfo=UTC),
                    vigente=True,
                    hash_payload=f"ia7-{outro_ente}-{periodo}",
                )
            )
            s.add(
                FatoRcl(
                    cod_ibge=outro_ente,
                    periodo_ref=periodo,
                    rcl_12m=valor,
                    receita_corrente=valor + Decimal("60000000"),
                    deducoes=Decimal("60000000"),
                    versao_entrega="assist-v1",
                    memoria={"fonte": "cenário de troca de ente"},
                )
            )

    org = make_org(entes=[cenario_com_rcl.ente, outro_ente])
    headers = auth_header(login(client, org.email, org.senha))
    inicial = FakeProvider()
    app.dependency_overrides[get_llm_provider] = lambda: inicial
    try:
        t1_resp = _perguntar(
            client,
            headers,
            ente=cenario_com_rcl.ente,
            pergunta=PERGUNTA_1,
            as_of=fotografia.isoformat(),
        )
        assert t1_resp.status_code == 200, t1_resp.text
        t1 = t1_resp.json()
        rcl_do_primeiro_ente = next(f for f in t1["fatos"] if f["codigo"] == "rcl")

        app.dependency_overrides[get_llm_provider] = lambda: ProvedorQueRepeteValor(
            f"O valor anterior, que não deve migrar de ente, foi "
            f"{rcl_do_primeiro_ente['valor_formatado']}."
        )
        troca_resp = _perguntar(
            client,
            headers,
            ente=outro_ente,
            periodo=PERIODO,
            pergunta=ACOMPANHAMENTO,
            conversa_id=t1["conversa_id"],
        )
        assert troca_resp.status_code == 200, troca_resp.text
        troca = troca_resp.json()
        assert troca["ente"] == outro_ente
        assert troca["periodo"] == PERIODO
        assert datetime.fromisoformat(troca["as_of"].replace("Z", "+00:00")) == fotografia
        assert troca["verificacao"]["status"] == "sinalizado"
        assert not any(fonte["tipo"] == "indicador_historico" for fonte in troca["fontes"])

        # Sem período no segundo ente, a resolução parte da série de B (2092-B6), não
        # do período 2091-B6 herdado de A. A fotografia continua a mesma do fio.
        periodo_local_resp = _perguntar(
            client,
            headers,
            ente=outro_ente,
            pergunta="Qual é a Receita Corrente Líquida deste ente?",
            conversa_id=t1["conversa_id"],
        )
        assert periodo_local_resp.status_code == 200, periodo_local_resp.text
        periodo_local = periodo_local_resp.json()
        assert periodo_local["periodo"] == periodo_mais_recente
        assert datetime.fromisoformat(periodo_local["as_of"].replace("Z", "+00:00")) == fotografia
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)
        with admin_session() as s:
            s.execute(delete(FatoRcl).where(FatoRcl.cod_ibge == outro_ente))
            s.execute(delete(DimEntrega).where(DimEntrega.cod_ibge == outro_ente))
            s.execute(delete(DimEnte).where(DimEnte.cod_ibge == outro_ente))


def test_tool_calls_da_pergunta_recebem_o_conversa_id_final(
    client,
    make_org,
    use_fake,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """O elo G7 e fechado mesmo quando a ferramenta roda antes do INSERT da conversa."""
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))

    resposta = _perguntar(
        client,
        headers,
        ente=cenario_com_rcl.ente,
        pergunta="Qual e o percentual de garantias?",
    )
    assert resposta.status_code == 200, resposta.text
    conversa_id = resposta.json()["conversa_id"]

    with admin_session() as s:
        chamadas = list(
            s.scalars(
                select(IaToolCall).where(
                    IaToolCall.org_id == org.org_id,
                    IaToolCall.origem == "assistente",
                )
            )
        )
    assert chamadas, "a pergunta nomeada deve passar pela ferramenta canonica"
    assert {str(chamada.conversa_id) for chamada in chamadas} == {conversa_id}


def test_numero_sem_lastro_continua_sinalizado_mesmo_em_conversa(
    client,
    make_org,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """O contraponto do teste anterior: o histórico não vira desculpa para inventar."""
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))

    provider = FakeProvider()
    app.dependency_overrides[get_llm_provider] = lambda: provider
    try:
        t1 = _perguntar(client, headers, ente=cenario_com_rcl.ente, pergunta=PERGUNTA_1).json()
        inventor = ProvedorQueRepeteValor(
            "A Receita Corrente Líquida do exercício seguinte será de R$ 777.777.777,77."
        )
        app.dependency_overrides[get_llm_provider] = lambda: inventor
        corpo = _perguntar(
            client, headers, pergunta=ACOMPANHAMENTO, conversa_id=t1["conversa_id"]
        ).json()
        assert corpo["verificacao"]["status"] == "sinalizado"
        assert "777.777.777,77" in corpo["verificacao"]["sem_lastro"]
        assert "Verificação automática (G6)" in corpo["resposta"]
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


# --------------------------------------------------------------------------- #
# 4. O prompt: nenhuma regra saiu, e a redação entrou
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "trecho",
    [
        "NUNCA use números de memória",
        "Cite a fonte de cada número",
        "calculado dos dados do ente",
        "não estime — sinalize a lacuna",
        "esfera (municipal/estadual)",
        "DICIONÁRIO DA PLATAFORMA",
        "Não emita parecer jurídico",
    ],
)
def test_prompt_preserva_as_regras_inviolaveis(trecho: str) -> None:
    """A tese da sprint em forma de teste: ganhar tom não custa nenhuma garantia."""
    assert trecho in SYSTEM_PROMPT


@pytest.mark.parametrize(
    "trecho",
    [
        "Comece pelo significado",
        "Expanda toda sigla",
        "o que o gestor pode fazer",
        "português comum",
    ],
)
def test_prompt_ganhou_instrucao_de_redacao(trecho: str) -> None:
    assert trecho in SYSTEM_PROMPT


def test_prompt_proibe_usar_a_redacao_para_acrescentar_dado() -> None:
    """O corolário incômodo da ficha, escrito no próprio prompt."""
    assert "nada nesta seção autoriza acrescentar número" in SYSTEM_PROMPT


def test_prompt_exige_valor_literal_sem_segunda_escala() -> None:
    """Arredondar R$ 812 milhões para ``812,3 milhões`` cria outro número para o G6."""
    assert "uma única vez, sem arredondar" in SYSTEM_PROMPT
    assert "milhares/milhões/bilhões" in SYSTEM_PROMPT

    request = LLMRequest(system=SYSTEM_PROMPT, pergunta="Qual é a RCL?")
    corpo = llm.montar_prompt(request)
    assert "Copie cada valor calculado exatamente como fornecido" in corpo
    assert "não o converta para milhares, milhões ou bilhões" in corpo
    assert not hasattr(request, "temperatura")


# --------------------------------------------------------------------------- #
# 5. Legibilidade — a régua objetiva (sigla, rótulo, significado antes do número)
# --------------------------------------------------------------------------- #
def test_resposta_telegrafica_reprova_na_regua_de_legibilidade() -> None:
    """O texto que a sprint existe para não produzir mais."""
    laudo = didatica.avaliar("Pessoal: 51,77%. Fonte: RGF.")
    assert laudo.ok is False
    assert laudo.explica_antes_do_numero is False
    assert "RGF" in laudo.siglas_sem_expansao


def test_resposta_didatica_aprova() -> None:
    laudo = didatica.avaliar(
        "A despesa com pessoal mede quanto da receita do município é consumida pela folha "
        "de pagamento; a LRF (Lei de Responsabilidade Fiscal) fixa um teto para ela. "
        "Pessoal do Executivo: 51,77% da RCL (Receita Corrente Líquida), apurado no RGF "
        "(Relatório de Gestão Fiscal). Está na faixa prudencial, o que já exige medidas."
    )
    assert laudo.ok is True, laudo.falhas


def test_expansao_de_sigla_e_idempotente_e_so_na_primeira_ocorrencia() -> None:
    texto = "A RCL cresceu. A RCL do ano anterior era menor."
    uma_vez = didatica.expandir_siglas(texto)
    assert uma_vez.count("Receita Corrente Líquida") == 1
    assert didatica.expandir_siglas(uma_vez) == uma_vez


def test_expansao_oficial_do_fundeb_e_reconhecida() -> None:
    texto = (
        "O Fundo de Manutenção e Desenvolvimento da Educação Básica e de Valorização dos "
        "Profissionais da Educação (FUNDEB) financia a educação básica."
    )
    assert didatica.siglas_sem_expansao(texto) == []


def test_expansao_com_qualificador_antes_da_sigla_e_reconhecida() -> None:
    texto = (
        "A Receita Corrente Líquida por habitante (RCL por habitante) mede a capacidade "
        "financeira por cidadão."
    )
    assert didatica.siglas_sem_expansao(texto) == []


# --------------------------------------------------------------------------- #
# 6. Resposta cortada pelo teto de saída — declarada, nunca entregue como completa
# --------------------------------------------------------------------------- #
class _Candidato:
    def __init__(self, motivo: str) -> None:
        self.finish_reason = motivo


class _RespostaFalsa:
    def __init__(self, motivo: str) -> None:
        self.candidates = [_Candidato(motivo)]


def test_corte_por_limite_de_saida_e_declarado() -> None:
    """Achado da IA-7 em produção: resposta terminando no meio da frase, em silêncio.

    Com a ordem didática (significado antes do número), o corte é pior do que era: o
    gestor lê a explicação inteira e nunca chega ao valor. O teto subiu, e o que o teto
    não resolver passa a vir declarado.
    """
    assert llm.foi_truncada(_RespostaFalsa("FinishReason.MAX_TOKENS")) is True
    assert llm.foi_truncada(_RespostaFalsa("FinishReason.STOP")) is False

    cortado = llm.declarar_corte("A RCL do ente é", _RespostaFalsa("MAX_TOKENS"))
    assert llm.AVISO_TRUNCADA in cortado
    assert cortado.startswith("A RCL do ente é")

    inteiro = llm.declarar_corte("Resposta completa.", _RespostaFalsa("STOP"))
    assert inteiro == "Resposta completa."


#: Maior saída medida na primeira corrida paga do conjunto dourado contra o
#: `gemini-3.5-flash` (74 perguntas + 12 adversárias). É o número que o teto tem de
#: acomodar com folga — não com 6% de sobra, que foi o que o teto anterior deixava.
MAIOR_SAIDA_MEDIDA = 5803


def test_teto_de_saida_acomoda_raciocinio_e_prosa_didatica() -> None:
    """O teto é justificado por medição, e o teste guarda a medição — não a constante.

    Duas vezes seguidas o mesmo modo de falha apareceu aqui: teto apertado, o modelo
    termina em ``MAX_TOKENS`` e a plataforma entrega texto cortado no meio da frase. Na
    primeira, uma pergunta simples gastou 1.443 tokens de raciocínio + 553 de resposta
    (97% do teto de 2.048). Na segunda, já com 6.144, a corrida ao vivo mediu máximo de
    5.803 no conjunto (94%) e truncou três respostas adversárias.

    Por isso a asserção é sobre a **folga**, não sobre o número: um teto que apenas cabe
    no pior caso já medido é um teto que vai estourar no próximo caso pior.
    """
    teto = LLMRequest(system="s", pergunta="p").max_tokens
    assert teto is not None
    assert teto >= 2 * MAIOR_SAIDA_MEDIDA, (
        f"teto {teto} deixa menos que o dobro do maior caso medido "
        f"({MAIOR_SAIDA_MEDIDA}); foi assim que 2.048 e 6.144 estouraram"
    )


def test_provedor_local_responde_de_forma_legivel(
    client,
    make_org,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """O provedor determinístico — o que a avaliação da IA-6 executa — passa na régua.

    Sem isto, a métrica de legibilidade mediria um provedor que a suíte não roda, e a
    afirmação "a resposta explica antes do número" não teria prova offline nenhuma.
    """
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))
    corpo = _perguntar(client, headers, ente=cenario_com_rcl.ente, pergunta=PERGUNTA_1).json()

    laudo = didatica.avaliar(corpo["resposta"])
    assert laudo.ok is True, f"{laudo.falhas}\n---\n{corpo['resposta']}"
    assert corpo["verificacao"]["status"] == "ok"


# --------------------------------------------------------------------------- #
# 5. O corte bitemporal só se herda quando foi PEDIDO
#
# ``op.conversa.as_of`` é gravado sempre — inclusive numa leitura corrente, porque a
# auditoria precisa saber qual fotografia respondeu. Herdar essa coluna como se ela
# significasse "o gestor pediu histórico" fazia toda continuação virar reprodução
# histórica presa ao relógio do primeiro turno: do segundo turno em diante o contexto
# nascia ``as_of_fixo``, e ``cobertura_do_ente`` — cujo mart só representa o estado
# corrente — passava a recusar *fail-closed* uma pergunta perfeitamente corrente.
#
# Os dois testes abaixo andam em par de propósito: o primeiro prova que a continuação
# corrente não vira histórico, o segundo é o controle negativo que prova que a asserção
# do primeiro **detecta** — quando o corte é de fato pedido, ele continua sendo herdado.
# --------------------------------------------------------------------------- #
class ProvedorQueChamaCobertura:
    """Pede a cobertura com ``as_of`` — o argumento que o serviço decide manter ou tirar."""

    name = "chama-cobertura"

    def __init__(self) -> None:
        self.argumentos: dict | None = None

    def chat(self, request: LLMRequest) -> LLMResult:  # pragma: no cover - ramo inválido
        raise AssertionError("provedor com function calling não deve cair em chat()")

    def chat_com_ferramentas(
        self,
        request: LLMRequest,
        ferramentas: list[ToolSpec],
        executar: ExecutorFerramenta,
    ) -> LLMResult:
        executar("cobertura_do_ente", {"as_of": "2020-01-01T00:00:00+00:00"})
        return LLMResult(texto="ok", modelo=self.name, tokens_entrada=1, tokens_saida=1)


def _cobertura_do_segundo_turno(
    client,
    headers,
    monkeypatch: pytest.MonkeyPatch,
    *,
    ente: str,
    as_of_no_primeiro_turno: str | None,
) -> dict:
    """Roda dois turnos de verdade e devolve os argumentos com que a cobertura foi chamada."""
    primeiro = _perguntar(
        client,
        headers,
        ente=ente,
        pergunta=PERGUNTA_1,
        **({"as_of": as_of_no_primeiro_turno} if as_of_no_primeiro_turno else {}),
    )
    assert primeiro.status_code == 200, primeiro.text

    capturados: dict = {}

    def invoke_falso(_ctx: object, _reg: object, nome: str, argumentos: dict) -> SimpleNamespace:
        assert nome == "cobertura_do_ente"
        capturados.update(argumentos)
        return SimpleNamespace(payload={"cobertura": []}, source_refs=[])

    monkeypatch.setattr(assistant_service.tooling, "invoke", invoke_falso)
    app.dependency_overrides[get_llm_provider] = ProvedorQueChamaCobertura
    try:
        segundo = _perguntar(
            client,
            headers,
            pergunta=ACOMPANHAMENTO,
            conversa_id=primeiro.json()["conversa_id"],
        )
        assert segundo.status_code == 200, segundo.text
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)
    return capturados


def test_continuacao_de_leitura_corrente_nao_vira_reproducao_historica(
    client,
    make_org,
    use_fake,
    monkeypatch: pytest.MonkeyPatch,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """Sem corte pedido, o segundo turno segue corrente — e a cobertura continua chamável."""
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))

    capturados = _cobertura_do_segundo_turno(
        client,
        headers,
        monkeypatch,
        ente=cenario_com_rcl.ente,
        as_of_no_primeiro_turno=None,
    )

    # Nem o corte inventado pelo modelo, nem o instante resolvido do turno 1: a cobertura
    # é um mart do estado corrente e não deve receber recorte nenhum numa leitura corrente.
    assert "as_of" not in capturados, capturados

    # E a explicitude fica registrada como o que é: ninguém pediu fotografia em turno algum.
    with admin_session() as s:
        linhas = list(
            s.scalars(
                select(Conversa)
                .where(Conversa.org_id == org.org_id)
                .order_by(Conversa.criado_em)
            )
        )
    assert len(linhas) == 2
    assert [linha.as_of_explicito for linha in linhas] == [False, False]
    # ``as_of`` continua preenchido nas duas: é a fotografia que de fato respondeu.
    assert all(linha.as_of is not None for linha in linhas)


def test_continuacao_de_corte_pedido_permanece_historica(
    client,
    make_org,
    use_fake,
    monkeypatch: pytest.MonkeyPatch,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    """Controle negativo: com corte pedido, o segundo turno herda — e a cobertura recusa."""
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))
    fotografia = datetime(2025, 1, 10, 12, 30, tzinfo=UTC)

    capturados = _cobertura_do_segundo_turno(
        client,
        headers,
        monkeypatch,
        ente=cenario_com_rcl.ente,
        as_of_no_primeiro_turno=fotografia.isoformat(),
    )

    # Aqui o corte SOBREVIVE ao segundo turno — e é o corte do gestor, não o do modelo.
    assert capturados.get("as_of") == fotografia

    with admin_session() as s:
        linhas = list(
            s.scalars(
                select(Conversa)
                .where(Conversa.org_id == org.org_id)
                .order_by(Conversa.criado_em)
            )
        )
    assert [linha.as_of_explicito for linha in linhas] == [True, True]


# --------------------------------------------------------------------------- #
# 6. O fecho didático é do pipeline, não do provedor
#
# A primeira corrida paga contra o Gemini achou o que a corrida local não podia achar: o
# provedor local expandia siglas e acrescentava a ressalva do §9, o caminho do Gemini não
# fazia nem uma coisa nem outra — e, como a suíte só rodava no provedor local, os dois
# buracos eram invisíveis. Legibilidade 100% offline × 91,9% ao vivo (seis recusas
# escrevendo "RREO" sem expandir) e três respostas adversárias sem a ressalva.
#
# É a lição da A22/E1 outra vez: a garantia mora dentro da ferramenta, não na borda que a
# chama — só que aqui a "borda" era um provedor inteiro. Uma regra de prompt PEDE; um
# passo de pipeline GARANTE. Por isso o teste usa um provedor que ignora o prompt de
# propósito: é a única forma de provar que a garantia não depende de o modelo obedecer.
# --------------------------------------------------------------------------- #
class ProvedorDesobediente:
    """Devolve texto cru, com sigla e sem ressalva — exatamente o que o prompt proíbe."""

    name = "desobediente"

    def chat(self, request: LLMRequest) -> LLMResult:
        return LLMResult(
            texto="O RREO do período mostra a RCL apurada.",
            modelo=self.name,
            tokens_entrada=1,
            tokens_saida=1,
        )


def test_sigla_e_ressalva_sao_garantidas_mesmo_com_provedor_que_ignora_o_prompt(
    client,
    make_org,
    cenario_com_rcl: Cenario,  # noqa: F811
) -> None:
    org = make_org(entes=[cenario_com_rcl.ente])
    headers = auth_header(login(client, org.email, org.senha))
    app.dependency_overrides[get_llm_provider] = ProvedorDesobediente
    try:
        corpo = _perguntar(
            client, headers, ente=cenario_com_rcl.ente, pergunta=PERGUNTA_1
        ).json()
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)

    texto = corpo["resposta"]
    assert "Relatório Resumido da Execução Orçamentária" in texto, texto
    assert "Receita Corrente Líquida" in texto, texto
    assert "não constitui parecer jurídico" in texto, texto

    # E o que foi persistido é o que o gestor leu — histórico e auditoria não podem
    # mostrar uma versão do texto diferente da que saiu na tela.
    with admin_session() as s:
        linha = s.scalars(select(Conversa).where(Conversa.org_id == org.org_id)).one()
    assert linha.resposta == texto


def test_ressalva_nao_e_duplicada_quando_o_provedor_ja_a_escreveu() -> None:
    """Idempotência: o provedor local continua produzindo a sua, e ela não vira duas."""
    com_ressalva = (
        "A Receita Corrente Líquida (RCL) foi apurada. Esta resposta é informativa e "
        "fundamentada apenas nas fontes citadas; não constitui parecer jurídico ou "
        "contábil definitivo."
    )
    fechado = didatica.fechar_resposta(com_ressalva)
    assert fechado.lower().count("constitui parecer") == 1
    assert didatica.fechar_resposta(fechado) == fechado


# --------------------------------------------------------------------------- #
# 7. A faixa entrega o número, e ele vale nos três lugares
#
# Medido na corrida ao vivo: `requests_provedor == 1` em 43 das 74 perguntas — 58% das
# respostas nunca chamam ferramenta e saem direto do contexto do retriever. Esse contexto
# dizia `faixa="normal"` e nenhum limiar; a redação da IA-7 manda explicar a posição em
# relação ao limite, e o modelo preenchia a lacuna calculando (54% × 0,90 = 48,6%).
#
# O limiar precisa existir em três lugares ao mesmo tempo, e faltando um a correção não
# funciona: no **render** (o modelo só vê o que o render escreve), no **FatoResposta** (é
# dele que o G6 tira lastro) e no formato **pt-BR** (com ponto, "48.60%" é lido como
# separador de milhar e vira token solto "60%" — o G6 acusaria um número que tem lastro).
# --------------------------------------------------------------------------- #
def _fato_com_faixa(**extra) -> FatoResposta:
    campos: dict = {
        "codigo": "pessoal_executivo",
        "rotulo": "Pessoal do Executivo",
        "valor_formatado": "47,83%",
        "valor": "47.83",
        "unidade": "PERCENTUAL",
        "status": "calculado",
        "faixa": "normal",
        "teto_formatado": "54,00%",
        "alerta_formatado": "48,60%",
        "prudencial_formatado": "51,30%",
        "disponivel": True,
        "periodo": "2091-Q3",
        "source_ref": {
            "relatorio": "RGF",
            "anexo": "Anexo 01",
            "periodo": "2091-Q3",
            "versao_entrega": "aval-v2",
        },
    }
    campos.update(extra)
    return FatoResposta(**campos)


def test_render_escreve_o_numero_da_faixa_e_nao_so_o_rotulo() -> None:
    """`[normal]` diz que está normal e não diz normal até quanto."""
    fato = FatoContexto(
        codigo="pessoal_executivo",
        rotulo="Pessoal do Executivo",
        valor_formatado="47,83%",
        unidade="PERCENTUAL",
        status="calculado",
        disponivel=True,
        periodo="2091-Q3",
        source_ref={},
        faixa="normal",
        teto_formatado="54,00%",
        alerta_formatado="48,60%",
        prudencial_formatado="51,30%",
    )
    render = llm._fmt_faixa(fato)
    assert "48,60%" in render and "51,30%" in render and "54,00%" in render
    # Vírgula, não ponto: "48.60%" seria lido como separador de milhar em português.
    assert "48.60" not in render


def test_indicador_sem_limite_legal_nao_inventa_faixa() -> None:
    """Indicador gerencial não tem teto — e ausência é a resposta, nunca um zero."""
    fato = FatoContexto(
        codigo="rcl_per_capita",
        rotulo="RCL por habitante",
        valor_formatado="R$ 3.412,00",
        unidade="BRL",
        status="calculado",
        disponivel=True,
        periodo="2091-B6",
        source_ref={},
        faixa=None,
    )
    assert llm._fmt_faixa(fato) == ""


def test_faixa_citada_do_contexto_tem_lastro_sem_nenhuma_ferramenta() -> None:
    """O caso de adv-012: resposta com zero tool calls citando os três limiares."""
    texto = (
        "O percentual de pessoal do Executivo é de 47,83%, na faixa normal. O limite "
        "prudencial está em 51,30% e o de alerta em 48,60%, contra um teto de 54,00%."
    )
    fatos = [_fato_com_faixa().model_dump(mode="json")]
    laudo = verificacao.verificar(texto, [], fatos, [], [], [])
    assert laudo.tokens_sem_lastro() == []
    assert laudo.total_citados == 4


def test_ponto_como_separador_decimal_ainda_e_acusado() -> None:
    """Controle negativo: a correção é fornecer o formato certo, não afrouxar o G6.

    Se o modelo escrever "48.60%" mesmo assim, a verificação continua acusando — e deve.
    Em português o ponto separa milhar, então "48.60%" **é** um número diferente de 48,60%,
    e um verificador que aceitasse os dois deixaria de distinguir 1.000% de 1,000%.
    """
    texto = "o limite de alerta está em 48.60%"
    fatos = [_fato_com_faixa().model_dump(mode="json")]
    laudo = verificacao.verificar(texto, [], fatos, [], [], [])
    assert laudo.tokens_sem_lastro() != []
