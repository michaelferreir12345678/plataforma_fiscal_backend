"""Motor de alertas agendado (Sprint 15).

O motor roda como job agendado (RQ/Celery) por organização, avaliando toda a carteira
e materializando ``op.alerta`` + ``gold.calendario_obrigacao``. No MVP (Postgres local,
sem Redis obrigatório) também pode ser chamado de forma síncrona; as leituras dos
endpoints já disparam a avaliação on-read.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.db import admin_session
from app.modules.alerts import engine


def avaliar_org(org_id: str, entes: list[str], *, incluir_preditivo: bool = True) -> dict[str, Any]:
    """Avalia a carteira de uma organização e materializa alertas + calendário."""
    oid = uuid.UUID(org_id)
    total = 0
    with admin_session() as session:
        for cod in entes:
            total += engine.avaliar_ente(session, oid, cod, incluir_preditivo=incluir_preditivo)
        session.commit()
    return {"org_id": org_id, "entes": len(entes), "alertas_materializados": total}
