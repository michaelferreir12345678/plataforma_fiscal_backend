"""Transporte HTTP do servidor MCP (Sprint IA-3).

Streamable HTTP em modo **sem estado**: um ``POST /mcp`` com uma mensagem JSON-RPC, uma
resposta JSON. Não há ``Mcp-Session-Id`` nem fluxo SSE porque não há nada a manter entre
chamadas — o registro de ferramentas é imutável e cada chamada carrega a sua própria
identidade. Servidor sem estado é o que permite subir N réplicas atrás do proxy sem
sessão fixa, exatamente como a ``api`` já faz.

**Autenticação.** ``Authorization: Bearer mcp_<prefixo>_<segredo>``. A falha é sempre 401
indistinto (ver ``service.CredencialInvalidaError``). O 401 sai como Problem Details
porque ``register_error_handlers`` está montado na aplicação — mesmo contrato de erro da
API, sem um formato próprio para o mundo externo.

**O que este arquivo não faz:** decidir o que o cliente pode ler. Ele fixa o contexto de
RLS com a organização da credencial e entrega a sessão ao protocolo; o gate por ente roda
dentro da ferramenta, como para qualquer outro chamador.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Request, Response
from sqlalchemy.orm import Session

from app.core.db import apply_context
from app.core.deps import get_db
from app.core.errors import AppError
from app.modules.mcp import protocol, service
from app.modules.mcp.service import Autenticacao

router = APIRouter(tags=["mcp"])

#: Corpo máximo aceito. Um cliente MCP manda argumentos de ferramenta, não documento.
_MAX_BYTES = 256 * 1024


def autenticacao(
    request: Request, authorization: str | None = Header(default=None)
) -> Autenticacao:
    """Resolve o ``Authorization: Bearer`` numa identidade de organização."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise service.CredencialInvalidaError()
    dados = service.autenticar(authorization.split(" ", 1)[1].strip())
    # Disponível para o middleware de auditoria e para o log de acesso.
    request.state.principal = dados.principal
    return dados


def sessao_mcp(
    auth: Autenticacao = Depends(autenticacao), session: Session = Depends(get_db)
) -> Session:
    """Sessão da requisição com o contexto multi-tenant da credencial já fixado.

    Mesmo passo que ``get_current_principal`` executa para o navegador: sem ele a sessão
    continua em *default-deny* e a RLS nega tudo. Com ele, a organização da credencial —
    e **apenas** ela — é a que o banco enxerga.
    """
    apply_context(
        session,
        org_id=auth.org_id,
        user_id=auth.principal.usuario_id,
        is_admin=False,
    )
    return session


@router.post("/mcp")
def mcp(
    response: Response,
    mensagem: Any = Body(default=None),
    auth: Autenticacao = Depends(autenticacao),
    session: Session = Depends(sessao_mcp),
) -> Any:
    """Uma mensagem JSON-RPC 2.0 do cliente MCP.

    Lote (``[...]``) é recusado: o MCP retirou o batch do protocolo em 2025-06-18, e
    aceitá-lo aqui criaria um caminho de execução com semântica de erro própria — mais
    superfície na única porta que dá para fora, por compatibilidade que ninguém pediu.
    """
    if isinstance(mensagem, list):
        response.status_code = 400
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": protocol.ERRO_REQUISICAO,
                "message": "Lote JSON-RPC não é suportado; envie uma mensagem por vez.",
            },
        }
    resultado = protocol.processar(
        session, auth.principal, mensagem, origem_ref=str(auth.credencial_id)
    )
    if resultado is None:
        # Notificação: aceita e sem corpo, como manda o transporte Streamable HTTP.
        response.status_code = 202
        return None
    return resultado


@router.get("/mcp")
def mcp_sem_stream() -> Response:
    """Sem fluxo SSE: o servidor não abre canal do servidor para o cliente (spec, §GET)."""
    return Response(status_code=405, headers={"Allow": "POST"})


async def limitar_corpo(request: Request, call_next: Any) -> Any:
    """Recusa corpo grande **antes** de desserializar JSON.

    Deixar o parser decidir significa alocar o documento inteiro na memória do processo que
    fica exposto à internet. O teto é generoso para argumento de ferramenta e ridículo para
    quem tenta usar a porta como depósito.
    """
    tamanho = request.headers.get("content-length")
    if tamanho and tamanho.isdigit() and int(tamanho) > _MAX_BYTES:
        raise AppError(
            status=413,
            title="Corpo grande demais",
            detail=f"O corpo da requisição MCP excede {_MAX_BYTES} bytes.",
        )
    return await call_next(request)
