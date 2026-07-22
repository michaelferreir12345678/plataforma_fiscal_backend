"""Registro central de conectores (fonte → classe) e mapa fonte → relatório.

Agrega os conectores de todas as fontes (SICONFI + complementares). O ``service`` usa
este registry para instanciar o conector correto por ``fonte``.
"""

from __future__ import annotations

from app.modules.ingestion.connectors import (
    bcb,
    capag,
    ibge,
    sadipem,
    siconfi,
    siconfi_rreo_minimos_pdf,
    siope,
    siops,
    transferencias,
)
from app.shared.ingestion.base import BaseConnector

CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {
    **siconfi.CONNECTORS,
    **siconfi_rreo_minimos_pdf.CONNECTORS,
    **sadipem.CONNECTORS,
    **bcb.CONNECTORS,
    **ibge.CONNECTORS,
    **transferencias.CONNECTORS,
    **capag.CONNECTORS,
    **siops.CONNECTORS,
    **siope.CONNECTORS,
}

# Mapa fonte → relatório (para status e resolução de versão as_of).
FONTE_RELATORIO: dict[str, str] = {
    fonte: cls.relatorio for fonte, cls in CONNECTOR_REGISTRY.items()
}
