"""Cálculo da Receita Corrente Líquida (RCL) — fonte única de verdade (§7).

Base legal: LRF (LC 101/2000) art. 2º, IV — RCL = somatório das receitas correntes,
deduzidas as parcelas legais (contribuição dos servidores ao RPPS, compensação financeira
entre regimes previdenciários e, no caso dos municípios/estados, a parcela do FUNDEB),
apurada em **12 meses móveis** e de forma **consolidada**. Materializada no RREO Anexo 03.

Convenções de leitura de ``silver.siconfi_rreo`` (Anexo 03):
- Considera a coluna do total de 12 meses (``COLUNAS_12M``).
- Usa os **subtotais marcados** do demonstrativo: ``RECEITAS CORRENTES (I)`` menos
  ``DEDUÇÕES (II)``. O SICONFI real traz **o subtotal e os itens-filhos juntos**; somar
  os dois duplicaria — por isso, havendo o subtotal ``(I)``/``(II)``, os filhos são
  ignorados. Só quando o subtotal está ausente é que os itens são somados (fallback).
- A linha "RECEITA CORRENTE LÍQUIDA" (resultado, ``III``) é ignorada no somatório.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

DEDUCAO_KEYWORDS = (
    "RPPS",
    "REGIME PROPRIO",
    "CONTRIBUICAO DO SERVIDOR",
    "CONTRIB. DO SERVIDOR",
    "COMPENSACAO",
    "FUNDEB",
    "DEDUCAO",
    "DEDUCOES",
)

COLUNAS_12M = frozenset(
    {
        "12M",
        "TOTAL",
        "TOTAL 12M",
        "TOTAL ULTIMOS 12 MESES",
        "TOTAL (ULTIMOS 12 MESES)",
        "ULTIMOS 12 MESES",
    }
)


def _norm(texto: str | None) -> str:
    """Normaliza para comparação: maiúsculas, sem acentos, colapsando espaços."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(sem_acento.upper().split())


@dataclass(frozen=True)
class RclLinha:
    """Linha bruta do Anexo 03 (silver)."""

    conta: str
    coluna: str | None
    valor: Decimal


@dataclass(frozen=True)
class RclComponente:
    conta: str
    valor: Decimal


@dataclass
class RclResultado:
    rcl_12m: Decimal
    receita_corrente: Decimal
    deducoes_total: Decimal
    componentes: list[RclComponente] = field(default_factory=list)
    deducoes: list[RclComponente] = field(default_factory=list)


def _marcador(conta_norm: str) -> str | None:
    """Marcador de subtotal do Anexo 03 (algarismo romano entre parênteses), se houver."""
    for m in ("(III)", "(II)", "(I)"):  # do mais específico ao mais genérico
        if m in conta_norm:
            return m
    return None


def classificar_linha_rcl(conta: str) -> str:
    """Classifica uma linha do Anexo 03.

    Distingue os **subtotais** marcados (``receita_total`` = (I), ``deducao_total`` = (II))
    dos **itens-filhos** (``receita_item``/``deducao_item``), para que o somatório use um
    OU outro — nunca os dois (senão duplica, como o dado real do SICONFI revelou).
    """
    c = _norm(conta)
    if "RECEITA CORRENTE LIQUIDA" in c:  # linha-resultado (III), não somar
        return "resultado"
    marcador = _marcador(c)
    if marcador == "(III)":
        return "resultado"
    if marcador == "(I)" and "RECEITAS CORRENTES" in c:
        return "receita_total"
    if marcador == "(II)":  # DEDUÇÕES (II) — subtotal das deduções
        return "deducao_total"
    if any(k in c for k in DEDUCAO_KEYWORDS):
        return "deducao_item"
    if "RECEITAS CORRENTES" in c:  # item-filho das receitas correntes
        return "receita_item"
    return "ignorar"


def _considera_coluna(coluna: str | None) -> bool:
    if coluna is None:
        return True  # sem coluna informada: assume já ser o total 12m
    return _norm(coluna) in COLUNAS_12M


def calcular_rcl(linhas: list[RclLinha]) -> RclResultado:
    """Aplica a fórmula da RCL sobre as linhas do Anexo 03 (coluna de 12 meses).

    Prefere os subtotais ``(I)``/``(II)``; na ausência deles, soma os itens correspondentes
    (nunca os dois — ver :func:`classificar_linha_rcl`).
    """
    receita_totais: list[RclComponente] = []
    receita_itens: list[RclComponente] = []
    deducao_totais: list[RclComponente] = []
    deducao_itens: list[RclComponente] = []

    for linha in linhas:
        if not _considera_coluna(linha.coluna):
            continue
        comp = RclComponente(conta=linha.conta, valor=linha.valor or Decimal(0))
        tipo = classificar_linha_rcl(linha.conta)
        if tipo == "receita_total":
            receita_totais.append(comp)
        elif tipo == "receita_item":
            receita_itens.append(comp)
        elif tipo == "deducao_total":
            deducao_totais.append(comp)
        elif tipo == "deducao_item":
            deducao_itens.append(comp)
        # "resultado"/"ignorar": fora do somatório

    componentes = receita_totais or receita_itens
    lista_deducoes = deducao_totais or deducao_itens
    receita = sum((c.valor for c in componentes), Decimal(0))
    deducoes = sum((c.valor for c in lista_deducoes), Decimal(0))

    return RclResultado(
        rcl_12m=receita - deducoes,
        receita_corrente=receita,
        deducoes_total=deducoes,
        componentes=componentes,
        deducoes=lista_deducoes,
    )
