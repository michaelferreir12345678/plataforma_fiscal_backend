"""Utilidades vetoriais puras (sem I/O, sem SDK) para o recuperador RAG.

Aqui vivem: normalização/tokenização de texto, um *embedder* determinístico por
*hashing* (usado offline/em testes, sem rede) e as métricas de similaridade
(cosseno + sobreposição lexical). O armazenamento do embedding é portável (lista de
floats); em produção o mesmo contrato é servido por pgvector — a troca do backend de
similaridade é isolada no recuperador, não aqui.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata

# Dimensão do embedder local determinístico (hashing). Pequena por design: o corpo
# normativo (LRF/CF/MDF) é curado e pequeno, então a busca é O(n) e instantânea.
LOCAL_EMBED_DIM = 256
LOCAL_EMBED_MODEL = "local-hash-v1"

# Radicais sem valor discriminativo — não entram na tokenização.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "o", "as", "os", "um", "uma", "de", "do", "da", "dos", "das", "e", "ou",
        "que", "qual", "quais", "para", "por", "com", "sem", "no", "na", "nos", "nas",
        "em", "ao", "aos", "se", "sua", "seu", "suas", "seus", "meu", "minha", "the",
        "is", "are", "quanto", "quanta", "como", "onde", "quando",
        "sobre", "entre", "ser", "esta", "este", "isso", "há", "ha", "foi", "está",
    }
)


def strip_accents(text: str) -> str:
    """Remove acentos (NFKD) — deixa a comparação robusta a variações de digitação."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def tokenize(text: str) -> list[str]:
    """Minúsculas, sem acento, tokens alfanuméricos com 2+ caracteres, sem stopwords."""
    cleaned = strip_accents(text).lower()
    tokens = re.findall(r"[a-z0-9]{2,}", cleaned)
    return [tok for tok in tokens if tok not in _STOPWORDS]


def hash_embed(text: str, dim: int = LOCAL_EMBED_DIM) -> list[float]:
    """Embedding determinístico por *hashing* das palavras (bag-of-words assinado).

    Cada token é mapeado a um índice via SHA-1 e recebe sinal estável; o vetor é
    normalizado (L2). Cosseno entre dois vetores reflete a sobreposição de vocabulário —
    suficiente para ranquear dispositivos normativos por relevância sem rede.
    """
    vector = [0.0] * dim
    for token in tokenize(text):
        digest = hashlib.sha1(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign
    return l2_normalize(vector)


def l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        return vector
    return [component / norm for component in vector]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosseno entre dois vetores de mesma dimensão. Fora disso, retorna 0.0."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def lexical_overlap(query: str, document: str) -> float:
    """Sobreposição lexical (proporção de tokens da pergunta presentes no documento).

    Serve de rede de segurança: mesmo com embeddings de outro modelo/dimensão no banco,
    a recuperação de normas continua sensata (busca híbrida).
    """
    q_tokens = set(tokenize(query))
    if not q_tokens:
        return 0.0
    d_tokens = set(tokenize(document))
    if not d_tokens:
        return 0.0
    return len(q_tokens & d_tokens) / len(q_tokens)


def approx_tokens(text: str) -> int:
    """Estimativa barata de tokens (~1 token / 4 caracteres) para telemetria offline."""
    if not text:
        return 0
    return max(1, len(text) // 4)
