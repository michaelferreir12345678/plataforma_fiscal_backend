"""Execução dos jobs de carteira fora da requisição (Sprint E1).

``POST /carteira/refresh`` percorria o escopo inteiro dentro do handler HTTP — para uma
licença global, 5.598 chamadas a ``refresh_mart_carteira`` numa requisição. O trabalho
passou a nascer como job durável em ``op.carteira_lote_job`` (``acao='refresh'``), e é
aqui que ele acontece.

Duas escolhas deliberadas:

* **O banco é a fila.** Não há uma segunda fila em memória: um job enfileirado sobrevive
  ao restart, e a retomada é uma consulta por ``status='enfileirado'``. É a mesma decisão
  já registrada para os jobs de ingestão (``ingest_jobs``), sem o transporte Redis — a
  materialização é idempotente, então reentrega dupla recalcula o mesmo snapshot.
* **Um job que falha não derruba o ciclo.** Ele é marcado ``falhou`` com o motivo, numa
  sessão nova, e o próximo job segue. Um lote que aborta inteiro por causa de um ente
  perderia o trabalho já feito dos demais.
"""

from __future__ import annotations

import logging
import threading
import uuid

from app.core.db import admin_session
from app.modules.dashboard import carteira_repository as carteira_repo
from app.modules.dashboard import carteira_service
from app.modules.dashboard.models import (
    ACAO_REFRESH,
    LOTE_STATUS_ENFILEIRADO,
    LOTE_STATUS_FALHOU,
)

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_SCHEDULER_THREAD: threading.Thread | None = None
_SCHEDULER_STOP = threading.Event()

#: Intervalo do relógio. O refresh não é interativo: o usuário pediu a materialização, não
#: está esperando por ela na tela.
INTERVALO_PADRAO_SEGUNDOS = 60.0

#: Quantos jobs um ciclo consome. Limitar evita que uma fila acumulada monopolize a thread.
LOTE_POR_CICLO = 20


def _marcar_falha(job_id: uuid.UUID, erro: str) -> None:
    """Fecha o job numa sessão nova — a que falhou já foi revertida."""
    with admin_session() as session:
        job = carteira_repo.get_lote_job(session, job_id)
        if job is None:
            return
        job.status = LOTE_STATUS_FALHOU
        filtro = dict(job.filtro or {})
        filtro["erro"] = erro[:400]
        job.filtro = filtro


def executar_pendentes(*, limite: int = LOTE_POR_CICLO) -> dict[str, int]:
    """Executa os jobs ``refresh`` enfileirados. Retorna contadores do ciclo."""
    resumo = {"jobs": 0, "linhas": 0, "falhas": 0}
    with admin_session() as session:
        pendentes = [
            job.id
            for job in carteira_repo.list_lote_jobs(
                session, acao=ACAO_REFRESH, status=LOTE_STATUS_ENFILEIRADO, limite=limite
            )
        ]
    for job_id in pendentes:
        try:
            with admin_session() as session:
                linhas = carteira_service.executar_refresh_job(session, job_id)
        except Exception as exc:  # noqa: BLE001 — um job não derruba o ciclo
            logger.exception("Falha ao executar o refresh de carteira %s", job_id)
            resumo["falhas"] += 1
            _marcar_falha(job_id, f"{exc.__class__.__name__}: {exc}")
            continue
        # Os contadores só sobem **depois** do commit: somá-los dentro do bloco faria um
        # commit falho reportar linhas que não chegaram ao banco.
        resumo["jobs"] += 1
        resumo["linhas"] += linhas
    return resumo


def start_scheduler(interval_seconds: float = INTERVALO_PADRAO_SEGUNDOS) -> None:
    """Relógio do refresh de carteira; uma thread por processo, como os demais."""
    global _SCHEDULER_THREAD
    with _LOCK:
        if _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive():
            return
        _SCHEDULER_STOP.clear()

        def _loop() -> None:
            while not _SCHEDULER_STOP.wait(interval_seconds):
                try:
                    executar_pendentes()
                except Exception:
                    logger.exception("Falha no ciclo de refresh de carteira")
                    continue

        _SCHEDULER_THREAD = threading.Thread(
            target=_loop, name="carteira-refresh-scheduler", daemon=True
        )
        _SCHEDULER_THREAD.start()


def stop_scheduler() -> None:
    global _SCHEDULER_THREAD
    with _LOCK:
        _SCHEDULER_STOP.set()
        if _SCHEDULER_THREAD is not None:
            _SCHEDULER_THREAD.join(timeout=2)
            _SCHEDULER_THREAD = None
