"""Aritmética de período fiscal canônico (§6.6) — fonte única.

Períodos da plataforma: ``2024`` (anual), ``2024-B6`` (bimestre), ``2024-Q3``
(quadrimestre), ``2024-S1`` (semestre) e ``2024-M07`` (mês). O cockpit precisa navegar
"período anterior" e "mesmo período do exercício anterior" sem reimplementar isso em cada
módulo — daí este utilitário compartilhado.
"""

from __future__ import annotations

import re

_RE = re.compile(r"^(\d{4})(?:-([BQSM])(\d{1,2}))?$")
# Quantos períodos daquele tipo cabem num exercício.
POR_ANO = {"B": 6, "Q": 3, "S": 2, "M": 12}


def parse(periodo: str) -> tuple[int, str | None, int | None]:
    """``2024-B6`` → (2024, 'B', 6); ``2024`` → (2024, None, None). Inválido ⇒ ValueError."""
    m = _RE.match((periodo or "").strip())
    if m is None:
        raise ValueError(f"Período fora do padrão canônico: {periodo!r}")
    ano, tipo, num = m.group(1), m.group(2), m.group(3)
    return int(ano), tipo, int(num) if num is not None else None


def formatar(ano: int, tipo: str | None, num: int | None) -> str:
    if tipo is None or num is None:
        return str(ano)
    if tipo == "M":
        return f"{ano}-M{num:02d}"
    return f"{ano}-{tipo}{num}"


def anterior(periodo: str) -> str | None:
    """Período imediatamente anterior (atravessa o exercício). ``None`` se indefinido."""
    try:
        ano, tipo, num = parse(periodo)
    except ValueError:
        return None
    if tipo is None or num is None:
        return str(ano - 1)
    if num > 1:
        return formatar(ano, tipo, num - 1)
    return formatar(ano - 1, tipo, POR_ANO[tipo])


def mesmo_periodo_exercicio_anterior(periodo: str) -> str | None:
    """Mesmo período do exercício anterior (``2024-B6`` → ``2023-B6``)."""
    try:
        ano, tipo, num = parse(periodo)
    except ValueError:
        return None
    return formatar(ano - 1, tipo, num)


def ordenar_chave(periodo: str) -> tuple[int, int]:
    """Chave de ordenação cronológica (ano, posição no ano). Inválido vai para o fim."""
    try:
        ano, tipo, num = parse(periodo)
    except ValueError:
        return (9999, 99)
    if tipo is None or num is None:
        return (ano, 0)
    # Normaliza para "fração do ano" para que B/Q/S/M sejam comparáveis entre si.
    return (ano, round(12 * num / POR_ANO[tipo]))


def mais_recente(periodos: list[str]) -> str | None:
    """O período cronologicamente mais recente da lista."""
    validos = [p for p in periodos if p]
    if not validos:
        return None
    return sorted(validos, key=ordenar_chave)[-1]
