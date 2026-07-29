"""Sprint 27: cache validators, compression and the analytical latency budget."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import StreamingResponse

from app.core.config import Settings
from app.modules.accounting.service import (
    Contexto,
    MscMes,
    _contexto_cache_get,
    _contexto_cache_put,
)
from app.modules.dashboard.carteira_service import _entes_cache_get, _entes_cache_put
from app.modules.dashboard.estadual_service import (
    _ranking_cache_get,
    _ranking_cache_put,
)
from app.modules.ingestion.cobertura import (
    _BULK_UPSERT_SIZE,
    _resolve_ingestion_time,
    _upsert_many,
)
from app.modules.ingestion.service import (
    _cobertura_cache_get,
    _cobertura_cache_put,
)
from app.shared.http_performance import (
    GoldHttpOptimizationMiddleware,
    PerformanceRecorder,
    if_none_match_matches,
)


def _contract_app() -> FastAPI:
    app = FastAPI()

    @app.get("/entes/{cod_ibge}/dashboard")
    def analytical_page(
        cod_ibge: str,
        response: Response,
        authorization: str | None = Header(None),
    ) -> dict[str, object]:
        if not authorization:
            raise HTTPException(status_code=401, detail="missing bearer token")
        response.headers["X-Contract-Header"] = "preserved"
        return {
            "cod_ibge": cod_ibge,
            "rows": [
                {"codigo": str(index), "descricao": "dado fiscal " * 20}
                for index in range(30)
            ],
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/relatorios/{relatorio_id}/arquivo")
    def report_file(relatorio_id: str) -> StreamingResponse:
        chunks = iter([f"{relatorio_id}:".encode(), b"x" * 2_000])
        return StreamingResponse(chunks, media_type="application/octet-stream")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://frontend.example"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.add_middleware(
        GoldHttpOptimizationMiddleware,
        budget_ms=500,
        sample_window=32,
    )
    app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)
    return app


def _vary(response_header: str) -> set[str]:
    return {value.strip().casefold() for value in response_header.split(",")}


def test_gold_get_contract_200_then_304_is_private() -> None:
    with TestClient(_contract_app()) as client:
        headers = {
            "Authorization": "Bearer tenant-a",
            "Accept-Encoding": "identity",
            "Origin": "https://frontend.example",
        }
        first = client.get("/entes/2304400/dashboard", headers=headers)

        assert first.status_code == 200
        assert first.headers["etag"].startswith('W/"')
        assert first.headers["cache-control"] == (
            "private, no-cache, max-age=0, must-revalidate"
        )
        assert _vary(first.headers["vary"]) == {
            "authorization",
            "cookie",
            "accept-encoding",
            "origin",
        }
        assert first.headers["x-performance-budget-ms"] == "500"
        assert first.headers["server-timing"].startswith("app;dur=")
        assert "app_p95;dur=" in first.headers["server-timing"]
        assert float(first.headers["x-performance-p95-ms"]) >= 0
        assert first.headers["x-performance-samples"] == "1"

        conditional = client.get(
            "/entes/2304400/dashboard",
            headers={**headers, "If-None-Match": first.headers["etag"]},
        )

        assert conditional.status_code == 304
        assert conditional.content == b""
        assert conditional.headers["etag"] == first.headers["etag"]
        assert conditional.headers["cache-control"].startswith("private, no-cache")
        assert conditional.headers["access-control-allow-origin"] == (
            "https://frontend.example"
        )
        assert conditional.headers["x-contract-header"] == "preserved"
        assert _vary(conditional.headers["vary"]) == {
            "authorization",
            "cookie",
            "accept-encoding",
            "origin",
        }
        assert conditional.headers["server-timing"].startswith("app;dur=")
        assert conditional.headers["x-performance-samples"] == "2"
        assert "content-length" not in conditional.headers
        assert "content-type" not in conditional.headers
        assert "content-encoding" not in conditional.headers


def test_etag_is_partitioned_by_authentication_scope() -> None:
    with TestClient(_contract_app()) as client:
        first = client.get(
            "/entes/2304400/dashboard",
            headers={
                "Authorization": "Bearer tenant-a",
                "Accept-Encoding": "identity",
            },
        )
        other_principal = client.get(
            "/entes/2304400/dashboard",
            headers={
                "Authorization": "Bearer tenant-b",
                "Accept-Encoding": "identity",
                "If-None-Match": first.headers["etag"],
            },
        )

        assert first.status_code == 200
        assert other_principal.status_code == 200
        assert other_principal.json() == first.json()
        assert other_principal.headers["etag"] != first.headers["etag"]


def test_gold_json_is_compressed_and_remains_decodable() -> None:
    with TestClient(_contract_app()) as client:
        response = client.get(
            "/entes/2304400/dashboard",
            headers={
                "Authorization": "Bearer tenant-a",
                "Accept-Encoding": "gzip",
            },
        )

        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        assert "accept-encoding" in _vary(response.headers["vary"])
        assert len(response.json()["rows"]) == 30


def test_non_gold_get_does_not_receive_private_validator_or_timing() -> None:
    with TestClient(_contract_app()) as client:
        response = client.get("/health", headers={"Accept-Encoding": "identity"})

    assert response.status_code == 200
    assert "etag" not in response.headers
    assert "server-timing" not in response.headers
    assert "x-performance-budget-ms" not in response.headers
    assert "x-performance-p95-ms" not in response.headers


def test_unmatched_gold_like_path_does_not_create_unbounded_metric_key() -> None:
    with TestClient(_contract_app()) as client:
        response = client.get(
            "/entes/attacker-controlled-unmatched-path",
            headers={"Accept-Encoding": "identity"},
        )

    assert response.status_code == 404
    assert "etag" not in response.headers
    assert "server-timing" not in response.headers
    assert "x-performance-samples" not in response.headers


def test_failed_auth_is_not_cacheable_or_convertible_to_304() -> None:
    with TestClient(_contract_app()) as client:
        response = client.get(
            "/entes/2304400/dashboard",
            headers={
                "Accept-Encoding": "identity",
                "If-None-Match": "*",
            },
        )

    assert response.status_code == 401
    assert "etag" not in response.headers
    assert "cache-control" not in response.headers


def test_file_stream_is_not_consumed_by_etag_middleware() -> None:
    with TestClient(_contract_app()) as client:
        response = client.get(
            "/relatorios/report-1/arquivo",
            headers={"Accept-Encoding": "identity"},
        )

    assert response.status_code == 200
    assert response.content == b"report-1:" + (b"x" * 2_000)
    assert "etag" not in response.headers
    assert "server-timing" not in response.headers


def test_if_none_match_uses_weak_comparison_and_wildcard() -> None:
    etag = 'W/"abc123"'

    assert if_none_match_matches('W/"other", "abc123"', etag)
    assert if_none_match_matches("*", etag)
    assert not if_none_match_matches('"other"', etag)
    assert not if_none_match_matches(None, etag)


def test_performance_recorder_reports_nearest_rank_p95_and_bounds_window() -> None:
    recorder = PerformanceRecorder(budget_ms=500, window_size=5)
    route = "GET /entes/{cod_ibge}/dashboard"
    for duration in (100, 200, 300, 400, 450):
        snapshot = recorder.observe(route, duration)

    assert snapshot.count == 5
    assert snapshot.p95_ms == 450
    assert snapshot.within_budget

    # The sixth value evicts 100; the bounded window now exceeds the P95 budget.
    snapshot = recorder.observe(route, 600)
    assert snapshot.count == 5
    assert snapshot.p95_ms == 600
    assert not snapshot.within_budget


def test_http_performance_settings_reject_unsafe_bounds() -> None:
    invalid_values = (
        {"http_gzip_minimum_size": -1},
        {"http_gzip_compresslevel": 10},
        {"http_performance_budget_ms": 0},
        {"http_performance_sample_window": 0},
    )

    for values in invalid_values:
        try:
            Settings(_env_file=None, **values)
        except ValidationError:
            continue
        raise AssertionError(f"Settings accepted invalid HTTP options: {values}")


def test_msc_context_cache_is_partitioned_by_gold_version_identity() -> None:
    cod_ibge = "9999991"
    ano = 2099
    identity_v1 = tuple(
        ("MSC", f"{ano}-M{month:02d}", "v1")
        for month in range(1, 7)
    )
    identity_v2 = (*identity_v1[:-1], ("MSC", f"{ano}-M06", "v2"))
    contexto = Contexto(
        cod_ibge=cod_ibge,
        uf="ZZ",
        ano=ano,
        esfera="municipal",
        dca_versao=None,
        msc_meses=[
            MscMes(periodo=periodo, mes=index, versao=versao)
            for index, (_relatorio, periodo, versao) in enumerate(identity_v1, start=1)
        ],
    )

    _contexto_cache_put(contexto, identity_v1)

    assert _contexto_cache_get(cod_ibge, ano, identity_v1) is contexto
    assert _contexto_cache_get(cod_ibge, ano, identity_v2) is None


def test_large_page_caches_hit_and_invalidate_when_data_identity_changes() -> None:
    response = object()

    ranking_identity_v1 = (("9999991", "v1", "hash-v1"),)
    ranking_identity_v2 = (("9999991", "v2", "hash-v2"),)
    ranking_key_v1 = (
        "99",
        "rcl",
        "2099-B1",
        None,
        None,
        "valor",
        ("9999991",),
        ranking_identity_v1,
    )
    ranking_key_v2 = (*ranking_key_v1[:-1], ranking_identity_v2)
    _ranking_cache_put(ranking_key_v1, response)  # type: ignore[arg-type]
    assert _ranking_cache_get(ranking_key_v1) is response
    assert _ranking_cache_get(ranking_key_v2) is None

    carteira_key_v1 = (
        "org-sprint-27",
        "2099-B1",
        1,
        200,
        "risco",
        None,
        None,
        None,
        ("9999991",),
        (("9999991", "Ente", "ZZ", "Região", 1_000),),
        (("9999991", "grupo", "tag"),),
        (1, None),
    )
    carteira_key_v2 = (*carteira_key_v1[:-1], (2, None))
    _entes_cache_put(carteira_key_v1, response)  # type: ignore[arg-type]
    assert _entes_cache_get(carteira_key_v1) is response
    assert _entes_cache_get(carteira_key_v2) is None

    cobertura_key_v1 = ("siconfi_rreo", "ZZ", 2099, 1, 200, (1_000, None))
    cobertura_key_v2 = (*cobertura_key_v1[:-1], (1_001, None))
    _cobertura_cache_put(cobertura_key_v1, response)  # type: ignore[arg-type]
    assert _cobertura_cache_get(cobertura_key_v1) is response
    assert _cobertura_cache_get(cobertura_key_v2) is None


def test_cobertura_refresh_resolves_ingestion_once_and_batches_writes() -> None:
    earlier = datetime(2099, 1, 1, tzinfo=UTC)
    later = datetime(2099, 1, 2, tzinfo=UTC)
    times = {
        ("siconfi_rreo", "9999991", "2099-B1", "v1"): later,
        ("siconfi_rreo", "BR", "2099-B1", "v1"): earlier,
    }

    assert _resolve_ingestion_time(
        times,
        fonte="siconfi_rreo",
        cod_ibge="9999991",
        periodo="2099-B1",
        versao="v1",
    ) == earlier
    assert (
        _resolve_ingestion_time(
            times,
            fonte="siconfi_rreo",
            cod_ibge="9999992",
            periodo="2099-B2",
            versao="v1",
        )
        is None
    )

    class FakeSession:
        def __init__(self) -> None:
            self.execute_calls = 0

        def execute(self, _statement: object) -> None:
            self.execute_calls += 1

    fake_session = FakeSession()
    now = datetime(2099, 1, 3, tzinfo=UTC)
    values = [
        {
            "fonte": "siconfi_rreo",
            "cod_ibge": f"{index:07d}",
            "periodo": "2099-B1",
            "uf": "99",
            "ano": 2099,
            "n_registros": 1,
            "versao_entrega_vigente": "v1",
            "ingerido_em": earlier,
            "defasagem_periodos": 0,
            "atualizado_em": now,
        }
        for index in range((_BULK_UPSERT_SIZE * 2) + 1)
    ]

    _upsert_many(fake_session, values)  # type: ignore[arg-type]

    assert fake_session.execute_calls == 3
