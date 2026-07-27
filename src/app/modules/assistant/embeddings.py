"""Embedder do *vector store* normativo — porta + adaptadores.

O corpo normativo (``gold.norma_chunk``) é indexado por um *embedder* estável. Em
produção usa-se o Gemini (``gemini-embedding-001``); offline/em testes usa-se um
*embedder* determinístico por *hashing* (sem rede). O *embedder* de indexação é
**separado** do provedor de chat: assim os testes podem injetar um LLM falso sem
precisar simular embeddings, e a recuperação de normas permanece determinística.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol, runtime_checkable

from app.core.config import Settings, get_settings
from app.modules.assistant import vectors
from app.modules.assistant.llm import LLMProviderError, gemini_sdk_available


@runtime_checkable
class Embedder(Protocol):
    """Porta de *embedding* do corpo normativo."""

    model_id: str
    dim: int

    def embed(self, textos: list[str]) -> list[list[float]]:
        """Vetores L2-normalizados, um por texto (mesma ordem)."""
        ...


class LocalHashEmbedder:
    """Embedder determinístico por *hashing* (offline, sem rede) — default de dev/test."""

    def __init__(self, dim: int = vectors.LOCAL_EMBED_DIM) -> None:
        self.model_id = vectors.LOCAL_EMBED_MODEL
        self.dim = dim

    def embed(self, textos: list[str]) -> list[list[float]]:
        return [vectors.hash_embed(texto, self.dim) for texto in textos]


class GeminiEmbedder:
    """Adaptador Gemini (``gemini-embedding-001``) — importa o SDK preguiçosamente."""

    def __init__(self, *, api_key: str, model: str, dim: int = 3072) -> None:
        self._api_key = api_key
        self.model_id = model
        self.dim = dim
        self._client: object | None = None  # inicializado sob demanda

    def _ensure_client(self) -> object:
        if self._client is None:
            try:
                from google import genai  # importado só quando há chave configurada
            except ImportError as exc:  # pragma: no cover - depende do ambiente
                raise LLMProviderError(
                    detail="SDK google-genai não instalado; configure ASSISTANT_PROVIDER=local."
                ) from exc
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def embed(self, textos: list[str]) -> list[list[float]]:
        client = self._ensure_client()
        try:
            result = client.models.embed_content(model=self.model_id, contents=textos)  # type: ignore[attr-defined]
            vetores = [list(item.values) for item in result.embeddings]
        except LLMProviderError:
            raise
        except Exception as exc:  # pragma: no cover - rede/credencial
            raise LLMProviderError(
                detail=f"Falha ao gerar embeddings no Gemini: {exc}"
            ) from exc
        return [vectors.l2_normalize(vetor) for vetor in vetores]


def build_embedder(settings: Settings) -> Embedder:
    """Escolhe o *embedder* do vector store, independente do provedor de chat.

    ``ASSISTANT_EMBEDDING_BACKEND=gemini`` usa ``gemini-embedding-001`` (produção/pgvector),
    desde que haja chave e SDK; o default ``local`` mantém o store determinístico e offline.
    """
    if (
        settings.assistant_embedding_backend.lower() == "gemini"
        and settings.gemini_api_key
        and gemini_sdk_available()
    ):
        return GeminiEmbedder(
            api_key=settings.gemini_api_key, model=settings.assistant_embedding_model
        )
    return LocalHashEmbedder()


@lru_cache
def get_embedder() -> Embedder:
    """Embedder cacheado do processo (indexação e consulta usam o mesmo)."""
    return build_embedder(get_settings())
