"""Sprint IA-3 — servidor MCP: protocolo, credencial e **isolamento entre organizações**.

O teste que justifica o arquivo é o terceiro bloco. O servidor MCP é a primeira porta da
plataforma que um cliente externo alcança, e a pergunta que ela levanta não é "o protocolo
está correto?" — é "uma credencial da organização A consegue ler ente ou artefato da B?".

A matriz segue o padrão da Sprint E1 (``test_sprint_e1_isolamento.py``), com uma diferença
que o transporte impõe: JSON-RPC responde HTTP 200 mesmo quando a ferramenta recusa, então
o código que importa viaja **dentro** do payload (``status``/``erro``). As asserções são
sobre ele, e são de igualdade — ``in {403, 404}`` deixaria passar exatamente a regressão que
o teste existe para impedir.

A régua, por caso:

- **ente fora da carteira** ⇒ 403 ``scope-forbidden``. Não é 404 porque o dado fiscal do
  SICONFI é público e o ente existe para todo mundo; o que se nega é o acesso *daquela
  organização*, e essa distinção é o produto (A22/E1).
- **ente na carteira, fora da licença** ⇒ 403 ``ente-nao-licenciado``. Causa diferente,
  ação diferente: uma é cadastro do cliente, a outra é comercial.
- **artefato de outro tenant** (um alerta da organização vizinha) ⇒ **ausência**, nunca o
  dado. Aqui o vazamento seria de conteúdo do ``op``, não de dado público.

O intruso recebe **todas** as capacidades RBAC na própria organização, de propósito: assim
uma recusa só pode vir da fronteira entre tenants, nunca de permissão faltando.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.db import admin_session
from app.mcp_main import app as mcp_app
from app.modules.alerts.models import Alerta
from app.modules.assistant.models import IaToolCall
from app.modules.mcp import service as mcp_service
from app.modules.mcp.models import McpCredencial
from tests.conftest import auth_header, login

FORTALEZA = "2304400"
MARACANAU = "2307650"
PERIODO = "2024-B6"


@pytest.fixture
def mcp_client() -> TestClient:
    """Cliente do **processo do MCP** — outra aplicação ASGI, não a API.

    Usar ``app.mcp_main`` aqui é parte do teste: se o ``/mcp`` estivesse montado na API,
    este import falharia e a topologia da §7.3 teria sido silenciosamente abandonada.
    """
    with TestClient(mcp_app) as c:
        yield c


def emitir_credencial(client: TestClient, fx, *, nome: str = "Agente de teste") -> str:
    """Emite pela rota administrativa da API — o caminho real, com JWT e capacidade."""
    headers = auth_header(login(client, fx.email, fx.senha))
    papeis = client.get("/papeis", headers=headers)
    assert papeis.status_code == 200, papeis.text
    papel_id = papeis.json()[0]["id"]
    resposta = client.post(
        "/admin/mcp/credenciais",
        headers=headers,
        json={"nome": nome, "papel_id": papel_id},
    )
    assert resposta.status_code == 201, resposta.text
    token = resposta.json()["token"]
    assert token.startswith("mcp_")
    return token


def rpc(
    mcp_client: TestClient, token: str | None, metodo: str, params: dict | None = None,
    *, ident: int = 1,
):
    """Uma mensagem JSON-RPC no transporte real (HTTP + Bearer)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    corpo: dict = {"jsonrpc": "2.0", "id": ident, "method": metodo}
    if params is not None:
        corpo["params"] = params
    return mcp_client.post("/mcp", headers=headers, json=corpo)


def chamar_ferramenta(mcp_client: TestClient, token: str, nome: str, argumentos: dict) -> dict:
    """``tools/call`` → o payload estruturado (de sucesso ou de recusa)."""
    resposta = rpc(mcp_client, token, "tools/call", {"name": nome, "arguments": argumentos})
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert "result" in corpo, corpo
    return corpo["result"]


