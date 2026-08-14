"""Ponto de entrada da API — Plataforma de Inteligência Fiscal (backend)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from app.core.config import settings
from app.core.errors import register_error_handlers
from app.modules.accounting.router import router as accounting_router
from app.modules.alerts.router import router as alerts_router
from app.modules.assistant.router import router as assistant_router
from app.modules.benchmark.router import router as benchmark_router
from app.modules.cash_rap.router import router as cash_rap_router
from app.modules.catalog.router import router as catalog_router
from app.modules.coverage.router import router as coverage_router
from app.modules.dashboard.carteira_router import router as carteira_router
from app.modules.dashboard.estadual_router import geo_router as geo_router
from app.modules.dashboard.estadual_router import router as estadual_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.debt.router import router as debt_router
from app.modules.expense.router import router as expense_router
from app.modules.forecast.router import router as forecast_router
from app.modules.health_edu.router import router as health_edu_router
from app.modules.hierarchy_demo.router import router as hierarchy_router
from app.modules.indicators.router import router as indicators_router
from app.modules.ingestion.router import integracoes_router
from app.modules.ingestion.router import router as ingestion_router
from app.modules.insights.router import router as insights_router
from app.modules.limits.router import router as limits_router
from app.modules.mcp.router import router as mcp_admin_router
from app.modules.personnel.router import router as personnel_router
from app.modules.platform.router import router as platform_router
from app.modules.quality.router import router as quality_router
from app.modules.reconciliation.router import router as reconciliation_router
from app.modules.reports.router import router as reports_router
from app.modules.result.router import router as result_router
from app.modules.revenue.router import router as revenue_router
from app.modules.tenancy.admin_router import router as tenancy_admin_router
from app.modules.tenancy.router import router as tenancy_router
from app.shared.audit import AuditMiddleware
from app.shared.http_performance import GoldHttpOptimizationMiddleware
from app.shared.seguranca import AuthRateLimitMiddleware, SecurityHeadersMiddleware

# Versão da API — servida em /health para o rodapé do app (nada de versão fixa na UI).
APP_VERSION = "0.1.0"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Plataforma de Inteligência Fiscal — API",
        version=APP_VERSION,
        description="Backend SICONFI: tenancy/RBAC, escopo multi-tenant e padrões reutilizáveis.",
    )

    register_error_handlers(app)
    # A ordem importa: o freio do login precisa responder **antes** de qualquer
    # processamento da tentativa, e os cabeçalhos precisam alcançar toda resposta,
    # inclusive a 429 emitida pelo próprio freio.
    app.add_middleware(AuditMiddleware)
    app.add_middleware(
        AuthRateLimitMiddleware,
        limite=settings.auth_rate_limit_tentativas,
        janela_segundos=settings.auth_rate_limit_janela_segundos,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # ETag sees canonical JSON; the outer GZip layer may then encode it. The
    # validator is weak and the final response varies by auth scope and encoding.
    app.add_middleware(
        GoldHttpOptimizationMiddleware,
        budget_ms=settings.http_performance_budget_ms,
        sample_window=settings.http_performance_sample_window,
    )
    # Fora de tudo: cabeçalho de segurança acompanha 200, 304, 429 e erro igualmente.
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.security_hsts_habilitado)
    app.add_middleware(
        GZipMiddleware,
        minimum_size=settings.http_gzip_minimum_size,
        compresslevel=settings.http_gzip_compresslevel,
    )

    @app.get("/health", tags=["infra"])
    def health() -> dict[str, str]:
        """Saúde + ambiente. ``app_env`` é o contrato que autoriza a UI a exibir a
        credencial de demonstração — fora de ``local`` ela nunca aparece."""
        return {"status": "ok", "app_env": settings.app_env, "version": APP_VERSION}

    app.include_router(tenancy_router)
    app.include_router(tenancy_admin_router)
    # Recurso didatico legado: nunca exponha medidas ficticias em ambientes reais.
    if settings.app_env.lower() in {"local", "development", "test"}:
        app.include_router(hierarchy_router)
    app.include_router(ingestion_router)
    app.include_router(integracoes_router)
    app.include_router(catalog_router)
    app.include_router(indicators_router)
    app.include_router(limits_router)
    app.include_router(revenue_router)
    app.include_router(expense_router)
    app.include_router(personnel_router)
    app.include_router(debt_router)
    app.include_router(result_router)
    app.include_router(cash_rap_router)
    app.include_router(accounting_router)
    app.include_router(benchmark_router)
    app.include_router(forecast_router)
    app.include_router(alerts_router)
    app.include_router(reports_router)
    app.include_router(assistant_router)
    # IA nas telas (Sprint IA-5): explique-este-número, alertas, relatório e Central de Dados.
    app.include_router(insights_router)
    app.include_router(health_edu_router)
    app.include_router(dashboard_router)
    app.include_router(carteira_router)
    app.include_router(estadual_router)
    app.include_router(geo_router)
    app.include_router(quality_router)
    app.include_router(platform_router)
    app.include_router(coverage_router)
    app.include_router(reconciliation_router)
    # Só a **administração** das credenciais MCP vive aqui. O protocolo em si é servido
    # por ``app.mcp_main``, em processo e porta próprios (§7.3 do plano de MCP).
    app.include_router(mcp_admin_router)

    @app.on_event("startup")
    def recover_report_jobs() -> None:
        """Retoma fila persistida e dispara agendamentos vencidos após restart."""
        from app.workers import carteira_tasks, ingest_jobs, quality_tasks, report_tasks

        report_tasks.recover_pending()
        report_tasks.executar_agendamentos()
        # O relógio é de **um** processo. Com várias réplicas de uvicorn, cada uma
        # emitiria o mesmo relatório e rodaria os mesmos checks — em produção quem
        # agenda é o ``scheduler_worker``. Fora dela, subir junto é conveniente.
        if not settings.scheduler_em_processo_separado:
            report_tasks.start_scheduler()
            quality_tasks.start_scheduler()
            carteira_tasks.start_scheduler()
        # Reentrega pendências duráveis; o consumidor in-process é apenas opt-in local.
        ingest_jobs.recover_pending()
        if settings.ingest_worker_in_process:
            ingest_jobs.start_worker()

    @app.on_event("shutdown")
    def stop_report_scheduler() -> None:
        """Interrompe o relógio local sem abandonar os jobs já persistidos."""
        from app.workers import ingest_jobs, report_tasks

        if not settings.scheduler_em_processo_separado:
            from app.workers import carteira_tasks, quality_tasks

            report_tasks.stop_scheduler()
            quality_tasks.stop_scheduler()
            carteira_tasks.stop_scheduler()
        if settings.ingest_worker_in_process:
            ingest_jobs.stop_worker()

    return app


app = create_app()
