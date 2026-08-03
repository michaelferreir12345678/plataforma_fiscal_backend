"""Aritmética de período fiscal canônico (§6.6) — fonte única.

Períodos da plataforma: ``2024`` (anual), ``2024-B6`` (bimestre), ``2024-Q3``
(quadrimestre), ``2024-S1`` (semestre) e ``2024-M07`` (mês). O cockpit precisa navegar
"período anterior" e "mesmo período do exercício anterior" sem reimplementar isso em cada
módulo — daí este utilitário compartilhado.
"""

from __future__ import annotations

import re
from datetime import date

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


def em_bimestre(periodo: str) -> str | None:
    """Converte um período fiscal ao **bimestre do RREO** que o encerra.

    ``2025-Q3`` → ``2025-B6``; ``2025-S1`` → ``2025-B3``; ``2025-B4`` → ele mesmo.

    É a tradução mais usada da plataforma e estava reescrita em três lugares —
    ``cash_rap``, ``personnel`` e a docstring de ``debt`` —, todas restritas a
    quadrimestre. Município com menos de 50 mil habitantes publica RGF **semestral**
    (LRF, art. 63) e caía fora das três: o cálculo simplesmente não achava a RCL.

    Existe porque quase tudo em ``gold.mart_indicador`` é ancorado no bimestre — o
    denominador (RCL) vem do RREO Anexo 03. Pedir um indicador em ``2025-Q3`` não
    encontra nada, e a leitura natural do erro é "falta dado", quando o que falta é a
    tradução: o período existe, com outro nome.

    A regra é de calendário, não de convenção: o quadrimestre *n* fecha no mês ``4n`` e o
    bimestre ``2n`` fecha no mesmo mês; o semestre *n* fecha em ``6n``, como o bimestre
    ``3n``. Anual não tem bimestre correspondente e devolve ``None``.
    """
    try:
        ano, tipo, num = parse(periodo)
    except ValueError:
        return None
    if tipo is None or num is None:
        return None
    if tipo == "B":
        return periodo
    fator = {"Q": 2, "S": 3}.get(tipo)
    if fator is None:  # mensal não fecha bimestre por si só
        return None
    bimestre = fator * num
    return formatar(ano, "B", bimestre) if 1 <= bimestre <= POR_ANO["B"] else None


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


def mes_final(periodo: str) -> int | None:
    """Mês (1–12) em que o período fiscal se encerra. ``2024-B3`` → 6; ``2024`` → 12.

    É o mês de referência para alinhar o período fiscal a séries mensais (IPCA/Selic).
    """
    try:
        ano, tipo, num = parse(periodo)
    except ValueError:
        return None
    del ano
    if tipo is None or num is None:
        return 12
    if tipo == "M":
        return num if 1 <= num <= 12 else None
    if not 1 <= num <= POR_ANO[tipo]:
        return None
    return round(12 * num / POR_ANO[tipo])


def mais_recente(periodos: list[str]) -> str | None:
    """O período cronologicamente mais recente da lista."""
    validos = [p for p in periodos if p]
    if not validos:
        return None
    return sorted(validos, key=ordenar_chave)[-1]


#: Último mês de cada período, por tipo. Um bimestre fecha em meses pares; um
#: quadrimestre em abril/agosto/dezembro; um semestre em junho/dezembro.
_MES_FINAL_POR_TIPO = {"B": 2, "Q": 4, "S": 6, "M": 1}


def periodos_possiveis(ano: int, tipo: str, hoje: date) -> list[int]:
    """Períodos de ``ano`` que **já terminaram** em ``hoje``.

    Exercício em andamento é o caso normal, não a exceção: em julho de 2026 existem
    RREO até o 3º bimestre e RGF até o 1º quadrimestre — os demais ainda não
    aconteceram. Pedi-los ao SICONFI não é otimismo, é gerar trabalho garantido a
    vazio e, pior, registrar "ausência" de um dado que ninguém deveria ter publicado.

    A regra aqui é deliberadamente conservadora: considera possível o período cujo
    **último mês já se encerrou**, sem descontar o prazo legal de publicação. Se o ente
    ainda não entregou, quem descobre isso é o conector — e aí a ausência é informação
    de verdade (atraso), não ruído de calendário.

    Exercício passado devolve todos; futuro, nenhum.
    """
    por_ano = POR_ANO.get(tipo.upper())
    if por_ano is None:
        raise ValueError(f"Tipo de período desconhecido: {tipo!r}")
    if ano < hoje.year:
        return list(range(1, por_ano + 1))
    if ano > hoje.year:
        return []
    passo = _MES_FINAL_POR_TIPO[tipo.upper()]
    # Um período n encerra no mês ``n * passo``; ele só é possível depois disso.
    return [n for n in range(1, por_ano + 1) if n * passo < hoje.month]
