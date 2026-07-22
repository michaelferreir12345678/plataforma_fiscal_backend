"""Rastreabilidade (§6.3): toda resposta com número fiscal carrega ``source_ref``.

É requisito de produto (não opcional). Aqui fica o *value object* e um mixin para
respostas Pydantic que exigem a referência à fonte (relatório, anexo, período,
``versao_entrega``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

COMPOSITE_VERSION_PREFIX = "cmp:v1:"


class SourceRef(BaseModel):
    """Referência à fonte de um valor fiscal (§2.4, regra invariante de rastreabilidade)."""

    model_config = ConfigDict(frozen=True)

    relatorio: str = Field(description="Relatório de origem (ex.: RREO, RGF, DCA, MSC).")
    anexo: str | None = Field(default=None, description="Anexo/quadro do relatório.")
    periodo: str | None = Field(default=None, description="Período fiscal canônico.")
    versao_entrega: str | None = Field(
        default=None, description="Versão/entrega (retificação) que originou o valor."
    )


def composite_version_key(componentes: Sequence[SourceRef]) -> str:
    """Gera a chave estável de um valor derivado de duas ou mais entregas.

    A ordenação canônica torna a chave independente da ordem das fontes. O
    prefixo versiona o algoritmo para que uma futura mudança de canonicalização
    não altere silenciosamente a identidade das linhas históricas.
    """
    if len(componentes) < 2:
        raise ValueError("Uma versão composta exige ao menos duas fontes.")
    if any(not ref.versao_entrega for ref in componentes):
        raise ValueError("Toda fonte composta deve informar versao_entrega.")

    fontes_canonicas = sorted(
        json.dumps(
            ref.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        for ref in componentes
    )
    payload = f"[{','.join(fontes_canonicas)}]"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{COMPOSITE_VERSION_PREFIX}{digest}"


class WithSourceRef(BaseModel):
    """Mixin para respostas que devem carregar ``source_ref``."""

    source_ref: SourceRef | None = None
