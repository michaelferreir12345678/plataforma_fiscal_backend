"""Clientes HTTP para as fontes de dados abertos.

Cada fonte tem sua natureza técnica: ORDS paginado (Tesouro), array simples (BCB/SGS) e
estruturas aninhadas (agregados v3/Pesquisas v1 do IBGE). A porta ``RecordsClient``
uniformiza tudo em ``list[dict]``;
o ``ClientResolver`` escolhe o cliente certo por ``fonte``. Nos testes, um cliente/resolver
falso evita a rede.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol
from urllib.parse import quote, urlencode

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings

SICONFI_BASE_URL = "https://apidatalake.tesouro.gov.br/ords/siconfi/"
SADIPEM_BASE_URL = "https://apidatalake.tesouro.gov.br/ords/cdwhprd/sadipem/tt/"
BCB_BASE_URL = "https://api.bcb.gov.br/"
IBGE_BASE_URL = "https://servicodados.ibge.gov.br/api/"
SIOPS_BASE_URL = settings.siops_base_url.rstrip("/") + "/"
SIOPE_BASE_URL = settings.siope_base_url.rstrip("/") + "/"
TESOURO_TRANSFERENCIAS_BASE_URL = settings.tesouro_transferencias_base_url.rstrip("/") + "/"


class _RespostaTransitoria(httpx.HTTPStatusError):
    """5xx ou 429: vale repetir. Continua sendo ``HTTPStatusError`` para quem trata acima."""


def recurso_ausente(exc: BaseException) -> bool:
    """O erro é a fonte dizendo "não tenho isso", e não um defeito nosso?

    Só **404**, e só quando o pedido foi por um recurso específico (ente/ano/período). Um
    5xx é indisponibilidade, um 401/403 é credencial, um 400 é pedido malformado — nenhum
    deles autoriza concluir que o dado não existe. Classificar erro como ausência é pior
    que falhar: transforma defeito em "sem dado" e ninguém investiga.
    """
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and not isinstance(exc, _RespostaTransitoria)
        and exc.response.status_code == 404
    )


class RecordsClient(Protocol):
    """Porta de acesso a dados: retorna todos os registros de um recurso."""

    def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...


class FileClient(Protocol):
    """Porta de download de arquivos (planilhas): retorna os bytes do arquivo."""

    def fetch(self, params: dict[str, Any]) -> bytes: ...


class RreoPdfClient(Protocol):
    """Porta para uma pagina anual e seus PDFs, preservando a sessao HTTP."""

    def fetch_page(self, url: str) -> str: ...

    def fetch_pdf(self, url: str, *, referer: str) -> bytes: ...


class ClientResolver(Protocol):
    """Resolve o cliente adequado (RecordsClient JSON ou FileClient) para uma ``fonte``."""

    def get(self, fonte: str) -> Any: ...


class _RateLimiter:
    """Limitador simples por intervalo mínimo entre chamadas (thread-safe)."""

    def __init__(self, max_per_second: float) -> None:
        self._min_interval = 1.0 / max_per_second if max_per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_allowed - now
            if sleep_for > 0:
                time.sleep(sleep_for)
                now = time.monotonic()
            self._next_allowed = now + self._min_interval


class _BaseHttpClient:
    """Base com httpx, rate-limit e backoff (tenacity)."""

    def __init__(
        self, base_url: str, *, max_per_second: float = 6.0, timeout: float = 60.0
    ) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._limiter = _RateLimiter(max_per_second)

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, _RespostaTransitoria)),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _get_json(self, path: str, params: dict[str, Any] | None) -> Any:
        self._limiter.wait()
        resp = self._client.get(path, params=params)
        # O comentário sempre disse "5xx é transitório, 4xx é definitivo", mas a política
        # de retry pegava **todo** HTTPStatusError: um 404 custava cinco requisições com
        # espera exponencial antes de falhar. Agora a distinção é real.
        if resp.status_code >= 500 or resp.status_code == 429:
            raise _RespostaTransitoria(
                f"{resp.status_code} em {resp.request.url}",
                request=resp.request,
                response=resp,
            )
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()


class OrdsRecordsClient(_BaseHttpClient):
    """Cliente ORDS (Tesouro): paginação ``items``/``hasMore``/``offset``."""

    def __init__(self, base_url: str, *, page_size: int = 500, **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)
        self._page_size = page_size

    def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._get_json(path, {**params, "limit": self._page_size, "offset": offset})
            items = page.get("items", []) if isinstance(page, dict) else []
            records.extend(items)
            if not (isinstance(page, dict) and page.get("hasMore")) or not items:
                break
            offset += len(items)
        return records


class SiconfiClient(OrdsRecordsClient):
    def __init__(self, base_url: str = SICONFI_BASE_URL, **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)


class SadipemClient(OrdsRecordsClient):
    def __init__(self, base_url: str = SADIPEM_BASE_URL, **kwargs: Any) -> None:
        # A API SADIPEM limita explicitamente o consumidor a uma requisição por segundo.
        # Ela entrega até 5.000 itens por página; usar esse tamanho reduz round-trips sem
        # contornar o rate-limit.
        kwargs.setdefault("max_per_second", 1.0)
        kwargs.setdefault("page_size", 5_000)
        super().__init__(base_url, **kwargs)


class BcbSgsClient(_BaseHttpClient):
    """Cliente do SGS/BCB: retorna o array JSON da série diretamente."""

    def __init__(self, base_url: str = BCB_BASE_URL, **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)

    def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        data = self._get_json(path, params)
        return list(data) if isinstance(data, list) else []


class JsonArrayRecordsClient(_BaseHttpClient):
    """Cliente para APIs que devolvem um array JSON na raiz."""

    def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        data = self._get_json(path, params)
        return list(data) if isinstance(data, list) else []


class JsonEnvelopeRecordsClient(_BaseHttpClient):
    """Cliente para APIs que envelopam as linhas em ``registros``."""

    def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        data = self._get_json(path, params)
        if not isinstance(data, dict):
            return []
        records = data.get("registros", [])
        return list(records) if isinstance(records, list) else []


class ODataRecordsClient(_BaseHttpClient):
    """Cliente OData v4 com paginacao por ``$top``/``$skip`` e ``nextLink``."""

    def __init__(self, base_url: str, *, page_size: int = 1_000, **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)
        self._page_size = page_size

    def _get_page(self, path: str, params: dict[str, Any]) -> Any:
        """Usa ``%20`` em vez de ``+``: o Olinda/FNDE rejeita ``+`` no ``$filter``."""
        if not params:
            return self._get_json(path, None)
        query = urlencode(params, doseq=True, quote_via=quote, safe="$@()',")
        separator = "&" if "?" in path else "?"
        return self._get_json(f"{path}{separator}{query}", None)

    def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        skip = int(params.get("$skip", 0))
        next_path = path
        next_params = {**params, "$top": self._page_size, "$skip": skip}
        following_next_link = False

        while True:
            page = self._get_page(next_path, next_params)
            items = page.get("value", []) if isinstance(page, dict) else []
            if not isinstance(items, list):
                break
            records.extend(items)

            next_link = page.get("@odata.nextLink") if isinstance(page, dict) else None
            if next_link:
                next_path = str(next_link)
                next_params = {}
                following_next_link = True
                continue
            if following_next_link or len(items) < self._page_size:
                break

            skip += len(items)
            next_path = path
            next_params = {**params, "$top": self._page_size, "$skip": skip}

        return records


class SiopsClient(JsonArrayRecordsClient):
    def __init__(self, base_url: str = SIOPS_BASE_URL, **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)


class SiopeClient(ODataRecordsClient):
    def __init__(self, base_url: str = SIOPE_BASE_URL, **kwargs: Any) -> None:
        # O servico Olinda/FNDE pode responder lentamente em consultas nacionais.
        kwargs.setdefault("timeout", 90.0)
        super().__init__(base_url, **kwargs)


class TesouroTransferenciasClient(JsonEnvelopeRecordsClient):
    def __init__(self, base_url: str = TESOURO_TRANSFERENCIAS_BASE_URL, **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)


class IbgeAgregadosClient(_BaseHttpClient):
    """Cliente IBGE: achata agregados v3 e indicadores leaf da API de Pesquisas v1."""

    def __init__(self, base_url: str = IBGE_BASE_URL, **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)

    def get_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        data = self._get_json(path, params)
        return self.flatten(data)

    @staticmethod
    def flatten(data: Any) -> list[dict[str, Any]]:
        """Normaliza os dois contratos JSON oficiais consumidos pelos conectores IBGE."""
        if isinstance(data, dict):
            return IbgeAgregadosClient.flatten_pesquisa(data)

        records: list[dict[str, Any]] = []
        if not isinstance(data, list):
            return records
        for variavel in data:
            if not isinstance(variavel, dict):
                continue
            # A API de Pesquisas envolve até mesmo um único indicador leaf em lista.
            if "res" in variavel and "resultados" not in variavel:
                records.extend(IbgeAgregadosClient.flatten_pesquisa(variavel))
                continue
            var_id = variavel.get("id")
            for resultado in variavel.get("resultados", []):
                for serie in resultado.get("series", []):
                    localidade = serie.get("localidade", {})
                    for ano, valor in (serie.get("serie") or {}).items():
                        records.append(
                            {
                                "variavel": var_id,
                                "cod_ibge": localidade.get("id"),
                                "ano": ano,
                                "valor": valor,
                            }
                        )
        return records

    @staticmethod
    def flatten_pesquisa(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Achata um indicador leaf v1: ``{id, res:[{localidade, res:{ano:valor}}]}``."""
        records: list[dict[str, Any]] = []
        indicador_id = data.get("id")
        resultados = data.get("res")
        if indicador_id is None or not isinstance(resultados, list):
            return records
        for resultado in resultados:
            if not isinstance(resultado, dict):
                continue
            serie = resultado.get("res")
            if not isinstance(serie, dict):
                continue
            for ano, valor in serie.items():
                records.append(
                    {
                        "variavel": str(indicador_id),
                        "cod_ibge": resultado.get("localidade"),
                        "ano": str(ano),
                        "valor": valor,
                    }
                )
        return records


