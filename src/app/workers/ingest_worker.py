"""Processo dedicado e durável do worker RQ da Central de Dados."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from rq import SimpleWorker, Worker
from rq.worker.base import BaseWorker

# Processo isolado: sem o registro, a FK ``ingest_job.criado_por → op.usuario`` fica sem
# tabela referenciada e todo job morre em ``NoReferencedTableError``.
from app.core import models_registry as _models_registry  # noqa: F401
from app.workers import ingest_jobs

logger = logging.getLogger(__name__)

# O RQ só isola a execução em processo filho onde ``os.fork`` existe (POSIX). No Windows
# o worker padrão sobe, aceita a primeira entrega e morre em ``AttributeError: module 'os'
# has no attribute 'fork'`` — a fila fica eternamente "na fila", sem sinal de erro. Por
# isso a classe base é escolhida pela plataforma, não fixada.
FORK_DISPONIVEL = hasattr(os, "fork")

# Bem abaixo da tolerância de heartbeat usada pela saúde da fila.
BATIMENTO_SEGUNDOS = 30.0


def _manutencao_duravel(connection: Any) -> None:
    """Acopla a manutenção SQL durável ao ciclo periódico nativo do RQ."""
    try:
        abandoned, queued = ingest_jobs.maintain_durable_queue(connection=connection)
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


# A manutenção é uma função e não um mixin de propósito: ``SimpleWorker`` **não** é
# subclasse de ``Worker`` (ambos descendem de ``BaseWorker``), então um mixin que herdasse
# ``Worker`` entraria antes de ``SimpleWorker`` na MRO e traria de volta o ``execute_job``
# que dá fork — o bug voltaria disfarçado de herança correta.
class DurableIngestWorker(Worker):
    """Consumidor com isolamento por fork (Linux/containers de produção)."""

    def run_maintenance_tasks(self) -> None:
        super().run_maintenance_tasks()
        _manutencao_duravel(self.connection)


class DurableIngestSimpleWorker(SimpleWorker):
    """Consumidor sem fork: executa a carga no próprio processo (Windows/dev).

    Sem work horse não há como matar um job travado por sinal — o limite passa a ser o
    ``TimerDeathPenalty`` do RQ, e uma queda dura do processo é recuperada pelo lease SQL
    em ``reconcile_abandoned``. É a razão de produção continuar rodando em Linux.
    """

    def run_maintenance_tasks(self) -> None:
        super().run_maintenance_tasks()
        _manutencao_duravel(self.connection)

    def execute_job(self, job: Any, queue: Any) -> Any:
        """Pulsa durante a carga: sem fork, ninguém renova o heartbeat por este worker.

        O monitor do work horse é quem pulsa no caminho com fork. Aqui o job roda inline e
        o heartbeat congelaria por horas, fazendo a saúde da fila declarar "sem sinal de
        vida" justamente enquanto o worker trabalha.
        """
        parar = threading.Event()

        def pulsar() -> None:
            while not parar.wait(BATIMENTO_SEGUNDOS):
                try:
                    self.heartbeat(ingest_jobs.JOB_TIMEOUT + 60)
                except Exception:
                    logger.debug("Heartbeat do worker falhou", exc_info=True)

        batida = threading.Thread(target=pulsar, name="ingest-heartbeat", daemon=True)
        batida.start()
        try:
            return super().execute_job(job, queue)
        finally:
            parar.set()
            batida.join(timeout=2)


def worker_class() -> type[BaseWorker]:
    return DurableIngestWorker if FORK_DISPONIVEL else DurableIngestSimpleWorker


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
    ingest_jobs.alinhar_death_penalty_da_plataforma()
    connection = wait_until_ready()
    cls = worker_class()
    opcoes: dict[str, Any] = {
        "connection": connection,
        "maintenance_interval": ingest_jobs.MAINTENANCE_INTERVAL_SECONDS,
    }
    if FORK_DISPONIVEL:
        # Só o worker com fork tem work horse para relatar como morto.
        opcoes["work_horse_killed_handler"] = ingest_jobs.rq_work_horse_killed
    else:
        logger.warning(
            "Plataforma sem os.fork (%s): a ingestão roda dentro deste processo. "
            "Em produção use Linux para manter o isolamento por work horse.",
            os.name,
        )
    worker = cls([ingest_jobs.QUEUE_NAME], **opcoes)
    worker.work(logging_level="INFO")


if __name__ == "__main__":
    main()
