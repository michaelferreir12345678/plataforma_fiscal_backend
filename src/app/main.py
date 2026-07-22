"""Ponto de entrada da API — Plataforma de Inteligência Fiscal (backend)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.errors import register_error_handlers
from app.modules.accounting.router import router as accounting_router
from app.modules.benchmark.router import router as benchmark_router
from app.modules.cash_rap.router import router as cash_rap_router
from app.modules.catalog.router import router as catalog_router
from app.modules.dashboard.carteira_router import router as carteira_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.debt.router import router as debt_router
from app.modules.expense.router import router as expense_router
from app.modules.health_edu.router import router as health_edu_router
from app.modules.hierarchy_demo.router import router as hierarchy_router
from app.modules.indicators.router import router as indicators_router
from app.modules.ingestion.router import router as ingestion_router
from app.modules.limits.router import router as limits_router
from app.modules.personnel.router import router as personnel_router
from app.modules.result.router import router as result_router
from app.modules.revenue.router import router as revenue_router
from app.modules.tenancy.router import router as tenancy_router
from app.shared.audit import AuditMiddleware


def create_app() -> FastAPI:
    app = FastAPI(
        title="Plataforma de Inteligência Fiscal — API",
        version="0.1.0",
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
        return {"status": "ok"}

    app.include_router(tenancy_router)
    # Recurso didatico legado: nunca exponha medidas ficticias em ambientes reais.
    if settings.app_env.lower() in {"local", "development", "test"}:
        app.include_router(hierarchy_router)
    app.include_router(ingestion_router)
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
    app.include_router(health_edu_router)
    app.include_router(dashboard_router)
    app.include_router(carteira_router)
    return app


app = create_app()
