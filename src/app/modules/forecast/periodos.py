"""Parsing e aritmética de períodos fiscais canônicos (§6.6).

As séries fiscais vêm em cadências distintas: bimestral (``2024-B3``, RREO),
quadrimestral (``2024-Q2``, RGF), mensal (``2024-M07``) e anual (``2024``). O
forecast precisa **ordenar** a série histórica e **gerar o próximo período-alvo**
sem depender de ``dim_periodo`` em cada request — este módulo é a fonte única dessa
aritmética, reusável por qualquer indicador.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_RE = re.compile(r"^(\d{4})(?:-([BQMbqm])(\d{1,2}))?$")

# Nº de subperíodos por cadência (para virar o ano ao incrementar).
_MAX = {"B": 6, "Q": 3, "M": 12, "A": 1}


@dataclass(frozen=True)
class Periodo:
    """Período fiscal canônico: ano + cadência (B/Q/M/A) + índice do subperíodo."""

    ano: int
    cadencia: str  # "B" | "Q" | "M" | "A"
    indice: int  # 1..max (1 quando anual)

    @property
    def codigo(self) -> str:
        if self.cadencia == "A":
            return f"{self.ano}"
        return f"{self.ano}-{self.cadencia}{self.indice}"

    @property
    def ordinal(self) -> int:
        """Chave monotônica para ordenação/subtração (dentro da mesma cadência)."""
        return self.ano * _MAX[self.cadencia] + (self.indice - 1)

    def meses(self) -> list[int]:
        """Meses-calendário cobertos pelo período (para alinhar exógenas mensais)."""
        if self.cadencia == "M":
            return [self.indice]
        if self.cadencia == "B":
            fim = self.indice * 2
            return [fim - 1, fim]
        if self.cadencia == "Q":
            fim = self.indice * 4
            return [fim - 3, fim - 2, fim - 1, fim]
        return list(range(1, 13))  # anual


def parse_periodo(codigo: str) -> Periodo:
    """Interpreta ``2024-B3`` / ``2024-Q2`` / ``2024-M07`` / ``2024``.

    Levanta ``ValueError`` para formatos desconhecidos — o forecast não adivinha.
    """
    m = _RE.match((codigo or "").strip())
    if m is None:
        raise ValueError(f"Período fiscal irreconhecível: {codigo!r}")
    ano = int(m.group(1))
    if m.group(2) is None:
        return Periodo(ano=ano, cadencia="A", indice=1)
    cad = m.group(2).upper()
    idx = int(m.group(3))
    if idx < 1 or idx > _MAX[cad]:
        raise ValueError(f"Subperíodo fora da faixa para {codigo!r}.")
    return Periodo(ano=ano, cadencia=cad, indice=idx)


def proximo(periodo: Periodo, passos: int = 1) -> Periodo:
    """Avança ``passos`` subperíodos, virando o ano quando necessário."""
    maximo = _MAX[periodo.cadencia]
    total = (periodo.indice - 1) + passos
    ano = periodo.ano + total // maximo
    indice = total % maximo + 1
    return Periodo(ano=ano, cadencia=periodo.cadencia, indice=indice)


def horizonte_periodos(base: str, passos: int) -> list[str]:
    """Lista dos próximos ``passos`` códigos de período a partir de ``base`` (exclusive)."""
    p = parse_periodo(base)
    return [proximo(p, i).codigo for i in range(1, passos + 1)]


def ordenar_codigos(codigos: list[str]) -> list[str]:
    """Ordena códigos de período cronologicamente (cadência homogênea assumida)."""
    return sorted(codigos, key=lambda c: parse_periodo(c).ordinal)