# --------------------------------------------------------------------------- #
# 1. Protocolo e descoberta
# --------------------------------------------------------------------------- #
def test_initialize_declara_ferramentas_e_recursos(client, mcp_client, make_org) -> None:
    """O aperto de mão do MCP, com as duas capacidades que este servidor realmente tem."""
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    corpo = rpc(mcp_client, token, "initialize").json()
    resultado = corpo["result"]
    assert resultado["protocolVersion"] == "2025-06-18"
    assert resultado["capabilities"]["tools"] == {"listChanged": False}
    assert resultado["capabilities"]["resources"] == {"listChanged": False}
    assert resultado["serverInfo"]["name"] == "plataforma-fiscal-mcp"


def test_tools_list_expoe_o_registro_inteiro_sem_curadoria(client, mcp_client, make_org) -> None:
    """As 14 ferramentas do registro, 1:1. Uma lista curada aqui seria regra na borda."""
    from app.shared import tooling

    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    ferramentas = rpc(mcp_client, token, "tools/list").json()["result"]["tools"]
    assert {f["name"] for f in ferramentas} == set(tooling.registro().nomes())
    assert len(ferramentas) == 14, "o catálogo da IA-1a/1b tem 14 ferramentas"
    for ferramenta in ferramentas:
        assert ferramenta["description"].strip()
        assert ferramenta["inputSchema"]["type"] == "object"


def test_resources_list_e_read_entregam_o_dicionario(client, mcp_client, make_org) -> None:
    """O dicionário da IA-2 é **recurso**: entra no contexto sem gastar chamada (§2.3)."""
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    recursos = rpc(mcp_client, token, "resources/list").json()["result"]["resources"]
    uris = {r["uri"] for r in recursos}
    assert uris == {
        "dicionario://indicadores", "dicionario://campos", "dicionario://juncoes"
    }
    leitura = rpc(
        mcp_client, token, "resources/read", {"uri": "dicionario://indicadores"}
    ).json()["result"]
    conteudo = leitura["contents"][0]
    assert conteudo["mimeType"] == "text/markdown"
    assert len(conteudo["text"]) > 200, "o dicionário subiu vazio (verifique o seed da IA-2)"


def test_nenhum_recurso_e_parametrizado_por_ente(client, mcp_client, make_org) -> None:
    """Recurso com ``{ente}`` seria dado de ente entrando sem gate de escopo."""
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    templates = rpc(mcp_client, token, "resources/templates/list").json()["result"]
    assert templates["resourceTemplates"] == []


def test_notificacao_nao_recebe_resposta(client, mcp_client, make_org) -> None:
    """Responder a uma notificação JSON-RPC é violação de protocolo."""
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    resposta = mcp_client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert resposta.status_code == 202
    assert not resposta.content or resposta.json() is None


def test_metodo_desconhecido_e_erro_de_protocolo(client, mcp_client, make_org) -> None:
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    corpo = rpc(mcp_client, token, "tools/inventado").json()
    assert corpo["error"]["code"] == -32601


def test_lote_json_rpc_e_recusado(client, mcp_client, make_org) -> None:
    """Batch saiu do MCP em 2025-06-18; aceitá-lo seria superfície sem demanda."""
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    resposta = mcp_client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
    )
    assert resposta.status_code == 400


def test_o_servidor_mcp_nao_expoe_rota_de_negocio(mcp_client) -> None:
    """A superfície para fora é ``/mcp`` e ``/health`` — nada de ``/relatorios``, ``/admin``.

    Sem esta asserção, alguém montaria "só um router a mais" no processo exposto e a
    fronteira da §7.2 evaporaria sem ninguém notar.
    """
    caminhos = {r.path for r in mcp_app.routes if hasattr(r, "path")}
    assert caminhos == {
        "/mcp", "/health", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"
    }
    # E a emissão de credencial **não** mora aqui: credencial que emite credencial
    # transformaria um vazamento em acesso permanente.
    assert mcp_client.post("/admin/mcp/credenciais", json={}).status_code == 404


# --------------------------------------------------------------------------- #
# 2. Autenticação por credencial de organização
# --------------------------------------------------------------------------- #
def test_sem_credencial_e_401(mcp_client) -> None:
    assert rpc(mcp_client, None, "tools/list").status_code == 401


