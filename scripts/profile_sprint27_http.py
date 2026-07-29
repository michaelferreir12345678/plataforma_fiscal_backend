"""Measure the Sprint 27 page endpoints through a running HTTP application.

Unlike ``profile_sprint27.py`` (read-only SQL plans), this script exercises the
real authentication, tenant scope, serialization, middleware and network stack.
Credentials are read only from environment variables and are never printed.

Required environment variables:

* ``SPRINT27_HTTP_STATE_EMAIL`` / ``SPRINT27_HTTP_STATE_PASSWORD``: an account
  with the profiled UF and entity in scope;
* ``SPRINT27_HTTP_MSC_EMAIL`` / ``SPRINT27_HTTP_MSC_PASSWORD``: an account with
  the MSC entity in scope (may be the same account).
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

import httpx
from sqlalchemy import Connection, create_engine, text

from app.core.config import settings

if __package__:
    from scripts.profile_sprint27 import Profile, _one, _profiles
else:
    from profile_sprint27 import Profile, _one, _profiles

_APP_DURATION = re.compile(r"(?:^|,\s*)app;dur=([0-9]+(?:\.[0-9]+)?)")
_Credential = Literal["state", "msc"]


@dataclass(frozen=True)
class HttpProfile:
    """One authenticated GET representative of a fiscal page."""

    name: str
    endpoint: str
    path: str
    params: dict[str, Any]
    credential: _Credential


@dataclass(frozen=True)
class HttpResult:
    """Measured wall-clock and application durations for one endpoint."""

    profile: HttpProfile
    runs: int
    http_p50_ms: float
    http_p95_ms: float
    http_max_ms: float
    app_p50_ms: float
    app_p95_ms: float
    response_bytes: int


def _nearest_rank(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Defina {name}; credenciais nunca sao aceitas pela linha de comando.")
    return value


def _benchmark_anchor(
    conn: Connection,
    benchmark: Profile,
    *,
    preferred_uf: str,
) -> str:
    """Choose an entity in the profiled cohort and the configured UF scope."""

    row = _one(
        conn,
        """
        SELECT cod_ibge
        FROM gold.mart_benchmark
        WHERE coorte_id = :coorte_id
          AND indicador = :indicador
          AND periodo = :periodo
          AND snapshot_hash = :snapshot_hash
          AND substr(cod_ibge, 1, 2) = :uf
        ORDER BY cod_ibge
        LIMIT 1
        """,
        {**benchmark.params, "uf": preferred_uf},
    )
    return str(row["cod_ibge"])


def _http_profiles(conn: Connection) -> list[HttpProfile]:
    sql_profiles = {profile.name: profile for profile in _profiles(conn)}
    msc = sql_profiles["01_msc_arvore"].params
    msc_matrix = sql_profiles["02_msc_matriz_mensal"].params
    ranking = sql_profiles["03_ranking_uf"].params
    carteira = sql_profiles["04_carteira_entes"].params
    benchmark = sql_profiles["05_benchmark_ranking"]
    cobertura = sql_profiles["06_cobertura_total"].params
    dashboard = sql_profiles["08_dashboard_indicadores"].params
    receita = sql_profiles["09_receita_arvore"].params
    despesa = sql_profiles["10_despesa_arvore"].params
    anchor = _benchmark_anchor(conn, benchmark, preferred_uf=str(ranking["uf"]))

    return [
        HttpProfile(
            "01_msc_arvore",
            "GET /entes/{cod_ibge}/msc/arvore",
            f"/entes/{quote(str(msc['cod_ibge']), safe='')}/msc/arvore",
            {"periodo": msc["periodo"]},
            "msc",
        ),
        HttpProfile(
            "02_msc_matriz_mensal",
            "GET /entes/{cod_ibge}/msc/conta/{codigo}/saldos",
            (
                f"/entes/{quote(str(msc_matrix['cod_ibge']), safe='')}/msc/conta/"
                f"{quote(str(msc_matrix['cod_conta']), safe='')}/saldos"
            ),
            {"ano": msc_matrix["ano"]},
            "msc",
        ),
        HttpProfile(
            "03_ranking_uf",
            "GET /uf/{uf}/ranking",
            f"/uf/{quote(str(ranking['uf']), safe='')}/ranking",
            {"indicador": ranking["indicador"], "periodo": ranking["periodo"]},
            "state",
        ),
        HttpProfile(
            "04_carteira_entes",
            "GET /carteira/entes",
            "/carteira/entes",
            {"periodo": carteira["periodo"], "page_size": 200},
            "state",
        ),
        HttpProfile(
            "05_benchmark_ranking",
            "GET /benchmark/ranking",
            "/benchmark/ranking",
            {
                "ente": anchor,
                "coorte": str(benchmark.params["coorte_id"]),
                "indicador": benchmark.params["indicador"],
                "periodo": benchmark.params["periodo"],
                "por_pagina": 200,
            },
            "state",
        ),
        HttpProfile(
            "06_cobertura_total",
            "GET /admin/ingestion/cobertura (1 grupo)",
            "/admin/ingestion/cobertura",
            {**cobertura, "page_size": 1},
            "state",
        ),
        HttpProfile(
            "07_cobertura_pagina",
            "GET /admin/ingestion/cobertura (100 grupos)",
            "/admin/ingestion/cobertura",
            {**cobertura, "page_size": 100},
            "state",
        ),
        HttpProfile(
            "08_dashboard_indicadores",
            "GET /entes/{cod_ibge}/dashboard",
            f"/entes/{quote(str(dashboard['cod_ibge']), safe='')}/dashboard",
            {"periodo": dashboard["periodo"]},
            "state",
        ),
        HttpProfile(
            "09_receita_arvore",
            "GET /entes/{cod_ibge}/receita/arvore",
            f"/entes/{quote(str(receita['cod_ibge']), safe='')}/receita/arvore",
            {"periodo": receita["periodo"]},
            "state",
        ),
        HttpProfile(
            "10_despesa_arvore",
            "GET /entes/{cod_ibge}/despesa/arvore",
            f"/entes/{quote(str(despesa['cod_ibge']), safe='')}/despesa/arvore",
            {"periodo": despesa["periodo"]},
            "state",
        ),
    ]


def _login(client: httpx.Client, *, email: str, password: str) -> str:
    response = client.post(
        "/auth/login",
        data={"username": email, "password": password},
    )
    if response.status_code != 200:
        raise RuntimeError(f"Login HTTP falhou com status {response.status_code}.")
    token = response.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Login HTTP nao devolveu access_token.")
    return token


def _request(
    client: httpx.Client,
    profile: HttpProfile,
    *,
    token: str,
) -> tuple[float, float, int]:
    started = time.perf_counter()
    response = client.get(
        profile.path,
        params=profile.params,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept-Encoding": "gzip",
            "Cache-Control": "no-cache",
        },
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    if response.status_code != 200:
        raise RuntimeError(f"{profile.name} devolveu HTTP {response.status_code}.")
    if not response.headers.get("etag", "").startswith('W/"'):
        raise RuntimeError(
            f"{profile.name} nao recebeu ETag; reinicie a API com o codigo da Sprint 27."
        )
    match = _APP_DURATION.search(response.headers.get("server-timing", ""))
    if match is None:
        raise RuntimeError(f"{profile.name} nao recebeu Server-Timing app;dur.")
    return elapsed_ms, float(match.group(1)), len(response.content)


def _measure(
    client: httpx.Client,
    profile: HttpProfile,
    *,
    token: str,
    warmup: int,
    runs: int,
) -> HttpResult:
    for _ in range(warmup):
        _request(client, profile, token=token)
    http_samples: list[float] = []
    app_samples: list[float] = []
    response_bytes = 0
    for _ in range(runs):
        http_ms, app_ms, response_bytes = _request(client, profile, token=token)
        http_samples.append(http_ms)
        app_samples.append(app_ms)
    return HttpResult(
        profile=profile,
        runs=runs,
        http_p50_ms=_nearest_rank(http_samples, 0.50),
        http_p95_ms=_nearest_rank(http_samples, 0.95),
        http_max_ms=max(http_samples),
        app_p50_ms=_nearest_rank(app_samples, 0.50),
        app_p95_ms=_nearest_rank(app_samples, 0.95),
        response_bytes=response_bytes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if args.runs <= 0 or args.warmup < 0 or args.timeout <= 0:
        parser.error("runs/timeout devem ser positivos e warmup nao pode ser negativo")

    credentials = {
        "state": (
            _required_env("SPRINT27_HTTP_STATE_EMAIL"),
            _required_env("SPRINT27_HTTP_STATE_PASSWORD"),
        ),
        "msc": (
            _required_env("SPRINT27_HTTP_MSC_EMAIL"),
            _required_env("SPRINT27_HTTP_MSC_PASSWORD"),
        ),
    }
    engine = create_engine(
        settings.database_admin_url,
        connect_args={"connect_timeout": 5},
        pool_pre_ping=True,
    )
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        profiles = _http_profiles(conn)
        conn.rollback()

    results: list[HttpResult] = []
    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        timeout=args.timeout,
        follow_redirects=False,
    ) as client:
        tokens = {
            key: _login(client, email=email, password=password)
            for key, (email, password) in credentials.items()
        }
        for profile in profiles:
            results.append(
                _measure(
                    client,
                    profile,
                    token=tokens[profile.credential],
                    warmup=args.warmup,
                    runs=args.runs,
                )
            )

    print("# Sprint 27 - authenticated HTTP latency")
    print()
    print("Credentials, tokens, DSN and concrete entity/query parameters are omitted.")
    print()
    print(
        "| Endpoint | Runs | HTTP P50 ms | HTTP P95 ms | App P50 ms | "
        "App P95 ms | Max ms | Bytes |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for result in results:
        print(
            f"| {result.profile.name} | {result.runs} | {result.http_p50_ms:.3f} | "
            f"{result.http_p95_ms:.3f} | {result.app_p50_ms:.3f} | "
            f"{result.app_p95_ms:.3f} | {result.http_max_ms:.3f} | "
            f"{result.response_bytes} |"
        )

    failures = [result for result in results if result.http_p95_ms >= 500]
    if failures:
        names = ", ".join(result.profile.name for result in failures)
        print(f"\nPerformance budget failed (HTTP P95 >= 500 ms): {names}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
