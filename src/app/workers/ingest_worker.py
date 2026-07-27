"""Processo dedicado e durável do worker RQ da Central de Dados."""

from __future__ import annotations

import logging
import time
from typing import Any

from rq import Worker

from app.workers import ingest_jobs

logger = logging.getLogger(__name__)


class DurableIngestWorker(Worker):
    """Acopla a manutenção SQL durável ao ciclo periódico nativo do RQ."""

    def run_maintenance_tasks(self) -> None:
        super().run_maintenance_tasks()
        try:
            abandoned, queued = ingest_jobs.maintain_durable_queue(
                connection=self.connection
            )
            if abandoned or queued:
                logger.info(
                    "Manutenção da fila: %s abandonado(s), %s pendente(s) entregue(s)",
                    abandoned,
                    queued,
                )
        except Exception:
            # Uma rodada falha não derruba o consumidor; a próxima manutenção repete
            # operações idempotentes e o restart policy continua como segunda barreira.
            logger.exception("Falha na manutenção durável da fila de ingestão")


def wait_until_ready(*, retry_seconds: float = 2.0) -> Any:
    """Aguarda Redis e schema SQL, permitindo start independente da API."""
    attempt = 0
    while True:
        attempt += 1
        try:
            connection = ingest_jobs._conn()
            ingest_jobs.maintain_durable_queue(connection=connection)
            return connection
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            delay = min(retry_seconds * attempt, 30.0)
            logger.warning(
                "Dependências do worker ainda indisponíveis (%s); nova tentativa em %.1fs",
                exc,
                delay,
            )
            time.sleep(delay)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    connection = wait_until_ready()
    worker = DurableIngestWorker(
        [ingest_jobs.QUEUE_NAME],
        connection=connection,
        maintenance_interval=ingest_jobs.MAINTENANCE_INTERVAL_SECONDS,
        work_horse_killed_handler=ingest_jobs.rq_work_horse_killed,
    )
    worker.work(logging_level="INFO")


if __name__ == "__main__":
    main()
