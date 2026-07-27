"""Configuração central da aplicação (12-factor, via variáveis de ambiente / .env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Raiz do projeto backend (dois níveis acima de src/app/core/config.py -> src/app -> src -> raiz)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Configuração tipada carregada de variáveis de ambiente / arquivo .env."""

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Banco
    database_url: str = (
        "postgresql+psycopg2://plataforma_app:plataforma_app@localhost:5432/plataforma_fiscal"
    )
    database_admin_url: str = (
        "postgresql+psycopg2://postgres:postgres@localhost:5432/plataforma_fiscal"
    )
    db_name: str = "plataforma_fiscal"
    app_db_role: str = "plataforma_app"
    app_db_password: str = "plataforma_app"

    # Auth / JWT
    jwt_secret: str = "dev-secret-nao-usar-em-producao"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480

    # Infra
    redis_url: str = "redis://localhost:6379/0"
    # O consumidor RQ deve rodar em processo separado em produção. O worker
    # embutido existe apenas como conveniência explícita para desenvolvimento.
    ingest_worker_in_process: bool = False
    app_env: str = "local"
    reports_storage_dir: str = str(_PROJECT_ROOT / "var" / "reports")

    # IA / Assistente (Sprint 17) — Google Gemini via porta LLMProvider.
    # Sem chave (dev/test/CI), o assistente usa o provedor local determinístico
    # (offline, extrativo, nunca inventa número). Com chave, usa o Gemini.
    gemini_api_key: str | None = None
    # ``local`` força o provedor offline mesmo com chave (útil em testes/demos).
    # ``auto`` usa Gemini se houver chave; senão, o local.
    assistant_provider: str = "auto"
    assistant_chat_model: str = "gemini-2.5-flash"
    assistant_summary_model: str = "gemini-2.5-pro"
    assistant_embedding_model: str = "gemini-embedding-001"
    # Backend do vector store normativo, independente do provedor de chat:
    # ``local`` (default) = embedder determinístico (reprodutível, offline, custo zero por
    # consulta, ideal p/ o corpo curado); ``gemini`` = gemini-embedding-001 (produção com
    # pgvector). Manter local evita chamadas de embedding a cada pergunta e churn no store
    # compartilhado; o chat continua no Gemini.
    assistant_embedding_backend: str = "local"
    assistant_request_timeout_s: float = 30.0
    # Nº de dispositivos normativos recuperados por pergunta (RAG).
    assistant_norma_top_k: int = 3

    # Fontes oficiais de enriquecimento de saude/educacao (Sprint 11).
    # Mantidas configuraveis para permitir espelhos institucionais sem alterar codigo.
    siops_base_url: str = "https://siops-consulta-publica-api.saude.gov.br/v1/"
    siope_base_url: str = (
        "https://www.fnde.gov.br/olinda-ide/servico/"
        "DADOS_ABERTOS_SIOPE/versao/v1/odata/"
    )
    tesouro_transferencias_base_url: str = (
        "https://apiapex.tesouro.gov.br/aria/v1/"
        "transferencias_constitucionais/custom/"
    )
    # Fallback municipal para os Anexos 8/12 que podem faltar no endpoint TT/RREO.
    # O template padrao e deliberadamente restrito a Fortaleza; outros entes devem
    # informar ``params.page_url_template`` na requisicao de carga.
    rreo_minimos_pdf_default_cod_ibge: str = "2304400"
    rreo_minimos_pdf_page_url_template: str = (
        "https://transparencia.fortaleza.ce.gov.br/"
        "index.php/contasPublicas/rreo/{ano}"
    )

    # CORS — origens do frontend (Vite dev). Lista separada por vírgula em CORS_ORIGINS.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Retorna a configuração (cacheada) — permite override em testes via env."""
    return Settings()


settings = get_settings()
