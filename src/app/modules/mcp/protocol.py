"""Tradução JSON-RPC 2.0 ⇄ camada de ferramentas (Sprint IA-3).

Este módulo é a parte "burra" prometida na §2.2 do plano: ele decodifica o método MCP,
encaminha para ``shared/tooling`` e serializa a resposta. Não consulta banco por conta
própria, não decide escopo, não formata número fiscal. Se um dia precisar fazer qualquer
uma dessas coisas, a regra vazou para a borda.

**Três decisões que não são óbvias:**

1. **Nome de ferramenta desconhecido não é erro de protocolo aqui.** A leitura literal do
   JSON-RPC mandaria devolver ``-32602``. Mas quem erra o nome é um modelo do outro lado, e
   ``invoke()`` audita a tentativa (``op.ia_tool_call`` com ``status='erro'``) justamente
   porque nome inventado é informação de operação. Verificar a existência **antes** de
   chamar o envelope economizaria uma exceção e perderia o registro — então tudo passa por
   ``invoke()``, e a recusa volta como resultado de ferramenta com ``isError``.

2. **Recusa de escopo volta como conteúdo, não como exceção.** Um 403 de ente fora da
   carteira é um **fato** que o agente do outro lado precisa ler e respeitar ("não posso
   acessar este ente"), e não uma falha de transporte. É a mesma escolha que o assistente
   interno já fazia em ``erro_para_payload``. O payload carrega o ``status`` e o ``type``
   do Problem Details — é por eles que a matriz de isolamento distingue *fora da carteira*
   de *sem licença*, sem precisar de um código HTTP que o JSON-RPC não tem onde pôr.

3. **Sem ``outputSchema``.** Declará-lo obrigaria o servidor a validar ``structuredContent``
   contra ele em toda resposta, inclusive nas de recusa — e a recusa tem forma própria. O
   contrato de saída continua garantido onde importa: o envelope revalida a saída da
   ferramenta e exige ``source_ref`` (G4) antes de qualquer coisa chegar aqui.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError
from app.shared import tooling
from app.shared.tooling.errors import RegistroInvalidoError

logger = logging.getLogger(__name__)

#: Versão do protocolo que este servidor fala.
PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "plataforma-fiscal-mcp"
SERVER_VERSION = "0.1.0"

#: ``origem`` gravada em ``op.ia_tool_call`` — é o que responde "quanto do consumo veio de
#: cliente externo?" sem instrumentar cada exposição (ver ``ToolContext``).
ORIGEM = "mcp"

# Códigos JSON-RPC 2.0.
ERRO_PARSE = -32700
ERRO_REQUISICAO = -32600
ERRO_METODO = -32601
ERRO_PARAMS = -32602
ERRO_INTERNO = -32603

INSTRUCOES = (
    "Ferramentas de inteligência fiscal sobre dados do SICONFI já calculados e "
    "versionados. Todo número devolvido carrega 'source_ref' (relatório, anexo, período e "
    "versão da entrega) — cite-o. Nenhuma ferramenta estima: quando o dado não foi "
    "apurado, a resposta traz 'disponivel: false' com a explicação, e essa ausência deve "
    "ser relatada como ausência, nunca como zero. Carregue os recursos 'dicionario://' "
    "antes de interpretar qualquer indicador: a definição oficial de fórmula, denominador "
    "e sentido é dado da plataforma e prevalece sobre conhecimento geral."
)


class ErroRpc(Exception):
    """Erro de **protocolo** (não de ferramenta): vira o campo ``error`` do JSON-RPC."""

    def __init__(self, codigo: int, mensagem: str, dados: Any = None) -> None:
        self.codigo = codigo
        self.mensagem = mensagem
        self.dados = dados
        super().__init__(mensagem)


def _texto(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _resultado_ferramenta(payload: dict[str, Any], *, erro: bool) -> dict[str, Any]:
    """Envelope de ``tools/call``: texto legível pelo modelo + estrutura para o cliente."""
    return {
        "content": [{"type": "text", "text": _texto(payload)}],
        "structuredContent": payload,
        "isError": erro,
    }


def descrever_ferramentas() -> list[dict[str, Any]]:
    """``tools/list`` — o registro do domínio, 1:1, sem curadoria por protocolo."""
    return [
        {
            "name": tool.nome,
            "description": tool.descricao,
            "inputSchema": tool.schema_entrada(),
        }
        for tool in tooling.registro().todas()
    ]


def descrever_recursos() -> list[dict[str, Any]]:
    """``resources/list`` — o registro de recursos da IA-2, 1:1 (§2.3)."""
    return [
        {
            "uri": recurso.uri,
            "name": recurso.nome,
            "description": recurso.descricao,
            "mimeType": recurso.mime_type,
        }
        for recurso in tooling.registro_de_recursos().todos()
    ]


def _chamar_ferramenta(
    session: Session, principal: Principal, params: dict[str, Any], *, origem_ref: str | None
) -> dict[str, Any]:
    nome = params.get("name")
    if not isinstance(nome, str) or not nome:
        raise ErroRpc(ERRO_PARAMS, "O parâmetro 'name' é obrigatório em tools/call.")
    argumentos = params.get("arguments") or {}
    if not isinstance(argumentos, dict):
        raise ErroRpc(ERRO_PARAMS, "O parâmetro 'arguments' deve ser um objeto.")

    ctx = tooling.ToolContext(
        session=session, principal=principal, origem=ORIGEM, origem_ref=origem_ref
    )
    try:
        resultado = tooling.invoke(ctx, tooling.registro(), nome, argumentos)
    except AppError as exc:
        # Escopo, licença, capacidade, nome inexistente, argumento inválido: tudo já foi
        # auditado dentro do envelope. Aqui só se comunica a recusa ao agente.
        return _resultado_ferramenta(tooling.erro_para_payload(exc), erro=True)
    return _resultado_ferramenta(resultado.payload, erro=False)


def _ler_recurso(session: Session, params: dict[str, Any]) -> dict[str, Any]:
    uri = params.get("uri")
    if not isinstance(uri, str) or not uri:
        raise ErroRpc(ERRO_PARAMS, "O parâmetro 'uri' é obrigatório em resources/read.")
    recurso = tooling.registro_de_recursos().get(uri)
    if recurso is None:
        raise ErroRpc(
            ERRO_PARAMS,
            f"Recurso desconhecido: '{uri}'.",
            {"recursos": tooling.registro_de_recursos().uris()},
        )
    try:
        conteudo = tooling.ler_recurso(session, uri)
    except RegistroInvalidoError as exc:  # pragma: no cover - guardado pelo get() acima
        raise ErroRpc(ERRO_PARAMS, str(exc)) from exc
    return {
        "contents": [{"uri": uri, "mimeType": recurso.mime_type, "text": conteudo}]
    }


def _despachar(
    session: Session,
    principal: Principal,
    metodo: str,
    params: dict[str, Any],
    *,
    origem_ref: str | None,
) -> dict[str, Any]:
    if metodo == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False},
            },
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCOES,
        }
    if metodo == "ping":
        return {}
    if metodo == "tools/list":
        return {"tools": descrever_ferramentas()}
    if metodo == "tools/call":
        return _chamar_ferramenta(session, principal, params, origem_ref=origem_ref)
    if metodo == "resources/list":
        return {"resources": descrever_recursos()}
    if metodo == "resources/templates/list":
        # Nenhum recurso é parametrizado, por decisão de desenho: recurso com ``{ente}``
        # seria dado de ente entrando por um caminho sem gate de escopo (``recursos.py``).
        return {"resourceTemplates": []}
    if metodo == "resources/read":
        return _ler_recurso(session, params)
    raise ErroRpc(ERRO_METODO, f"Método não suportado: '{metodo}'.")


def processar(
    session: Session,
    principal: Principal,
    mensagem: Any,
    *,
    origem_ref: str | None = None,
) -> dict[str, Any] | None:
    """Processa uma mensagem JSON-RPC. Retorna ``None`` para notificações (sem ``id``).

    Separada do transporte HTTP de propósito: é aqui que o comportamento do protocolo é
    testável sem subir servidor, e é isto que a suíte de isolamento exercita.
    """
    if not isinstance(mensagem, dict):
        return _erro(None, ERRO_REQUISICAO, "A mensagem JSON-RPC deve ser um objeto.")
    identificador = mensagem.get("id")
    metodo = mensagem.get("method")
    if mensagem.get("jsonrpc") != "2.0" or not isinstance(metodo, str):
        return _erro(identificador, ERRO_REQUISICAO, "Requisição JSON-RPC 2.0 malformada.")
    params = mensagem.get("params") or {}
    if not isinstance(params, dict):
        return _erro(identificador, ERRO_PARAMS, "'params' deve ser um objeto.")

    # Notificação: o cliente não espera resposta (``notifications/initialized`` é a única
    # que importa hoje). Responder a uma notificação é violação de protocolo.
    if identificador is None:
        if not metodo.startswith("notifications/"):
            logger.info("Requisição MCP sem 'id' e sem ser notificação: %s", metodo)
        return None

    try:
        resultado = _despachar(session, principal, metodo, params, origem_ref=origem_ref)
    except ErroRpc as exc:
        return _erro(identificador, exc.codigo, exc.mensagem, exc.dados)
    except AppError as exc:
        # Erro de aplicação fora de ``tools/call`` (ex.: sem organização ativa).
        return _erro(
            identificador,
            ERRO_INTERNO,
            exc.title,
            {"type": exc.type, "detail": exc.detail, "status": exc.status},
        )
    return {"jsonrpc": "2.0", "id": identificador, "result": resultado}


def _erro(
    identificador: Any, codigo: int, mensagem: str, dados: Any = None
) -> dict[str, Any]:
    erro: dict[str, Any] = {"code": codigo, "message": mensagem}
    if dados is not None:
        erro["data"] = dados
    return {"jsonrpc": "2.0", "id": identificador, "error": erro}
