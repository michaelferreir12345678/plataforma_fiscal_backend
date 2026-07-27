"""Treino batch das projeções (Sprint 14).

O motor de projeção roda como job assíncrono (RQ/Celery) sobre um conjunto de entes
e indicadores, materializando ``gold.fato_projecao`` para todos os modelos viáveis.
Como o MVP roda contra Postgres local (sem Redis obrigatório), estas funções também
podem ser chamadas de forma síncrona (ex.: após uma nova entrega homologada).
"""

from __future__ import annotations

from typing import Any

from app.core.db import admin_session
from app.core.errors import AppError
from app.modules.forecast import service
from app.modules.forecast.series import INDICADORES

_INDICADORES_PADRAO = tuple(INDICADORES.keys())


def treinar_ente(
    cod_ibge: str,
    *,
    indicadores: tuple[str, ...] = _INDICADORES_PADRAO,
    horizonte: int = 4,
) -> dict[str, Any]:
    """Treina e materializa as projeções de um ente para todos os indicadores viáveis."""
    resultado: dict[str, Any] = {"cod_ibge": cod_ibge, "ok": [], "ignorados": []}
    with admin_session() as session:
        for indicador in indicadores:
            try:
                resp = service.build_projecao(
                    session, cod_ibge, indicador, horizonte=horizonte, persistir=True
                )
                resultado["ok"].append(
                    {"indicador": indicador, "modelo": resp.modelo, "pontos": len(resp.projecao)}
                )
            except AppError as exc:
                # Série insuficiente / esfera desconhecida: registra e segue (não falha o lote).
                resultado["ignorados"].append({"indicador": indicador, "motivo": exc.title})
        session.commit()
    return resultado


def treinar_lote(
    entes: list[str],
    *,
    indicadores: tuple[str, ...] = _INDICADORES_PADRAO,
    horizonte: int = 4,
) -> list[dict[str, Any]]:
    """Treina uma carteira/lista de entes (job batch)."""
    return [treinar_ente(cod, indicadores=indicadores, horizonte=horizonte) for cod in entes]
