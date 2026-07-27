"""Ponto de entrada da API — Plataforma de Inteligência Fiscal (backend)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.errors import register_error_handlers
from app.modules.accounting.router import router as accounting_router
from app.modules.alerts.router import router as alerts_router
from app.modules.assistant.router import router as assistant_router
from app.modules.benchmark.router import router as benchmark_router
from app.modules.cash_rap.router import router as cash_rap_router
from app.modules.catalog.router import router as catalog_router
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
from app.modules.limits.router import router as limits_router
from app.modules.personnel.router import router as personnel_router
from app.modules.reports.router import router as reports_router
from app.modules.result.router import router as result_router
from app.modules.revenue.router import router as revenue_router
from app.modules.tenancy.admin_router import router as tenancy_admin_router
from app.modules.tenancy.router import router as tenancy_router
from app.shared.audit import AuditMiddleware

# Versão da API — servida em /health para o rodapé do app (nada de versão fixa na UI).
APP_VERSION = "0.1.0"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Plataforma de Inteligência Fiscal — API",
        version=APP_VERSION,
        description="Backend SICONFI: tenancy/RBAC, escopo multi-tenant e padrões reutilizáveis.",
    )

    register_error_handlers(app)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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
    app.include_router(health_edu_router)
    app.include_router(dashboard_router)
    app.include_router(carteira_router)
    app.include_router(estadual_router)
    app.include_router(geo_router)

    @app.on_event("startup")
    def recover_report_jobs() -> None:
        """Retoma fila persistida e dispara agendamentos vencidos após restart."""
        from app.workers import ingest_jobs, report_tasks

        report_tasks.recover_pending()
        report_tasks.executar_agendamentos()
        report_tasks.start_scheduler()
        # Reentrega pendências duráveis; o consumidor in-process é apenas opt-in local.
        ingest_jobs.recover_pending()
        if settings.ingest_worker_in_process:
            ingest_jobs.start_worker()

    @app.on_event("shutdown")
    def stop_report_scheduler() -> None:
        """Interrompe o relógio local sem abandonar os jobs já persistidos."""
        from app.workers import ingest_jobs, report_tasks

        report_tasks.stop_scheduler()
        if settings.ingest_worker_in_process:
            ingest_jobs.stop_worker()

    return app


app = create_app()
