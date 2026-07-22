"""Classificação de faixa de limite legal — fonte única de verdade (§7).

Faixas (§2 CLAUDE.md): **alerta 90%**, **prudencial 95%**, **máximo 100%** do teto.
Mínimos são **pisos** (semântica invertida: abaixo do piso = ruim).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Faixas para tetos (ordem do "melhor" ao "pior").
FAIXA_NORMAL = "normal"
FAIXA_ALERTA = "alerta"
FAIXA_PRUDENCIAL = "prudencial"
FAIXA_EXCEDIDO = "excedido"
# Faixas para pisos (mínimos).
FAIXA_ADEQUADO = "adequado"
FAIXA_INSUFICIENTE = "insuficiente"


@dataclass(frozen=True)
class LimiteLegal:
    """Projeção do ``gold.dim_limite_legal`` para o cálculo."""

    indicador: str
    esfera: str
    poder: str
    sentido: str  # "teto" | "piso"
    teto_pct: Decimal
    alerta_pct: Decimal | None
    prudencial_pct: Decimal | None


def faixas_de_teto(teto_pct: Decimal) -> tuple[Decimal, Decimal]:
    """Deriva (alerta, prudencial) = (90%, 95%) do teto."""
    return teto_pct * Decimal("0.90"), teto_pct * Decimal("0.95")


def classificar_faixa(valor_pct: Decimal, limite: LimiteLegal) -> str:
    """Classifica ``valor_pct`` (% da base, ex.: % da RCL) na faixa do limite."""
    if limite.sentido == "piso":
        return FAIXA_ADEQUADO if valor_pct >= limite.teto_pct else FAIXA_INSUFICIENTE

    alerta_default, prudencial_default = faixas_de_teto(limite.teto_pct)
    alerta = limite.alerta_pct if limite.alerta_pct is not None else alerta_default
    prudencial = (
        limite.prudencial_pct if limite.prudencial_pct is not None else prudencial_default
    )
    if valor_pct >= limite.teto_pct:
        return FAIXA_EXCEDIDO
    if valor_pct >= prudencial:
        return FAIXA_PRUDENCIAL
    if valor_pct >= alerta:
        return FAIXA_ALERTA
    return FAIXA_NORMAL


@dataclass(frozen=True)
class IndicadorCalculado:
    valor_rs: Decimal
    valor_pct_rcl: Decimal
    faixa: str
    teto_pct: Decimal


def calcular_indicador_sobre_rcl(
    valor_rs: Decimal, rcl_12m: Decimal, limite: LimiteLegal
) -> IndicadorCalculado:
    """Calcula % da RCL e classifica a faixa. Requer ``rcl_12m`` > 0."""
    if rcl_12m <= 0:
        raise ValueError("RCL deve ser positiva para calcular o percentual do limite.")
    pct = (valor_rs / rcl_12m) * Decimal(100)
    return IndicadorCalculado(
        valor_rs=valor_rs,
        valor_pct_rcl=pct,
        faixa=classificar_faixa(pct, limite),
        teto_pct=limite.teto_pct,
    )
