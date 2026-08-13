"""Entrypoint do servidor MCP — o quarto processo da mesma imagem (Sprint IA-3, §7.3).

``api``, ``ingest-worker`` e ``scheduler`` já são a mesma imagem com comandos diferentes.
Este módulo acrescenta o quarto: mesmo código de domínio, processo, porta, limites e falha
próprios.

    uvicorn app.mcp_main:app --host 0.0.0.0 --port 8010

**Por que uma aplicação ASGI separada, e não mais um router na ``app.main``.** A camada de
ferramentas é domínio e mora no monólito — isso está decidido (§7.1). O servidor MCP é
fronteira: público diferente, autenticação diferente (credencial de organização, não sessão
de navegador), perfil de carga oposto (laço de agente longo × tela curta e sensível a
latência) e raio de explosão que não pode alcançar a plataforma. Montar o ``/mcp`` na
mesma aplicação daria o benefício zero de compartilhar processo e o custo de um cliente
externo competindo com o gestor que abriu o Cockpit.

**O que ele deliberadamente NÃO monta:** nenhum router de negócio. Um cliente MCP não
alcança ``/relatorios``, ``/alertas`` nem ``/admin`` por este processo — a superfície é
``POST /mcp`` e ``GET /health``, e nada mais. Se um dia a lista crescer, a pergunta certa
é por que a porta para fora precisa de mais.

**O que ele compartilha, porque tem de compartilhar:** o ``ToolRegistry``, o envelope com
escopo/licença/``source_ref``/auditoria e a mesma conexão com o banco. É o que torna a
divergência entre as duas exposições estruturalmente impossível: é o mesmo objeto Python
executando as mesmas verificações.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware

from app.core.config import settings
from app.core.errors import register_error_handlers
from app.modules.mcp.transporte import limitar_corpo
from app.modules.mcp.transporte import router as mcp_router
from app.shared.seguranca import SecurityHeadersMiddleware

MCP_VERSION = "0.1.0"


def create_mcp_app() -> FastAPI:
    app = FastAPI(
        title="Plataforma de Inteligência Fiscal — servidor MCP",
        version=MCP_VERSION,
        description=(
            "Exposição MCP da camada de ferramentas governadas. Autenticação por "
            "credencial de organização; escopo, licença e auditoria dentro da ferramenta."
        ),
    )
    register_error_handlers(app)
    app.middleware("http")(limitar_corpo)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.security_hsts_habilitado)
    app.add_middleware(GZipMiddleware, minimum_size=settings.http_gzip_minimum_size)

    # Sem CORS: cliente MCP é servidor/agente, não navegador. Liberar origem aqui seria
    # convidar uma página qualquer a usar a credencial de quem a tiver no navegador.

    @app.get("/health", tags=["infra"])
    def health() -> dict[str, str]:
        return {"status": "ok", "servico": "mcp", "version": MCP_VERSION}

    app.include_router(mcp_router)
    return app


app = create_mcp_app()