@pytest.mark.parametrize(
    "token",
    ["lixo", "mcp_", "mcp_semsegredo", "Bearer mcp_a_b", "mcp_deadbeefcafe_segredoerrado"],
)
def test_credencial_malformada_ou_desconhecida_e_401(mcp_client, token: str) -> None:
    """Todas as causas colapsam no mesmo 401: distinguir seria oráculo de enumeração."""
    resposta = rpc(mcp_client, token, "tools/list")
    assert resposta.status_code == 401
    assert resposta.json()["type"].endswith("mcp-credential-invalid")


def test_credencial_revogada_deixa_de_funcionar(client, mcp_client, make_org) -> None:
    """Revogação vale na hora — não depende de o token expirar nem de job passar."""
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    assert rpc(mcp_client, token, "ping").status_code == 200

    headers = auth_header(login(client, fx.email, fx.senha))
    listagem = client.get("/admin/mcp/credenciais", headers=headers).json()["itens"]
    credencial_id = listagem[0]["id"]
    revogar = client.delete(f"/admin/mcp/credenciais/{credencial_id}", headers=headers)
    assert revogar.status_code == 200, revogar.text
    assert revogar.json()["ativa"] is False

    assert rpc(mcp_client, token, "ping").status_code == 401


def test_credencial_expirada_e_recusada(client, mcp_client, make_org) -> None:
    """Vencimento conferido por data, como a licença da Sprint 19."""
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    with admin_session() as s:
        credencial = s.query(McpCredencial).filter_by(org_id=fx.org_id).one()
        credencial.expira_em = datetime.now(UTC) - timedelta(seconds=1)
    assert rpc(mcp_client, token, "ping").status_code == 401


def test_o_segredo_nunca_volta_na_listagem(client, make_org) -> None:
    """Só o hash é persistido; a listagem não pode reemitir o que o cliente perdeu."""
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    headers = auth_header(login(client, fx.email, fx.senha))
    corpo = client.get("/admin/mcp/credenciais", headers=headers).text
    segredo = token.split("_", 2)[2]
    assert segredo not in corpo
    with admin_session() as s:
        credencial = s.query(McpCredencial).filter_by(org_id=fx.org_id).one()
        assert segredo not in credencial.segredo_hash


def test_a_credencial_nao_amplia_capacidade_do_papel(client, mcp_client, make_org) -> None:
    """Credencial é identidade, não permissão: sem ``ver`` no papel, a ferramenta recusa."""
    fx = make_org(entes=[FORTALEZA], capacidades=["administrar"])
    token = emitir_credencial(client, fx)
    resultado = chamar_ferramenta(
        mcp_client, token, "indicador_do_ente",
        {"ente": FORTALEZA, "indicador": "garantias"},
    )
    assert resultado["isError"] is True
    assert resultado["structuredContent"]["status"] == 403
    assert resultado["structuredContent"]["erro"].endswith("missing-capability")


# --------------------------------------------------------------------------- #
# 3. Isolamento entre organizações — o critério que mais importa
# --------------------------------------------------------------------------- #
@pytest.fixture
def matriz(client, make_org):
    """Dono (Fortaleza) e intruso (Maracanaú), cada um com a sua credencial MCP."""
    dono = make_org(entes=[FORTALEZA])
    intruso = make_org(entes=[MARACANAU])
    alerta_id = uuid.uuid4()
    with admin_session() as s:
        s.add(
            Alerta(
                id=alerta_id, org_id=dono.org_id, cod_ibge=FORTALEZA,
                chave=f"ia3:mcp:{uuid.uuid4().hex}", categoria="limite",
                severidade="critico", prioridade=1, titulo="Alerta do dono",
                motivo_legal="LRF art. 20", acao_sugerida="Conferir a apuração.",
                status="nova",
            )
        )
    token_dono = emitir_credencial(client, dono, nome="Agente do dono")
    token_intruso = emitir_credencial(client, intruso, nome="Agente do intruso")
    return dono, intruso, token_dono, token_intruso, alerta_id


#: Toda ferramenta que recebe ente, com argumentos válidos. A cobertura é do **catálogo**,
#: não de uma amostra: uma ferramenta nova que esqueça o gate tem de reprovar aqui.
FERRAMENTAS_COM_ENTE = [
    ("indicador_do_ente", {"indicador": "garantias"}),
    ("serie_historica", {"indicador": "pessoal_executivo"}),
    ("limites_do_ente", {}),
    ("drill_receita", {}),
    ("drill_despesa", {}),
    ("cobertura_do_ente", {"pagina": "limites"}),
    ("qualidade_do_ente", {}),
    ("alertas_do_ente", {}),
    ("comparar_com_coorte", {"indicador": "pessoal_executivo"}),
]


