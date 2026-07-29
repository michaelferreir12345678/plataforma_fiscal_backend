"""HTTP cache validators and latency budget for analytical GET endpoints.

Gold data is shared, but access to an entity is authorized in the tenant context.
Validators therefore remain private and include an authentication-scope salt. A
validator produced for one principal cannot turn another principal's request into
``304 Not Modified`` merely because both responses contain the same bytes.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from collections import deque
from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import cast

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

DEFAULT_PERFORMANCE_BUDGET_MS = 500.0
DEFAULT_SAMPLE_WINDOW = 256

# Existing analytical/page endpoints backed predominantly by ``gold``. Operational
# resources such as users, reports, jobs and saved scenarios are intentionally absent.
_GOLD_ROUTE_PREFIXES = (
    "/entes/",
    "/benchmark",
    "/uf/",
    "/geo/",
)
_GOLD_ROUTE_PATHS = frozenset(
    {
        "/entes",
        "/periodos",
        "/carteira/resumo",
        "/carteira/entes",
        "/carteira/mapa",
        "/admin/ingestion/status",
        "/admin/ingestion/fontes",
        "/admin/ingestion/cobertura",
        "/admin/ingestion/data",
        "/admin/ingestion/retificacoes",
        "/admin/qualidade",
        "/admin/lineage",
    }
)


@dataclass(frozen=True, slots=True)
class PerformanceSnapshot:
    """Rolling latency summary for one parameterized API route."""

    route: str
    count: int
    p95_ms: float
    budget_ms: float

    @property
    def within_budget(self) -> bool:
        return self.p95_ms < self.budget_ms


class PerformanceRecorder:
    """Thread-safe, bounded in-process latency window.

    This is lightweight request instrumentation, not a metrics backend. Production
    log/metrics collectors can aggregate the parameterized route and P95 without the
    API exposing a new endpoint or logging tenant/entity identifiers.
    """

    def __init__(
        self,
        *,
        budget_ms: float = DEFAULT_PERFORMANCE_BUDGET_MS,
        window_size: int = DEFAULT_SAMPLE_WINDOW,
    ) -> None:
        if budget_ms <= 0:
            raise ValueError("budget_ms must be positive")
        if window_size <= 0:
            raise ValueError("window_size must be positive")
        self.budget_ms = float(budget_ms)
        self.window_size = int(window_size)
        self._samples: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _snapshot(
        route: str,
        samples: list[float],
        budget_ms: float,
    ) -> PerformanceSnapshot:
        ordered = sorted(samples)
        rank = max(0, math.ceil(len(ordered) * 0.95) - 1)
        return PerformanceSnapshot(
            route=route,
            count=len(ordered),
            p95_ms=ordered[rank],
            budget_ms=budget_ms,
        )

    def observe(self, route: str, duration_ms: float) -> PerformanceSnapshot:
        with self._lock:
            samples = self._samples.get(route)
            if samples is None:
                samples = deque(maxlen=self.window_size)
                self._samples[route] = samples
            samples.append(float(duration_ms))
            return self._snapshot(route, list(samples), self.budget_ms)

    def snapshot(self, route: str) -> PerformanceSnapshot | None:
        with self._lock:
            samples = self._samples.get(route)
            if not samples:
                return None
            return self._snapshot(route, list(samples), self.budget_ms)

    def reset(self) -> None:
        """Clear process-local observations (primarily useful for tests)."""

        with self._lock:
            self._samples.clear()


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return str(getattr(route, "path", request.url.path))


def is_heavy_gold_get(request: Request) -> bool:
    """Return whether the routed request is an existing analytical GET."""

    if request.method != "GET":
        return False
    # Starlette only publishes ``scope["route"]`` after a route was matched. Do
    # not classify a 404 from an attacker-controlled ``/entes/<anything>`` by its
    # raw URL: doing so would create an unbounded recorder key per unknown path.
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if not isinstance(route_path, str):
        return False
    return route_path in _GOLD_ROUTE_PATHS or route_path.startswith(_GOLD_ROUTE_PREFIXES)


def _merge_vary(response: Response, *names: str) -> None:
    values = [value.strip() for value in response.headers.get("vary", "").split(",")]
    values = [value for value in values if value]
    known = {value.casefold() for value in values}
    for name in names:
        if name.casefold() not in known:
            values.append(name)
            known.add(name.casefold())
    response.headers["Vary"] = ", ".join(values)


def _principal_scope(request: Request) -> bytes:
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        return (
            f"principal:{principal.org_id or '-'}:{principal.usuario_id}:"
            f"{','.join(sorted(principal.capacidades))}"
        ).encode()
    # Authentication normally publishes Principal. The header/cookie fallback keeps
    # custom deployments and isolated middleware tests equally partitioned.
    authorization = request.headers.get("authorization", "")
    cookie = request.headers.get("cookie", "")
    return f"headers:{authorization}\0{cookie}".encode()


def _etag_for(request: Request, body: bytes) -> str:
    digest = hashlib.blake2b(digest_size=16)
    digest.update(request.method.encode())
    digest.update(b"\0")
    digest.update(request.url.path.encode())
    digest.update(b"?")
    digest.update(request.url.query.encode())
    digest.update(b"\0")
    digest.update(_principal_scope(request))
    digest.update(b"\0")
    digest.update(body)
    # Weak is intentional: the same JSON representation may subsequently be gzip
    # encoded by the outer middleware without invalidating conditional GET semantics.
    return f'W/"{digest.hexdigest()}"'


def _opaque_etag(value: str) -> str | None:
    candidate = value.strip()
    if candidate[:2].casefold() == "w/":
        candidate = candidate[2:].strip()
    if len(candidate) < 2 or candidate[0] != '"' or candidate[-1] != '"':
        return None
    return candidate[1:-1]


def if_none_match_matches(header_value: str | None, response_etag: str) -> bool:
    """Apply RFC weak comparison for ``If-None-Match`` on GET."""

    if not header_value:
        return False
    expected = _opaque_etag(response_etag)
    if expected is None:
        return False
    for candidate in header_value.split(","):
        if candidate.strip() == "*":
            return True
        if _opaque_etag(candidate) == expected:
            return True
    return False


def _is_json(response: Response) -> bool:
    media_type = response.headers.get("content-type", "").partition(";")[0].strip().casefold()
    return media_type == "application/json" or media_type.endswith("+json")


async def _consume_body(response: Response) -> bytes:
    iterator = cast(
        AsyncIterable[bytes] | None,
        getattr(response, "body_iterator", None),
    )
    if iterator is None:
        return bytes(getattr(response, "body", b""))
    chunks = [bytes(chunk) async for chunk in iterator]
    return b"".join(chunks)


def _rebuild_response(response: Response, body: bytes) -> Response:
    rebuilt = Response(
        content=body,
        status_code=response.status_code,
        background=response.background,
    )
    # Preserve duplicate headers (notably Set-Cookie) and the original media type.
    rebuilt.raw_headers = list(response.raw_headers)
    return rebuilt


class GoldHttpOptimizationMiddleware(BaseHTTPMiddleware):
    """ETag/private revalidation plus rolling P95 instrumentation for gold GETs."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        budget_ms: float = DEFAULT_PERFORMANCE_BUDGET_MS,
        sample_window: int = DEFAULT_SAMPLE_WINDOW,
        recorder: PerformanceRecorder | None = None,
    ) -> None:
        super().__init__(app)
        self.recorder = recorder or PerformanceRecorder(
            budget_ms=budget_ms,
            window_size=sample_window,
        )

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        if not is_heavy_gold_get(request):
            return response

        body = await _consume_body(response)
        optimized = _rebuild_response(response, body)

        if response.status_code == 200 and _is_json(response):
            etag = _etag_for(request, body)
            optimized.headers["ETag"] = etag
            optimized.headers["Cache-Control"] = "private, no-cache, max-age=0, must-revalidate"
            _merge_vary(optimized, "Authorization", "Cookie", "Accept-Encoding")
            if if_none_match_matches(request.headers.get("if-none-match"), etag):
                not_modified = Response(status_code=304, background=optimized.background)
                # Keep cache/security/CORS metadata produced by inner middleware, but
                # never carry representation framing to a bodyless 304.
                excluded = {
                    b"content-length",
                    b"content-type",
                    b"content-encoding",
                    b"content-range",
                    b"transfer-encoding",
                }
                not_modified.raw_headers = [
                    (name, value)
                    for name, value in optimized.raw_headers
                    if name.lower() not in excluded
                ]
                optimized = not_modified

        duration_ms = (time.perf_counter() - started) * 1000
        route = f"{request.method} {_route_template(request)}"
        snapshot = self.recorder.observe(route, duration_ms)
        optimized.headers["Server-Timing"] = (
            f'app;dur={duration_ms:.2f}, app_p95;dur={snapshot.p95_ms:.2f};'
            f'desc="rolling {snapshot.count}"'
        )
        optimized.headers["X-Performance-Budget-Ms"] = f"{self.recorder.budget_ms:g}"
        optimized.headers["X-Performance-P95-Ms"] = f"{snapshot.p95_ms:.2f}"
        optimized.headers["X-Performance-Samples"] = str(snapshot.count)
        if not snapshot.within_budget:
            logger.warning(
                "performance_budget_exceeded route=%s duration_ms=%.2f p95_ms=%.2f "
                "budget_ms=%.2f samples=%d",
                route,
                duration_ms,
                snapshot.p95_ms,
                snapshot.budget_ms,
                snapshot.count,
            )
        return optimized
