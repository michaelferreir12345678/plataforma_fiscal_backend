"""Agendador de atualização contínua por cadência (Sprint 21).

Mantém os dados frescos sem intervenção: a cada passagem, decide quais fontes estão
**vencidas** (comparando ``now`` ao último ``ingestion_log`` bem-sucedido e à cadência da
fonte) e roda a ingestão incremental do período corrente para o escopo âncora, mais a
varredura de retificações e o refresh da cobertura. Cada passagem registra um resumo em
``gold.ingestion_log`` (fonte ``scheduler``).

Execução:
- ``--once``  : uma passagem (ideal para um agendador do SO — cron/Agendador de Tarefas).
- ``--loop --interval-min N`` : laço próprio (útil sem cron).

O escopo incremental é enxuto de propósito (ente estadual CE + Fortaleza + ano corrente),
para que a passagem seja rápida e segura no ambiente local; produção amplia o escopo.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.core.db import SessionLocal  # noqa: E402
from app.modules.ingestion import cobertura as cobertura_mod  # noqa: E402
from app.modules.ingestion import integracoes, service  # noqa: E402
from app.modules.ingestion.connectors.registry import FONTE_META  # noqa: E402
from app.modules.ingestion.models import IngestionLog  # noqa: E402
from app.modules.ingestion.schemas import RunRequest  # noqa: E402
from app.shared.ingestion.client import RealClientResolver  # noqa: E402

# Intervalo mínimo entre execuções por cadência (dias). 'eventual' não vence sozinho.
CADENCIA_DIAS: dict[str, int] = {
    "diaria": 1,
    "continua": 1,
    "mensal": 30,
    "bimestral": 60,
    "quadrimestral": 120,
    "semestral": 182,
    "anual": 365,
    "eventual": 10_000,
}

# Escopo incremental do agendador local (âncora enxuta).
ESCOPO_ENTES = ["23", "2304400"]  # ente estadual CE + Fortaleza
# Fontes que o agendador atualiza incrementalmente (as demais são backfill pontual).
FONTES_INCREMENTAIS = ["siconfi_rreo", "siconfi_rgf", "siconfi_dca", "siconfi_extratos", "bcb"]


def _ultimo_ok(session, fonte: str) -> datetime | None:
    return session.scalar(
        select(func.max(IngestionLog.ts)).where(
            IngestionLog.fonte == fonte, IngestionLog.status.in_(["ingested", "skipped", "vazio"])
        )
    )


def _vencida(session, fonte: str, agora: datetime) -> bool:
    meta = FONTE_META.get(fonte)
    if meta is None:
        return False
    intervalo = CADENCIA_DIAS.get(meta.cadencia, 30)
    if intervalo >= 10_000:
        return False
    ultimo = _ultimo_ok(session, fonte)
    if ultimo is None:
        return True
    if ultimo.tzinfo is None:
        ultimo = ultimo.replace(tzinfo=UTC)
    # Roda quando passou ao menos metade do intervalo da cadência (folga de publicação).
    return (agora - ultimo) >= timedelta(days=max(intervalo // 2, 1))


def _req(fonte: str, ano: int) -> RunRequest:
    if fonte == "bcb":
        return RunRequest(fonte="bcb", series=[433, 11, 4390, 189], data_final=date.today())
    return RunRequest(fonte=fonte, entes=ESCOPO_ENTES, anos=[ano])


def run_once() -> dict[str, int]:
    """Uma passagem: roda as fontes vencidas, varre retificações e refaz a cobertura."""
    agora = datetime.now(UTC)
    ano = agora.year
    resolver = RealClientResolver()
    agg = {"fontes": 0, "ingeridos": 0, "silver_rows": 0}
    try:
        with SessionLocal() as session:
            integracoes.seed_integracoes(session)
            vencidas = [f for f in FONTES_INCREMENTAIS if _vencida(session, f, agora)]
            session.commit()
        for fonte in vencidas:
            with SessionLocal() as session:
                res = service.run(session, resolver, _req(fonte, ano))
                session.commit()
            agg["fontes"] += 1
            agg["ingeridos"] += res.ingeridos
            agg["silver_rows"] += res.silver_rows
        # Retificações do escopo (só se o SICONFI estiver entre as vencidas).
        if any(f.startswith("siconfi_") for f in vencidas):
            for ente in ESCOPO_ENTES:
                with SessionLocal() as session:
                    service.varrer_retificacoes(session, resolver, ente=ente)
                    session.commit()
        with SessionLocal() as session:
            cobertura_mod.seed_catalogo(session)
            cobertura_mod.refresh_cobertura(session)
            session.add(
                IngestionLog(
                    fonte="scheduler",
                    status="ingested" if agg["fontes"] else "skipped",
                    mensagem=(
                        f"passagem {agora.isoformat()}: fontes={agg['fontes']} "
                        f"ingeridos={agg['ingeridos']} silver_rows={agg['silver_rows']} "
                        f"vencidas={vencidas}"
                    ),
                )
            )
            session.commit()
    finally:
        resolver.close()
    return agg


def run() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--once", action="store_true", help="Uma passagem e sai (para cron/agendador).")
    p.add_argument("--loop", action="store_true", help="Laço próprio (sem cron).")
    p.add_argument("--interval-min", type=int, default=360, help="Intervalo do laço (min).")
    args = p.parse_args()

    if args.loop:
        while True:
            res = run_once()
            print(f"passagem: {res}")
            time.sleep(max(args.interval_min, 1) * 60)
    else:
        res = run_once()
        print(f"passagem única: {res}")


if __name__ == "__main__":
    run()