def test_a_matriz_cobre_todas_as_ferramentas_que_recebem_ente() -> None:
    """A lista acima não pode envelhecer em silêncio.

    Uma ferramenta nova que receba ente e não entre aqui sairia sem prova de isolamento —
    exatamente o tipo de lacuna que a E1 encontrou. Este teste falha no dia em que o
    catálogo crescer, obrigando quem cresceu a declarar os argumentos válidos dela.
    """
    from app.shared import tooling

    com_ente = {t.nome for t in tooling.registro().todas() if t.recebe_ente}
    assert {nome for nome, _ in FERRAMENTAS_COM_ENTE} == com_ente


def test_os_argumentos_da_matriz_sao_validos(client, mcp_client, make_org) -> None:
    """Garante que a matriz mede escopo, e não validação de entrada.

    Foi o defeito da primeira versão deste arquivo: ``cobertura_do_ente`` sem ``pagina``
    devolvia 422 — que acontece **antes** do gate de escopo — e o teste teria "passado"
    por recusar pelo motivo errado, sem nunca exercitar a fronteira entre tenants.
    """
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    for nome, extras in FERRAMENTAS_COM_ENTE:
        resultado = chamar_ferramenta(mcp_client, token, nome, {"ente": FORTALEZA, **extras})
        payload = resultado["structuredContent"]
        assert payload.get("status") != 422, f"{nome} recebeu argumentos inválidos: {payload}"


def test_toda_ferramenta_com_ente_recusa_ente_de_outra_organizacao(
    mcp_client, matriz
) -> None:
    """O coração da matriz: nenhuma ferramenta entrega Fortaleza a quem só tem Maracanaú.

    Percorrer o catálogo inteiro é deliberado. Testar uma ferramenta provaria que *uma*
    passa pelo gate; o que precisa ser verdade é que **nenhuma** escapa — foi assim que a
    E1 achou o ``GET /ingestao/data`` que exigia capacidade e nunca chamava
    ``assert_ente_in_scope``.
    """
    _dono, _intruso, _token_dono, token_intruso, _alerta = matriz
    for nome, extras in FERRAMENTAS_COM_ENTE:
        resultado = chamar_ferramenta(
            mcp_client, token_intruso, nome, {"ente": FORTALEZA, **extras}
        )
        payload = resultado["structuredContent"]
        assert resultado["isError"] is True, f"{nome} entregou dado de ente alheio"
        assert payload["status"] == 403, f"{nome} devolveu {payload.get('status')}"
        assert payload["erro"].endswith("scope-forbidden"), f"{nome}: {payload}"


def test_a_recusa_nao_vaza_nenhum_dado_do_ente_alheio(mcp_client, matriz) -> None:
    """O 403 não pode carregar de brinde o que ele está negando."""
    _dono, _intruso, _token_dono, token_intruso, _alerta = matriz
    for nome, extras in FERRAMENTAS_COM_ENTE:
        resultado = chamar_ferramenta(
            mcp_client, token_intruso, nome, {"ente": FORTALEZA, **extras}
        )
        texto = resultado["content"][0]["text"]
        assert "Fortaleza" not in texto, f"{nome} vazou o nome do ente na recusa"
        # O código IBGE aparece na mensagem ("o ente X não está na sua carteira"), que é
        # justamente o que o intruso já sabia — ele o digitou. O que não pode aparecer é
        # qualquer medida: valor, faixa, período apurado ou versão de entrega.
        for proibido in ("valor_rs", "valor_pct", "versao_entrega", "faixa"):
            assert proibido not in texto, f"{nome} vazou '{proibido}' na recusa"


