"""Configuração central da aplicação (12-factor, via variáveis de ambiente / .env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
    # Sprint 27: compressao e orcamento dos GETs analiticos.
    http_gzip_minimum_size: int = Field(default=500, ge=0)
    http_gzip_compresslevel: int = Field(default=6, ge=0, le=9)
    http_performance_budget_ms: float = Field(default=500.0, gt=0)
    http_performance_sample_window: int = Field(default=256, ge=1)

    # Sprint 28: defesas de borda. O limite é de tentativas **falhas** de login por
    # origem + identidade tentada; acertar a senha devolve a cota.
    auth_rate_limit_tentativas: int = Field(default=10, ge=1)
    auth_rate_limit_janela_segundos: float = Field(default=300.0, gt=0)
    # HSTS só é enviado sob HTTPS. O desligamento existe para ambientes que terminam
    # TLS num proxy e preferem emitir o cabeçalho lá.
    security_hsts_habilitado: bool = True
    # Em produção o relógio (relatórios agendados + verificações de qualidade) roda no
    # ``scheduler_worker``, um processo só. Ligado aqui, a API não arma agendador —
    # senão cada réplica emitiria o mesmo relatório.
    scheduler_em_processo_separado: bool = False

    # Pool de conexões. O padrão do SQLAlchemy (5 + 10) não serve 50 usuários
    # simultâneos; estes valores vêm do teste de carga da Sprint 28. Ao mudar,
    # confira a conta contra o ``max_connections`` do Postgres:
    #   (db_pool_size + db_max_overflow) × réplicas_da_api + workers < max_connections
    db_pool_size: int = Field(default=20, ge=1)
    db_max_overflow: int = Field(default=20, ge=0)
    # Espera curta de propósito: pool cheio deve falhar visível, não pendurar meio
    # minuto num carregamento que o usuário já abandonou.
    db_pool_timeout_s: float = Field(default=10.0, gt=0)
    # Recicla conexão antes que um firewall/pgbouncer a derrube em silêncio.
    db_pool_recycle_s: int = Field(default=1800, ge=60)

    # IA / Assistente (Sprint 17) — Google Gemini via porta LLMProvider.
    # Sem chave (dev/test/CI), o assistente usa o provedor local determinístico
    # (offline, extrativo, nunca inventa número). Com chave, usa o Gemini.
    gemini_api_key: str | None = None
    # ``local`` força o provedor offline mesmo com chave (útil em testes/demos).
    # ``auto`` usa Gemini se houver chave; senão, o local.
    assistant_provider: str = "auto"
    assistant_chat_model: str = "gemini-3.5-flash"
    #: Modelos tentados **em ordem** se o principal falhar por indisponibilidade
    #: (404/model not found, 429, 503). Só isso: erro de conteúdo ou de credencial não
    #: cai para outro modelo, senão uma configuração errada ficaria escondida atrás de
    #: um degrade silencioso. A resposta sempre declara qual modelo respondeu.
    assistant_chat_fallback_models: tuple[str, ...] = ("gemini-2.5-flash",)
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
    # Laço de agente (Sprint IA-3). Um modelo que não converge em 4 rodadas não converge
    # na quinta, e um laço aberto gasta a cota da organização sem produzir texto. O teto
    # não derruba a resposta: estourá-lo degrada para uma resposta **parcial declarada**,
    # composta só dos valores que as ferramentas já devolveram (ver ``assistant/agente.py``).
    assistant_agente_max_passos: int = 4
    # Orçamento de tokens do laço inteiro (entrada + saída somadas de todas as voltas).
    # Existe porque o teto de passos sozinho não limita custo: uma única volta com dez
    # séries históricas no contexto custa mais que quatro voltas curtas.
    assistant_agente_max_tokens: int = 60000

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
