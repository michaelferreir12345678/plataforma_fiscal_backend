"""Helpers de parsing compartilhados pelos conectores (números, datas, campos alternativos)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d")


def num(value: Any) -> Decimal | None:
    """Converte valor monetário/numérico da API em Decimal (ou None)."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def first(item: dict[str, Any], *keys: str) -> Any:
    """Primeiro valor não-nulo dentre nomes de campo alternativos da API."""
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def boolean(value: Any) -> bool | None:
    """Interpreta flags booleanos da API: ``'0'``/``'1'``, ``'S'``/``'N'``, ``'true'``/``'false'``.

    O SICONFI expõe alguns flags como texto com espaços (ex.: ``'0  '``); normaliza antes.
    Valores desconhecidos viram ``None`` (não adivinha).
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    texto = str(value).strip().lower()
    if texto in ("1", "true", "t", "s", "sim", "y", "yes"):
        return True
    if texto in ("0", "false", "f", "n", "nao", "no"):
        return False
    return None


def parse_date(value: Any) -> date | None:
    """Interpreta datas em ISO (``2024-01-31``), BR (``31/01/2024``) ou datetime ISO."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None