def test_alerta_do_dono_nao_aparece_para_o_vizinho(mcp_client, matriz) -> None:
    """Vazamento de ``op`` seria pior que o de dado público: é conteúdo do cliente.

    O intruso pede os alertas do **próprio** ente, que ele pode ver — e o que se verifica é
    que o alerta da organização vizinha não está lá. É o teste que a RLS tem de sustentar.
    """
    _dono, _intruso, token_dono, token_intruso, alerta_id = matriz
    do_intruso = chamar_ferramenta(
        mcp_client, token_intruso, "alertas_do_ente", {"ente": MARACANAU}
    )
    assert do_intruso["isError"] is False
    assert "Alerta do dono" not in do_intruso["content"][0]["text"]
    assert str(alerta_id) not in do_intruso["content"][0]["text"]

    # E o dono continua enxergando o que é dele — sem este lado, um "nega tudo" passaria.
    do_dono = chamar_ferramenta(
        mcp_client, token_dono, "alertas_do_ente", {"ente": FORTALEZA}
    )
    assert do_dono["isError"] is False
    assert "Alerta do dono" in do_dono["content"][0]["text"]


def test_ente_na_carteira_mas_fora_da_licenca_tem_403_proprio(
    client, mcp_client, make_org
) -> None:
    """A distinção de causa sobrevive ao caminho MCP: cadastro × comercial."""
    fx = make_org(entes=[FORTALEZA], licenciar=False)
    token = emitir_credencial(client, fx)
    resultado = chamar_ferramenta(
        mcp_client, token, "indicador_do_ente",
        {"ente": FORTALEZA, "indicador": "garantias"},
    )
    payload = resultado["structuredContent"]
    assert payload["status"] == 403
    assert payload["erro"].endswith("ente-nao-licenciado"), payload


def test_credencial_restrita_a_subconjunto_da_carteira(client, mcp_client, make_org) -> None:
    """``escopo_ibges`` da credencial restringe como ``op.membership_escopo`` restringe.

    É o caso de uma integração que só deve enxergar um município da carteira inteira — e a
    restrição tem de valer mesmo com o ente **na** carteira e licenciado.
    """
    fx = make_org(entes=[FORTALEZA, MARACANAU])
    headers = auth_header(login(client, fx.email, fx.senha))
    papel_id = client.get("/papeis", headers=headers).json()[0]["id"]
    criada = client.post(
        "/admin/mcp/credenciais",
        headers=headers,
        json={"nome": "Só Maracanaú", "papel_id": papel_id, "escopo_ibges": [MARACANAU]},
    )
    assert criada.status_code == 201, criada.text
    token = criada.json()["token"]

    permitido = chamar_ferramenta(
        mcp_client, token, "cobertura_do_ente", {"ente": MARACANAU, "pagina": "limites"}
    )
    assert permitido["isError"] is False
    negado = chamar_ferramenta(
        mcp_client, token, "cobertura_do_ente", {"ente": FORTALEZA, "pagina": "limites"}
    )
    assert negado["isError"] is True
    assert negado["structuredContent"]["status"] == 403


def test_credencial_de_outra_organizacao_nao_pode_ser_revogada(client, make_org) -> None:
    """Mutação entre tenants: identificador alheio ⇒ **404**, e a credencial segue viva."""
    dono = make_org(entes=[FORTALEZA])
    intruso = make_org(entes=[MARACANAU])
    emitir_credencial(client, dono)
    with admin_session() as s:
        credencial = s.query(McpCredencial).filter_by(org_id=dono.org_id).one()
        credencial_id = credencial.id

    h_intruso = auth_header(login(client, intruso.email, intruso.senha))
    resposta = client.delete(f"/admin/mcp/credenciais/{credencial_id}", headers=h_intruso)
    assert resposta.status_code == 404, resposta.text
    assert str(dono.org_id) not in resposta.text

    with admin_session() as s:
        ainda_viva = s.get(McpCredencial, credencial_id)
        assert ainda_viva is not None
        assert ainda_viva.revogada_em is None, "o vizinho revogou a credencial do dono"


def test_papel_de_outra_organizacao_nao_pode_ser_emprestado(client, make_org) -> None:
    """``papel_id`` é entrada do cliente: sem a checagem, herdaria capacidade alheia."""
    dono = make_org(entes=[FORTALEZA])
    intruso = make_org(entes=[MARACANAU], capacidades=["administrar"])
    h_dono = auth_header(login(client, dono.email, dono.senha))
    papel_do_dono = client.get("/papeis", headers=h_dono).json()[0]["id"]

    h_intruso = auth_header(login(client, intruso.email, intruso.senha))
    resposta = client.post(
        "/admin/mcp/credenciais",
        headers=h_intruso,
        json={"nome": "Sequestro de papel", "papel_id": papel_do_dono},
    )
    assert resposta.status_code == 404, resposta.text


