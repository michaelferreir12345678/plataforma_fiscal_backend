"""Guarda de rastreabilidade (G4/§6.3): número fiscal sem ``source_ref`` não sai.

O `CLAUDE.md` §6.3 diz que toda resposta com número fiscal carrega ``source_ref``. Até
aqui isso era **convenção**: cada serviço lembrava (ou esquecia) de preencher o campo, e a
Sprint E1 (A26) encontrou dois lugares onde não lembrava. Numa camada que serve um modelo
de linguagem o custo do esquecimento sobe: o número entra na prosa e sai de lá sem
proveniência, com aparência de fato conferido.

Por isso a verificação aqui é **estrutural** e roda em toda chamada, sobre o payload já
serializado: percorre a árvore e exige que todo número esteja coberto por um
``source_ref`` no próprio objeto ou num ancestral. Não há como uma ferramenta nova
"esquecer" — no máximo ela falha em teste, que é onde se quer que falhe.

Só ficam de fora as chaves **estruturais** — contadores, paginação e cronometria —, que
descrevem a resposta e não o fisco. A lista é curta e fechada de propósito: cada nome novo
nela é uma exceção à rastreabilidade e precisa ser defendida em revisão.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

#: Chaves cujo valor numérico **não** é número fiscal: descrevem o envelope, não o dado.
CHAVES_ESTRUTURAIS: frozenset[str] = frozenset(
    {
        "total",
        "total_arestas",
        "pagina",
        "por_pagina",
        "page",
        "page_size",
        "nivel",
        "depth",
        "duracao_ms",
        "latencia_ms",
        "linhas",
        "score",
        "dim",
    }
)

#: Chaves que **carregam** a fonte (e por isso não são inspecionadas como dado).
CHAVES_DE_FONTE: frozenset[str] = frozenset({"source_ref", "source_refs"})


def _e_numero(valor: Any) -> bool:
    # ``bool`` é subclasse de ``int`` em Python: um ``disponivel: false`` não é número.
    return isinstance(valor, int | float | Decimal) and not isinstance(valor, bool)


def _tem_fonte(objeto: dict[str, Any]) -> bool:
    """Um ``source_ref`` preenchido (com relatório) cobre o objeto e seus descendentes."""
    ref = objeto.get("source_ref")
    if isinstance(ref, dict) and ref.get("relatorio"):
        return True
    refs = objeto.get("source_refs")
    return isinstance(refs, list) and any(
        isinstance(r, dict) and r.get("relatorio") for r in refs
    )


def numeros_sem_fonte(payload: Any, *, caminho: str = "") -> list[str]:
    """Caminhos dos números fiscais que nenhum ``source_ref`` cobre.

    Lista vazia ⇒ a saída respeita a §6.3. A cobertura é herdada: basta um ``source_ref``
    na raiz para cobrir a árvore inteira, que é o formato natural de uma resposta de
    indicador; listas em que cada item tem fonte própria também funcionam.
    """
    return _percorrer(payload, coberto=False, caminho=caminho or "$")


def _percorrer(valor: Any, *, coberto: bool, caminho: str) -> list[str]:
    faltantes: list[str] = []
    if isinstance(valor, dict):
        coberto_aqui = coberto or _tem_fonte(valor)
        for chave, filho in valor.items():
            if chave in CHAVES_DE_FONTE or chave in CHAVES_ESTRUTURAIS:
                continue
            sub = f"{caminho}.{chave}"
            if _e_numero(filho):
                if not coberto_aqui:
                    faltantes.append(sub)
            else:
                faltantes.extend(_percorrer(filho, coberto=coberto_aqui, caminho=sub))
    elif isinstance(valor, list):
        for indice, item in enumerate(valor):
            sub = f"{caminho}[{indice}]"
            if _e_numero(item):
                if not coberto:
                    faltantes.append(sub)
            else:
                faltantes.extend(_percorrer(item, coberto=coberto, caminho=sub))
    return faltantes