class HttpFileClient(_BaseHttpClient):
    """Baixa um arquivo (planilha) via GET em ``params['url']`` e retorna os bytes.

    O timeout é maior que o dos clientes JSON porque estes arquivos são grandes: a planilha
    da CAPAG dos municípios tem ~24 MB, e 60 s derrubavam o download no meio.
    """

    #: Este cliente busca a URL que receber; quem sabe **onde** o arquivo está é o conector.
    #: A marca permite ao conector decidir se precisa consultar o catálogo da fonte — um
    #: cliente que já entrega o conteúdo (teste, cache local) não precisa de descoberta.
    requer_url = True

    def __init__(self, base_url: str = "", *, timeout: float = 300.0, **kwargs: Any) -> None:
        super().__init__(base_url, timeout=timeout, **kwargs)

    def fetch(self, params: dict[str, Any]) -> bytes:
        url = str(params.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            # Sem isto o httpx estoura em ``UnsupportedProtocol``, e o operador lê um erro
            # de biblioteca em vez do que de fato aconteceu: a fonte não foi configurada.
            raise ValueError(
                "Fonte de arquivo sem URL utilizável. Informe 'url' nos parâmetros da "
                "integração ou use uma fonte com descoberta automática de catálogo."
            )
        self._limiter.wait()
        resp = self._client.get(url, follow_redirects=True)
        resp.raise_for_status()
        return resp.content


class MunicipalRreoPdfClient:
    """Cliente para portais municipais RREO com cookie de sessao e ``Referer``.

    O portal de Fortaleza cria o cookie na pagina anual e exige que o download seja
    feito pela mesma sessao. O cache evita baixar a pagina seis vezes numa carga anual.
    """

    def __init__(self, *, max_per_second: float = 2.0, timeout: float = 90.0) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "plataforma-sefaz-fiscal/1.0 (+ingestao-dados-publicos)",
                "Accept-Language": "pt-BR,pt;q=0.9",
            },
        )
        self._limiter = _RateLimiter(max_per_second)
        self._page_cache: dict[str, str] = {}
        self._pdf_cache: dict[str, bytes] = {}

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _get(self, url: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        self._limiter.wait()
        response = self._client.get(url, headers=headers)
        response.raise_for_status()
        return response

    def fetch_page(self, url: str) -> str:
        cached = self._page_cache.get(url)
        if cached is not None:
            return cached
        response = self._get(url, headers={"Accept": "text/html,application/xhtml+xml"})
        html = response.text
        if not html.strip():
            raise ValueError(f"Pagina anual RREO vazia: {url}")
        self._page_cache[url] = html
        return html

    def fetch_pdf(self, url: str, *, referer: str) -> bytes:
        cached = self._pdf_cache.get(url)
        if cached is not None:
            return cached
        response = self._get(
            url,
            headers={
                "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
                "Referer": referer,
            },
        )
        content = response.content
        if not content.startswith(b"%PDF-"):
            raise ValueError(f"Download RREO nao retornou PDF: {url}")
        self._pdf_cache[url] = content
        return content

    def close(self) -> None:
        self._client.close()


# Fontes cujo cliente é baseado em download de arquivo (planilhas).
# FPM e transferências genéricas saíram daqui: passaram a usar a API oficial de
# Transferências Constitucionais, a mesma do FUNDEB. Eram planilhas sem URL configurada,
# e falhavam em toda execução.
FILE_FONTES = frozenset({"tesouro_capag"})


class RealClientResolver:
    """Resolver de produção: um cliente por família de fonte, criado sob demanda."""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def get(self, fonte: str) -> Any:
        # Precisa preceder o prefixo generico: esta fonte usa pagina/PDF e cookies,
        # nao o cliente ORDS do SICONFI.
        if fonte == "siconfi_rreo_minimos_pdf":
            return self._cached("rreo_minimos_pdf", MunicipalRreoPdfClient)
        if fonte.startswith("siconfi_"):
            return self._cached("siconfi", SiconfiClient)
        if fonte.startswith("sadipem_"):
            return self._cached("sadipem", SadipemClient)
        if fonte == "bcb":
            return self._cached("bcb", BcbSgsClient)
        if fonte.startswith("ibge_"):
            return self._cached("ibge", IbgeAgregadosClient)
        if fonte == "siops_saude":
            return self._cached("siops", SiopsClient)
        if fonte == "siope_educacao":
            return self._cached("siope", SiopeClient)
        if fonte in {"fnde_fundeb_repasse", "tesouro_fpm", "transferencia_generica"}:
            return self._cached("tesouro_transferencias", TesouroTransferenciasClient)
        if fonte in FILE_FONTES:
            return self._cached("file", HttpFileClient)
        raise ValueError(f"Sem cliente para a fonte '{fonte}'")

    def _cached(self, key: str, factory: type) -> Any:
        if key not in self._cache:
            self._cache[key] = factory()
        return self._cache[key]

    def close(self) -> None:
        for client in self._cache.values():
            close = getattr(client, "close", None)
            if callable(close):
                close()
        self._cache.clear()