def test_credenciais_listadas_sao_so_da_propria_organizacao(client, make_org) -> None:
    dono = make_org(entes=[FORTALEZA])
    intruso = make_org(entes=[MARACANAU])
    emitir_credencial(client, dono, nome="Credencial do dono")
    emitir_credencial(client, intruso, nome="Credencial do intruso")

    h_intruso = auth_header(login(client, intruso.email, intruso.senha))
    itens = client.get("/admin/mcp/credenciais", headers=h_intruso).json()["itens"]
    nomes = {i["nome"] for i in itens}
    assert nomes == {"Credencial do intruso"}


# --------------------------------------------------------------------------- #
# 4. Auditoria (G7) pelo caminho novo
# --------------------------------------------------------------------------- #
def test_chamada_pelo_mcp_e_auditada_com_origem_e_credencial(
    client, mcp_client, make_org
) -> None:
    """A chamada externa entra na mesma ``op.ia_tool_call``, marcada como ``mcp``."""
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    chamar_ferramenta(
        mcp_client, token, "indicador_do_ente",
        {"ente": FORTALEZA, "indicador": "garantias"},
    )
    with admin_session() as s:
        linhas = (
            s.query(IaToolCall)
            .filter_by(org_id=fx.org_id, ferramenta="indicador_do_ente")
            .all()
        )
    assert linhas, "a chamada via MCP não foi auditada"
    assert all(linha.origem == "mcp" for linha in linhas)
    assert all(linha.cod_ibge == FORTALEZA for linha in linhas)


def test_a_recusa_de_escopo_tambem_e_auditada(client, mcp_client, make_org) -> None:
    """A tentativa negada é a linha mais valiosa da trilha — e sobrevive ao rollback."""
    fx = make_org(entes=[MARACANAU])
    token = emitir_credencial(client, fx)
    chamar_ferramenta(
        mcp_client, token, "indicador_do_ente",
        {"ente": FORTALEZA, "indicador": "garantias"},
    )
    with admin_session() as s:
        linha = (
            s.query(IaToolCall)
            .filter_by(org_id=fx.org_id, status="erro")
            .order_by(IaToolCall.criado_em.desc())
            .first()
        )
    assert linha is not None
    assert linha.origem == "mcp"
    assert linha.http_status == 403
    assert linha.erro_tipo is not None and linha.erro_tipo.endswith("scope-forbidden")
    assert linha.cod_ibge == FORTALEZA


def test_nome_de_ferramenta_inventado_e_auditado_e_nao_derruba_a_conversa(
    client, mcp_client, make_org
) -> None:
    """Alucinação de nome é informação de operação: vira linha de auditoria, não 500."""
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    resultado = chamar_ferramenta(mcp_client, token, "ferramenta_que_nao_existe", {})
    assert resultado["isError"] is True
    assert resultado["structuredContent"]["status"] == 404
    with admin_session() as s:
        linha = (
            s.query(IaToolCall)
            .filter_by(org_id=fx.org_id, ferramenta="ferramenta_que_nao_existe")
            .one_or_none()
        )
    assert linha is not None and linha.status == "erro"


# --------------------------------------------------------------------------- #
# 5. Garantias de conteúdo pelo caminho MCP
# --------------------------------------------------------------------------- #
def test_pergunta_sobre_garantias_volta_fundamentada_com_source_ref(
    client, mcp_client, make_org
) -> None:
    """Critério de aceite da ficha: ``garantias`` era inalcançável antes da IA-1a.

    A ferramenta responde pelo **nome do indicador**, então não depende mais do dicionário
    de palavras-chave que decidia o contexto antes da pergunta. Quando há dado, ele vem com
    ``source_ref``; quando não há, vem ``disponivel=false`` com a explicação — as duas
    saídas são aceitáveis, uma terceira (número sem fonte) não é.
    """
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    resultado = chamar_ferramenta(
        mcp_client, token, "indicador_do_ente",
        {"ente": FORTALEZA, "indicador": "garantias", "periodo": PERIODO},
    )
    assert resultado["isError"] is False
    payload = resultado["structuredContent"]
    assert payload["indicador"] == "garantias"
    if payload["disponivel"]:
        assert payload["source_ref"], "número fiscal sem source_ref (G4)"
        assert payload["source_ref"]["relatorio"]
    else:
        assert payload["observacao"], "ausência sem explicação não é ausência declarada"
        assert payload["valor_rs"] is None and payload["valor_pct"] is None


