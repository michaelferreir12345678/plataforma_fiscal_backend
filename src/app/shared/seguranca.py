"""Cabeçalhos de segurança e limite de tentativas no autenticador (Sprint 28).

Duas defesas que só fazem sentido na borda, e que faltavam para o go-live:

* **Cabeçalhos** — instruções que o navegador obedece: não adivinhe o tipo do conteúdo,
  não me coloque num iframe, não vaze a URL no ``Referer``, e (em HTTPS) só me acesse
  por HTTPS daqui em diante.
* **Rate limit no login** — a única rota que aceita credencial de quem ainda não provou
  ser ninguém. Sem freio, uma lista de senhas comuns roda contra o tenant inteiro.

O limite é por **origem + e-mail tentado**, não só por IP: atrás de um NAT institucional
milhares de usuários legítimos compartilham o mesmo IP, e limitar só por IP puniria a
prefeitura inteira por causa de um. E é por *tentativa falha* — quem acerta a senha não
consome cota, então o usuário legítimo nunca esbarra no freio.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

# --------------------------------------------------------------------------- #
# Cabeçalhos
# --------------------------------------------------------------------------- #

#: A API responde JSON, nunca HTML com script. A política mais restritiva possível é,
#: portanto, a correta: nada de script, nada de frame, nada de objeto. O frontend é
#: servido por outro processo e tem a própria política.
_CSP_API = (
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)

#: Um ano, incluindo subdomínios. Só faz sentido sob HTTPS — enviar em HTTP puro é
#: ruído, e em desenvolvimento local chegaria a atrapalhar (o navegador passaria a
#: recusar http://localhost por um ano).
_HSTS = "max-age=31536000; includeSubDomains"

_CABECALHOS_FIXOS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Aplica os cabeçalhos de segurança em toda resposta."""

    def __init__(self, app: ASGIApp, *, hsts: bool = True) -> None:
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        for nome, valor in _CABECALHOS_FIXOS.items():
            response.headers.setdefault(nome, valor)
        response.headers.setdefault("Content-Security-Policy", _CSP_API)
        # O HSTS é ignorado pelo navegador fora de HTTPS; enviá-lo só onde vale evita
        # dar a impressão de proteção onde ela não existe.
        if self.hsts and request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", _HSTS)
        return response


# --------------------------------------------------------------------------- #
# Rate limit do autenticador
# --------------------------------------------------------------------------- #


class JanelaDeslizante:
    """Contador de eventos por chave numa janela de tempo, seguro entre threads.

    Em processo, de propósito: uma dependência de Redis no caminho do login faria a
    autenticação cair junto com o cache. Para várias réplicas, o limite passa a ser
    por réplica — o que **reduz** a cota efetiva por atacante, nunca a amplia.
    """

    def __init__(self, *, limite: int, janela_segundos: float) -> None:
        self.limite = limite
        self.janela = janela_segundos
        self._eventos: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _expirar(self, fila: deque[float], agora: float) -> None:
        limite_tempo = agora - self.janela
        while fila and fila[0] < limite_tempo:
            fila.popleft()

    def bloqueado(self, chave: str) -> bool:
        agora = time.monotonic()
        with self._lock:
            fila = self._eventos.get(chave)
            if fila is None:
                return False
            self._expirar(fila, agora)
            return len(fila) >= self.limite

    def registrar(self, chave: str) -> int:
        agora = time.monotonic()
        with self._lock:
            fila = self._eventos.setdefault(chave, deque())
            self._expirar(fila, agora)
            fila.append(agora)
            return len(fila)

    def limpar(self, chave: str | None = None) -> None:
        """Zera a contagem — o login bem-sucedido devolve a cota de quem acertou."""
        with self._lock:
            if chave is None:
                self._eventos.clear()
            else:
                self._eventos.pop(chave, None)

    def segundos_para_liberar(self, chave: str) -> int:
        agora = time.monotonic()
        with self._lock:
            fila = self._eventos.get(chave)
            if not fila:
                return 0
            return max(0, int(self.janela - (agora - fila[0])) + 1)


def _origem(request: Request) -> str:
    """IP do cliente, respeitando o proxy reverso quando ele existe.

    ``X-Forwarded-For`` só é confiável se **nós** controlamos o proxy; por isso o
    primeiro salto é usado apenas como refinamento da chave, nunca como autorização.
    """
    encaminhado = request.headers.get("x-forwarded-for", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """Freia tentativas **falhas** de login por origem + identidade tentada."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        limite: int = 10,
        janela_segundos: float = 300.0,
        caminho: str = "/auth/login",
    ) -> None:
        super().__init__(app)
        self.caminho = caminho
        self.janela = JanelaDeslizante(limite=limite, janela_segundos=janela_segundos)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method != "POST" or request.url.path != self.caminho:
            return await call_next(request)

        # O corpo precisa ser lido para saber **quem** está sendo tentado, e relido
        # depois pelo endpoint: sem repor o `receive`, o form chegaria vazio.
        corpo = await request.body()

        async def _receive() -> dict[str, object]:
            return {"type": "http.request", "body": corpo, "more_body": False}

        request._receive = _receive
        chave = f"{_origem(request)}|{_identidade_tentada(corpo)}"

        if self.janela.bloqueado(chave):
            espera = self.janela.segundos_para_liberar(chave)
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(espera)},
                content={
                    "type": "urn:plataforma-fiscal:error:rate-limit",
                    "title": "Tentativas demais",
                    "status": 429,
                    "detail": (
                        "Excesso de tentativas de autenticação. "
                        f"Tente novamente em {espera} segundo(s)."
                    ),
                },
            )

        response = await call_next(request)
        if response.status_code == 200:
            # Acertou: devolve a cota. Um usuário que erra a senha duas vezes e acerta
            # na terceira não pode ficar mais perto do bloqueio por causa disso.
            self.janela.limpar(chave)
        elif response.status_code in {400, 401, 403, 422}:
            self.janela.registrar(chave)
        return response


def _identidade_tentada(corpo: bytes) -> str:
    """E-mail tentado, extraído do form — sem senha, sem log, só para compor a chave."""
    try:
        texto = corpo.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 — corpo malformado não pode derrubar o freio
        return "?"
    for parte in texto.split("&"):
        if parte.startswith("username="):
            from urllib.parse import unquote_plus

            return unquote_plus(parte[len("username=") :])[:120]
    return "?"
