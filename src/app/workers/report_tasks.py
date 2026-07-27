"""Worker persistente de relatórios (fila local no MVP, extraível para RQ/Celery)."""

from __future__ import annotations

import calendar
import contextlib
import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.db import admin_session
from app.modules.reports import repository, service

logger = logging.getLogger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="reports-worker")
_FUTURES: dict[str, Future[Any]] = {}
_LOCK = threading.Lock()
_SCHEDULER_STOP = threading.Event()
_SCHEDULER_THREAD: threading.Thread | None = None


def enqueue_reports(ids: list[uuid.UUID]) -> str:
    """Enfileira um lote sem bloquear a resposta HTTP; o status vive no PostgreSQL."""
    job_key = uuid.uuid4().hex
    future = _EXECUTOR.submit(processar_lote, [str(item) for item in ids])
    with _LOCK:
        _FUTURES[job_key] = future

    def _cleanup(_: Future[Any]) -> None:
        with _LOCK:
            _FUTURES.pop(job_key, None)

    future.add_done_callback(_cleanup)
    return job_key


def processar_lote(ids: list[str]) -> dict[str, Any]:
    resultados = [processar_relatorio(item) for item in ids]
    return {
        "total": len(resultados),
        "gerados": sum(item["status"] in {"gerado", "parcial"} for item in resultados),
        "falhas": sum(item["status"] == "falhou" for item in resultados),
        "itens": resultados,
    }


def processar_relatorio(relatorio_id: str) -> dict[str, Any]:
    """Gera um artefato real e registra hash/proveniência/auditoria na mesma transação."""
    rid = uuid.UUID(relatorio_id)
    with admin_session() as session:
        claimed = repository.claim_relatorio(session, rid)
        if claimed is None:
            existing = repository.get_relatorio(session, rid)
            return {
                "id": relatorio_id,
                "status": existing.status if existing else "inexistente",
            }
        session.commit()

    artifact_path: Path | None = None
    try:
        with admin_session() as session:
            row = repository.get_relatorio(session, rid)
            if row is None:
                raise RuntimeError("Relatório desapareceu após entrar em processamento.")
            generated_at = datetime.now(UTC)
            document = service.build_document(session, row, generated_at)
            artifact = service.persist_artifact(row, document)
            artifact_path = Path(artifact["arquivo_path"])
            repository.mark_success(
                row,
                cabecalho=document["cabecalho"],
                source_refs=document["source_refs"],
                memoria=service.report_memory(document),
                dados_incompletos=document["dados_incompletos"],
                gerado_em=generated_at,
                **artifact,
            )
            service.audit_export(session, row)
            session.flush()
            return {"id": relatorio_id, "status": row.status, "arquivo": row.arquivo_nome}
    except Exception as exc:
        if artifact_path is not None:
            with contextlib.suppress(OSError):
                artifact_path.unlink()
        with admin_session() as session:
            row = repository.get_relatorio(session, rid)
            if row is not None:
                repository.mark_failure(row, f"{type(exc).__name__}: {exc}")
        return {"id": relatorio_id, "status": "falhou", "erro": str(exc)}


def recover_pending() -> int:
    """Reenfileira jobs que sobreviveram a um restart do processo da API."""
    with admin_session() as session:
        ids = repository.list_pending_ids(session)
    if ids:
        enqueue_reports(ids)
    return len(ids)


def start_scheduler(interval_seconds: float = 30.0) -> None:
    """Inicia o relógio dos agendamentos sem criar mais de uma thread por processo."""
    global _SCHEDULER_THREAD
    with _LOCK:
        if _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive():
            return
        _SCHEDULER_STOP.clear()

        def _loop() -> None:
            while not _SCHEDULER_STOP.wait(interval_seconds):
                try:
                    executar_agendamentos()
                except Exception:
                    # Falha transitória não encerra o scheduler; o próximo ciclo tenta
                    # novamente e o lock/next_execution evita duplicar uma execução.
                    logger.exception("Falha ao executar agendamentos de relatórios")
                    continue

        _SCHEDULER_THREAD = threading.Thread(
            target=_loop,
            name="reports-scheduler",
            daemon=True,
        )
        _SCHEDULER_THREAD.start()


def stop_scheduler() -> None:
    """Solicita parada limpa do scheduler no shutdown da API."""
    global _SCHEDULER_THREAD
    _SCHEDULER_STOP.set()
    thread = _SCHEDULER_THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=2)
    _SCHEDULER_THREAD = None


def executar_agendamentos(now: datetime | None = None) -> int:
    """Materializa os agendamentos vencidos; pode ser chamado por cron/RQ/Celery."""
    run_at = now or datetime.now(UTC)
    report_ids: list[uuid.UUID] = []
    with admin_session() as session:
        schedules = repository.list_due_agendamentos(session, run_at)
        for schedule in schedules:
            lote_id = uuid.uuid4()
            rows = repository.insert_relatorios(
                session,
                org_id=schedule.org_id,
                lote_id=lote_id,
                modelo=schedule.modelo,
                formato=schedule.formato,
                escopo=schedule.escopo,
                entes=[str(item) for item in schedule.entes],
                periodo=schedule.periodo,
                as_of=run_at,
                parametros={**schedule.parametros, "agendamento_id": str(schedule.id)},
                criado_por=schedule.criado_por,
            )
            report_ids.extend(row.id for row in rows)
            schedule.ultima_execucao = run_at
            schedule.proxima_execucao = _next_execution(run_at, schedule.periodicidade)
            schedule.atualizado_em = run_at
    if report_ids:
        enqueue_reports(report_ids)
    return len(report_ids)


def _next_execution(current: datetime, periodicidade: str) -> datetime:
    if periodicidade == "diario":
        return current + timedelta(days=1)
    if periodicidade == "semanal":
        return current + timedelta(days=7)
    months = 2 if periodicidade == "bimestral" else 1
    month_index = current.month - 1 + months
    year = current.year + month_index // 12
    month = month_index % 12 + 1
    day = min(current.day, calendar.monthrange(year, month)[1])
    return current.replace(year=year, month=month, day=day)