def test_argumento_fora_do_contrato_e_recusado_sem_executar(
    client, mcp_client, make_org
) -> None:
    """``extra='forbid'``: argumento inventado estoura em vez de virar filtro ignorado."""
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    resultado = chamar_ferramenta(
        mcp_client, token, "indicador_do_ente",
        {"ente": FORTALEZA, "indicador": "garantias", "exercicio": 2023},
    )
    assert resultado["isError"] is True
    assert resultado["structuredContent"]["status"] == 422


def test_as_of_e_de_primeira_classe_no_caminho_mcp(client, mcp_client, make_org) -> None:
    """G5: bitemporalidade não é privilégio da tela — o cliente externo também a tem."""
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    antigo = chamar_ferramenta(
        mcp_client, token, "indicador_do_ente",
        {
            "ente": FORTALEZA,
            "indicador": "garantias",
            "as_of": "2020-01-01T00:00:00+00:00",
        },
    )
    assert antigo["isError"] is False
    payload = antigo["structuredContent"]
    # Em 2020 não havia entrega de 2024: a resposta correta é ausência declarada.
    assert payload["disponivel"] is False
    assert payload["observacao"]

    with admin_session() as s:
        linha = (
            s.query(IaToolCall)
            .filter_by(org_id=fx.org_id, ferramenta="indicador_do_ente")
            .order_by(IaToolCall.criado_em.desc())
            .first()
        )
    assert linha is not None and linha.as_of is not None, "o as_of não chegou à auditoria"


def test_o_servidor_mcp_nao_reimplementa_regra_de_escopo(
    client, mcp_client, make_org, monkeypatch
) -> None:
    """A garantia mora na ferramenta: desligar **o** gate derruba o MCP junto.

    O alvo do *patch* é a referência que o envelope resolveu no import — a única chamada
    de ``assert_ente_in_scope`` no caminho de uma ferramenta. Se o servidor MCP tivesse uma
    cópia própria da regra (o cenário A22/E1 renascendo numa porta nova), ele continuaria
    recusando mesmo com esse gate neutralizado, e este teste falharia denunciando a
    duplicação. Ele passa porque existe **um** ponto de decisão, e ele não fica aqui.
    """
    fx = make_org(entes=[MARACANAU])
    token = emitir_credencial(client, fx)
    monkeypatch.setattr(
        "app.shared.tooling.envelope.assert_ente_in_scope", lambda *a, **k: None
    )
    resultado = chamar_ferramenta(
        mcp_client, token, "cobertura_do_ente", {"ente": FORTALEZA, "pagina": "limites"}
    )
    assert resultado["isError"] is False, (
        "com o gate do domínio desligado o MCP continuou recusando — sinal de que ele "
        "tem uma cópia própria da regra de escopo"
    )


# --------------------------------------------------------------------------- #
# 6. Unidade: autenticação fora do transporte
# --------------------------------------------------------------------------- #
def test_autenticar_resolve_principal_sem_superuser(client, make_org) -> None:
    """Credencial de máquina nunca é operador da plataforma (control plane, Sprint 19)."""
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    dados = mcp_service.autenticar(token)
    assert dados.org_id == fx.org_id
    assert dados.principal.is_superuser is False
    assert dados.principal.org_id == fx.org_id
    assert "ver" in dados.principal.capacidades


def test_autenticar_carimba_o_ultimo_uso(client, make_org) -> None:
    """Sem último uso, revogar credencial ociosa vira adivinhação."""
    fx = make_org(entes=[FORTALEZA])
    token = emitir_credencial(client, fx)
    mcp_service.autenticar(token)
    with admin_session() as s:
        credencial = s.query(McpCredencial).filter_by(org_id=fx.org_id).one()
        assert credencial.ultimo_uso_em is not None
