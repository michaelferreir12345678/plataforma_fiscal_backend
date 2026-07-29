"""Executor RQ dos jobs de ingestão (Central de Dados, Sprint 24).

Redis/RQ é o único transporte assíncrono fora dos testes. Não existe fallback silencioso
para thread local: indisponibilidade da fila vira erro explícito e o job persistido termina
como falho/retryável. O claim no PostgreSQL é atômico, portanto entregas duplicadas do RQ,
cancelamento concorrente e dois retries não executam a mesma carga duas vezes.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from rq.timeouts import JobTimeoutException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.modules.ingestion import jobs_repository
from app.modules.ingestion.jobs_models import (
    STATUS_CONCLUIDO,
    STATUS_FALHOU,
    IngestJob,
)

logger = logging.getLogger(__name__)

QUEUE_NAME = "ingest"
JOB_TIMEOUT = 60 * 60
ABANDONED_GRACE_SECONDS = 60
MAINTENANCE_INTERVAL_SECONDS = 60
# Folga sobre o intervalo de manutenção: um worker ocioso renova o heartbeat bem antes.
HEARTBEAT_TOLERANCIA_SEGUNDOS = 150
RESULT_TTL = 24 * 60 * 60
FAILURE_TTL = 7 * 24 * 60 * 60
_COVERAGE_LOCK = 0x53465024

_EAGER = False
_RECALCULAR = True
_UNIT_RUNNER: Callable[[Session, IngestJob, str, str], int | dict[str, Any]] | None = None

_STOP = threading.Event()
_CONSUMER: threading.Thread | None = None


def set_eager(value: bool) -> None:
    """Modo estritamente de teste: ``enqueue`` executa inline."""
    global _EAGER
    _EAGER = value


def eager_enabled() -> bool:
    return _EAGER


def set_unit_runner(
    runner: Callable[[Session, IngestJob, str, str], int | dict[str, Any]] | None,
) -> None:
    """Substitui a unidade de trabalho (worker fake nos testes)."""
    global _UNIT_RUNNER
    _UNIT_RUNNER = runner


def set_recalcular(value: bool) -> None:
    global _RECALCULAR
    _RECALCULAR = value


def now() -> datetime:
    return datetime.now(UTC)


def inspecionar_fila() -> tuple[int, int, int | None, bool, str | None]:
    """(registrados, vivos, profundidade, redis_ok, detalhe) do consumidor da fila.

    "Vivo" depende do estado do worker, e os dois casos são diferentes:

    * **ocupado** — precisa de heartbeat recente. Quem morre no meio de um job deixa a chave
      no Redis até o timeout do job (uma hora), então contar registro diria "há consumidor"
      durante todo esse tempo: o erro na direção mais perigosa, com a tela calma enquanto
      nada anda. Por isso o worker sem fork pulsa durante a carga (ver ``ingest_worker``).
    * **ocioso** — o RQ só pulsa a cada ciclo de espera (~405 s), mas renova o TTL da chave
      a cada pulso; um ocioso morto **desaparece** de ``Worker.all`` em ~420 s. Aí a própria
      expiração já é a prova de vida, e exigir heartbeat recente marcaria como morto um
      worker perfeitamente saudável — o alarme falso que este endpoint existe para evitar.
    """
    try:
        from rq import Queue, Worker
        from rq.worker_registration import clean_worker_registry

        connection = _conn()
        fila = Queue(QUEUE_NAME, connection=connection)
        clean_worker_registry(fila)
        limite = now() - timedelta(seconds=HEARTBEAT_TOLERANCIA_SEGUNDOS)
        workers = Worker.all(queue=fila)
        def vivo(w: Any) -> bool:
            if str(w.get_state()) != "busy":
                return True  # ocioso: a expiração da chave já é a prova de vida
            return w.last_heartbeat is not None and w.last_heartbeat >= limite

        vivos = sum(1 for w in workers if vivo(w))
        return len(workers), vivos, fila.count, True, None
    except Exception as exc:  # noqa: BLE001 — indisponibilidade é resposta, não erro 500
        logger.warning("Não foi possível inspecionar a fila de ingestão: %s", exc)
        return 0, 0, None, False, (
            f"Redis/RQ indisponível: {exc.__class__.__name__}: {exc}"[:400]
        )


def alinhar_death_penalty_da_plataforma() -> None:
    """Alinha o limite de tempo dos *registries* do RQ ao suportado pela plataforma.

    O RQ escolhe a classe do **worker** por plataforma (``get_default_death_penalty_class``),
    mas deixa ``BaseRegistry.death_penalty_class`` fixo em ``UnixSignalDeathPenalty``. Em
    Windows, limpar um registro cujo job tem ``on_failure`` estoura ``AttributeError:
    module 'signal' has no attribute 'SIGALRM'`` e derruba o consumidor — exatamente na
    rotina que existe para recuperar entregas órfãs, então uma entrega perdida passava a
    matar o worker a cada manutenção.
    """
    from rq.registry import BaseRegistry
    from rq.timeouts import get_default_death_penalty_class

    # O atributo é anotado no RQ como a classe POSIX concreta, embora aceite qualquer
    # ``BaseDeathPenalty``; é justamente essa anotação estreita que esconde o bug no Windows.
    BaseRegistry.death_penalty_class = get_default_death_penalty_class()  # type: ignore[assignment]


def _conn() -> Any:
    import redis

    connection = redis.Redis.from_url(
        settings.redis_url,
        protocol=2,
        socket_connect_timeout=2,
        socket_timeout=5,
    )
    connection.ping()
    return connection


def _rq_job_id(job: IngestJob) -> str:
    # RQ 2.10 aceita apenas letras, números, "_" e "-" no id explícito.
    return f"ingest-{job.id}-tentativa-{job.tentativas + 1}"


def _rq_job_uuid(rq_job: Any) -> uuid.UUID | None:
    """Extrai o UUID do payload RQ sem confiar no id composto da entrega."""
    try:
        return uuid.UUID(str(rq_job.args[0]))
    except (AttributeError, IndexError, TypeError, ValueError):
        logger.error("Entrega RQ sem UUID de ingestão válido: %s", getattr(rq_job, "id", "?"))
        return None


def _audit_result(session: Session, job: IngestJob) -> None:
    # Import tardio evita ciclo jobs_service → worker → jobs_service.
    from app.modules.ingestion import jobs_service

    jobs_service.audit_job_result(session, job)


def _mark_active_failure(
    job_id: uuid.UUID,
    *,
    phase: str,
    error: str,
) -> bool:
    """Fecha, de forma idempotente, uma entrega que o runtime RQ não conseguiu concluir."""
    from app.core.db import admin_session

    with admin_session() as session:
        job = jobs_repository.lock_active_job(session, job_id)
        if job is None:
            return False
        mensagem = error[:2000]
        resultado = dict(job.resultado or {})
        resultado.setdefault("itens", [])
        resultado.update(
            {
                "indicadores_recalculados": resultado.get("indicadores_recalculados", []),
                "cobertura_antes": resultado.get("cobertura_antes"),
                "cobertura_depois": resultado.get("cobertura_depois"),
                "delta_cobertura": resultado.get("delta_cobertura"),
                "erro_sistema": {"fase": phase, "erro": mensagem},
            }
        )
        job.resultado = resultado
        job.status = STATUS_FALHOU
        job.erro_resumo = mensagem[:400]
        job.terminado_em = now()
        jobs_repository.add_log(
            session,
            job_id=job.id,
            fonte=job.fonte,
            status="erro",
            mensagem=f"Falha terminal do worker em {phase}: {mensagem}",
        )
        _audit_result(session, job)
    return True


def rq_failure_callback(
    rq_job: Any,
    connection: Any,
    exc_type: type[BaseException],
    exc_value: BaseException,
    traceback: Any,
) -> None:
    """Callback do RQ para falhas antes/depois do ciclo transacional da aplicação."""
    del connection, traceback
    job_id = _rq_job_uuid(rq_job)
    if job_id is None:
        return
    error = f"{exc_type.__name__}: {exc_value}"
    _mark_active_failure(job_id, phase="rq_failure_callback", error=error)


def rq_work_horse_killed(
    rq_job: Any,
    retpid: int,
    ret_val: int,
    rusage: Any,
) -> None:
    """Fecha a entrega quando o processo filho morre sem executar callback Python."""
    del rusage
    job_id = _rq_job_uuid(rq_job)
    if job_id is None:
        return
    _mark_active_failure(
        job_id,
        phase="rq_work_horse_killed",
        error=f"Work horse RQ terminou inesperadamente (pid={retpid}, status={ret_val}).",
    )


def _esta_na_lista(queue: Any, rq_id: str) -> bool:
    """O id está mesmo na lista da fila, ou só o hash de status sobrou?

    Um worker que morre entre o ``LPOP`` e a gravação do status deixa um registro dizendo
    ``queued`` sem nada na lista. Confiar no status é confiar na lápide: o job some da fila
    e a tela mostra "Na fila" para sempre.
    """
    try:
        posicao = queue.connection.lpos(queue.key, rq_id)
        if posicao is not None:
            return True
        # ``lpos`` devolve None tanto para ausente quanto para Redis antigo sem o comando;
        # a varredura confirma antes de descartarmos uma entrega válida.
        return rq_id in set(queue.get_job_ids())
    except Exception:  # noqa: BLE001 — na dúvida, tratar como presente e não duplicar
        return True


def _worker_vivo(queue: Any, rq_job: Any) -> bool:
    """O worker que assumiu esta entrega ainda dá sinal de vida?"""
    from rq import Worker

    nome = getattr(rq_job, "worker_name", None)
    if not nome:
        return False
    try:
        limite = now() - timedelta(seconds=HEARTBEAT_TOLERANCIA_SEGUNDOS)
        return any(
            w.name == nome and w.last_heartbeat is not None and w.last_heartbeat >= limite
            for w in Worker.all(queue=queue)
        )
    except Exception:  # noqa: BLE001 — na dúvida, reentregar é o lado seguro
        return False


def _enqueue_unique(queue: Any, job: IngestJob) -> str:
    """Entrega idempotente usando ``unique=True`` do RQ 2.10.

    Uma entrega ativa com o mesmo id é reutilizada. Uma chave terminal deixada pelo
    Redis antes do claim SQL é removida e recriada, pois o PostgreSQL ainda declara
    essa tentativa como pendente.

    ``STARTED`` **não** conta como ativa por si só. Esta função só é chamada para jobs que o
    PostgreSQL declara ``na_fila``, e o claim SQL (``na_fila → executando``) acontece no
    início da execução: se o SQL diz ``na_fila``, ninguém está executando. Um ``STARTED``
    remanescente é lápide de worker morto, e tratá-lo como entrega viva congelava o job até
    o timeout de uma hora — sem consumidor, sem erro e sem sinal na tela.
    """
    from rq.exceptions import DuplicateJobError, NoSuchJobError
    from rq.job import Job, JobStatus

    rq_id = _rq_job_id(job)
    enqueue_options = {
        "func": run_job_async,
        "args": (str(job.id),),
        "job_id": rq_id,
        "timeout": JOB_TIMEOUT,
        "result_ttl": RESULT_TTL,
        "failure_ttl": FAILURE_TTL,
        "on_failure": rq_failure_callback,
        "unique": True,
    }
    active = {
        JobStatus.QUEUED,
        JobStatus.DEFERRED,
        JobStatus.SCHEDULED,
    }
    for _ in range(2):
        try:
            queue.enqueue_call(**enqueue_options)
            return rq_id
        except DuplicateJobError:
            try:
                existing = Job.fetch(rq_id, connection=queue.connection)
            except NoSuchJobError:
                continue
            status = existing.get_status(refresh=True)
            if status == JobStatus.QUEUED and not _esta_na_lista(queue, rq_id):
                # Diz "na fila" mas não está na fila: entrega perdida, não entrega viva.
                existing.delete(remove_from_queue=True, delete_dependents=False)
                continue
            if status in active:
                return rq_id
            if status == JobStatus.STARTED and _worker_vivo(queue, existing):
                # Corrida estreita: o worker acabou de fazer o claim e o SQL ainda não
                # refletia. Reentregar aqui só geraria uma duplicata que o claim recusa.
                return rq_id
            existing.delete(remove_from_queue=True, delete_dependents=False)
    queue.enqueue_call(**enqueue_options)
    return rq_id


def _queue_failure(session: Session, job_id: uuid.UUID, exc: Exception) -> None:
    """Persiste a falha de transporte; nunca deixa um ``na_fila`` invisivelmente órfão."""
    session.rollback()
    # Um timeout de socket pode ocorrer depois de o Redis aceitar a entrega. A trava
    # condicional impede o produtor de sobrescrever um claim/cancelamento concorrente.
    job = jobs_repository.lock_queued_job(session, job_id)
    if job is None:
        return
    mensagem = f"Redis/RQ indisponível: {exc.__class__.__name__}: {exc}"[:2000]
    job.status = STATUS_FALHOU
    job.erro_resumo = mensagem[:400]
    job.terminado_em = now()
    job.resultado = {
        **(job.resultado or {}),
        "itens": list((job.resultado or {}).get("itens", [])),
        "indicadores_recalculados": [],
        "cobertura_antes": None,
        "cobertura_depois": None,
        "delta_cobertura": None,
        "erro_sistema": {"fase": "fila", "erro": mensagem},
    }
    jobs_repository.add_log(
        session,
        job_id=job.id,
        fonte=job.fonte,
        status="erro",
        mensagem=mensagem,
    )
    _audit_result(session, job)
    session.commit()


def enqueue(
    session: Session,
    job: IngestJob,
    *,
    eager_resolver: Any | None = None,
) -> str:
    """Persiste e entrega ao RQ; só o modo eager de teste executa sem Redis."""
    if _EAGER:
        _processar(session, job.id, resolver_override=eager_resolver)
        return "eager"

    session.commit()
    try:
        from rq import Queue

        connection = _conn()
        queue = Queue(QUEUE_NAME, connection=connection, default_timeout=JOB_TIMEOUT)
        return _enqueue_unique(queue, job)
    except Exception as exc:
        logger.exception("Falha ao entregar job %s ao Redis/RQ", job.id)
        _queue_failure(session, job.id, exc)
        raise AppError(
            status=503,
            title="Fila de ingestão indisponível",
            detail=(
                "O job foi persistido como falho e pode ser reenviado quando o Redis/RQ "
                "estiver disponível."
            ),
            type_="urn:plataforma-fiscal:error:ingestion-queue-unavailable",
        ) from exc


def cancel_rq(job: IngestJob) -> None:
    """Remove a entrega pendente do Redis quando possível; o claim SQL é a garantia final."""
    if _EAGER:
        return
    try:
        from rq import cancel_job

        cancel_job(_rq_job_id(job), connection=_conn(), enqueue_dependents=False)
    except Exception as exc:
        logger.info("Job %s já saiu da fila RQ ou Redis está indisponível: %s", job.id, exc)


def reconcile_abandoned(*, cutoff: datetime | None = None) -> int:
    """Recupera leases SQL vencidos após crash integral do worker.

    O cutoff padrão inclui a tolerância usada pelo monitor do RQ e, portanto, só
    considera abandonado um job há mais tempo que ``JOB_TIMEOUT``.
    """
    from app.core.db import admin_session

    stale_before = cutoff or (
        now() - timedelta(seconds=JOB_TIMEOUT + ABANDONED_GRACE_SECONDS)
    )
    with admin_session() as session:
        jobs = jobs_repository.requeue_abandoned_jobs(session, cutoff=stale_before)
        for job in jobs:
            resultado = dict(job.resultado or {})
            resultado.setdefault("itens", [])
            resultado.update(
                {
                    "indicadores_recalculados": resultado.get(
                        "indicadores_recalculados", []
                    ),
                    "cobertura_antes": resultado.get("cobertura_antes"),
                    "cobertura_depois": resultado.get("cobertura_depois"),
                    "delta_cobertura": resultado.get("delta_cobertura"),
                    "erro_sistema": {
                        "fase": "worker_abandonado",
                        "erro": (
                            "Execução sem heartbeat útil após o timeout; checkpoints "
                            "preservados para reprocessar apenas unidades pendentes."
                        ),
                    },
                }
            )
            job.resultado = resultado
            jobs_repository.add_log(
                session,
                job_id=job.id,
                fonte=job.fonte,
                status="recuperado",
                mensagem=(
                    "Worker anterior foi interrompido; entrega devolvida à fila com "
                    "checkpoints preservados."
                ),
            )
    if jobs:
        logger.warning("%s job(s) de ingestão abandonado(s) reconciliado(s)", len(jobs))
    return len(jobs)


def recover_pending(*, connection: Any | None = None) -> int:
    """Reentrega jobs ``na_fila`` após restart; claim atômico torna duplicatas inofensivas."""
    from app.core.db import admin_session

    if connection is None:
        try:
            connection = _conn()
        except Exception as exc:
            logger.warning("Redis/RQ indisponível durante recuperação: %s", exc)
            return 0

    from rq import Queue

    queue = Queue(QUEUE_NAME, connection=connection, default_timeout=JOB_TIMEOUT)
    recovered = 0
    with admin_session() as session:
        for job in jobs_repository.list_queued_jobs(session):
            try:
                _enqueue_unique(queue, job)
                recovered += 1
            except Exception:
                logger.exception("Falha ao recuperar job de ingestão %s", job.id)
    return recovered


def maintain_durable_queue(*, connection: Any | None = None) -> tuple[int, int]:
    """Executa uma rodada idempotente de reconciliação SQL + entrega Redis."""
    abandoned = reconcile_abandoned()
    queued = recover_pending(connection=connection)
    return abandoned, queued


def run_job_async(job_id_str: str) -> None:
    """Entrada oficial do RQ: abre sessão privilegiada e executa um claim condicional."""
    from app.core.db import admin_session

    try:
        job_id = uuid.UUID(job_id_str)
    except ValueError:
        logger.error("RQ recebeu id de ingestão inválido: %s", job_id_str)
        return
    with admin_session() as session:
        _processar(session, job_id, propagate_unexpected=True)


class _ThreadSimpleWorkerMixin:
    """Permite o worker RQ oficial numa thread de desenvolvimento sem signal handlers."""

    def _install_signal_handlers(self) -> None:
        return


def _embedded_loop() -> None:
    from rq import SimpleWorker

    class ThreadSimpleWorker(_ThreadSimpleWorkerMixin, SimpleWorker):
        pass

    alinhar_death_penalty_da_plataforma()
    while not _STOP.is_set():
        try:
            connection = _conn()
            worker = ThreadSimpleWorker([QUEUE_NAME], connection=connection)
            worker.work(burst=True, logging_level="WARNING")
        except Exception:
            if not _STOP.is_set():
                logger.exception("Worker RQ embutido falhou")
        _STOP.wait(1)


def start_worker() -> None:
    """Worker embutido opt-in para desenvolvimento; produção usa processo RQ separado."""
    global _CONSUMER
    if not settings.ingest_worker_in_process or _EAGER or _CONSUMER is not None:
        return
    _STOP.clear()
    _CONSUMER = threading.Thread(
        target=_embedded_loop,
        name="rq-ingest-embedded",
        daemon=True,
    )
    _CONSUMER.start()


def stop_worker() -> None:
    global _CONSUMER
    thread = _CONSUMER
    if thread is None:
        return
    _STOP.set()
    thread.join(timeout=5)
    _CONSUMER = None


def _all_units(job: IngestJob) -> list[tuple[str, str]]:
    if job.tipo == "replay":
        chaves = list(job.periodos)
    else:
        payload = (job.parametros or {}).get("run_request") or {}
        anos = list(payload.get("anos") or job.periodos)
        chaves = [str(ano) for ano in anos] or ["global"]
    if job.fonte == "bcb":
        return [("BR", "global")]
    return [(ente, chave) for ente in job.entes for chave in chaves]


def _retry_plan(job: IngestJob) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    resultado = job.resultado or {}
    anteriores = list(resultado.get("itens", []))
    sucessos = [item for item in anteriores if item.get("ok")]
    sucesso_keys = {
        (str(item["ente"]), str(item["chave"]))
        for item in sucessos
        if item.get("ente") is not None and item.get("chave") is not None
    }
    falhos = [
        (str(item["ente"]), str(item["chave"]))
        for item in anteriores
        if not item.get("ok") and item.get("ente") is not None and item.get("chave") is not None
    ]
    erro_sistema = resultado.get("erro_sistema") or {}
    if erro_sistema:
        if erro_sistema.get("fase") == "pos_job":
            # Se todas as unidades passaram, a tentativa repete somente o pós-job.
            # Havendo falhas funcionais, reprocessa-as antes de recalcular.
            return sucessos, falhos
        pendentes = [unit for unit in _all_units(job) if unit not in sucesso_keys]
        return sucessos, pendentes
    if falhos:
        return sucessos, falhos
    return [], _all_units(job)


def pending_retry_units(job: IngestJob) -> list[tuple[str, str]]:
    """Unidades que uma nova tentativa realmente executará, para contrato/auditoria."""
    return _retry_plan(job)[1]


def _make_real_runner(
    resolver: Any,
) -> Callable[[Session, IngestJob, str, str], dict[str, Any]]:
    from app.modules.ingestion import service
    from app.modules.ingestion.schemas import RunRequest

    def runner(session: Session, job: IngestJob, ente: str, chave: str) -> dict[str, Any]:
        if job.tipo == "replay":
            replay_cfg = (job.parametros or {}).get("replay") or {}
            result = service.replay(
                session,
                resolver,
                ente=ente,
                periodo=chave,
                fonte=replay_cfg.get("fonte"),
            )
        else:
            payload = dict((job.parametros or {}).get("run_request") or {})
            payload["fonte"] = job.fonte
            if job.fonte != "bcb":
                payload["entes"] = (
                    list(payload.get("entes") or []) if ente == "BR" else [ente]
                )
                if chave != "global":
                    payload["anos"] = [int(chave)]
            request = RunRequest.model_validate(payload)
            result = service.run(session, resolver, request)

        if result.pausado:
            raise RuntimeError(result.observacao or "Integração pausada.")
        if result.total_jobs == 0:
            raise RuntimeError("Nenhuma unidade de ingestão/replay foi encontrada.")
        return {
            "silver_rows": int(result.silver_rows or 0),
            "mensagem": json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
            "detalhe": result.model_dump(mode="json"),
        }

    return runner


def _runner_result(
    value: int | dict[str, Any],
) -> tuple[int, str | None, dict[str, Any] | None]:
    if isinstance(value, dict):
        return (
            int(value.get("silver_rows", 0) or 0),
            str(value["mensagem"])[:2000] if value.get("mensagem") else None,
            dict(value["detalhe"]) if isinstance(value.get("detalhe"), dict) else None,
        )
    return int(value or 0), None, None


def _processar(
    session: Session,
    job_id: uuid.UUID,
    *,
    resolver_override: Any | None = None,
    propagate_unexpected: bool = False,
) -> None:
    """Executa o ciclo com claim atômico, checkpoint por unidade e falha terminal segura."""
    resolver = resolver_override
    owns_resolver = False
    stage = "claim"
    try:
        job = jobs_repository.claim_job(session, job_id, iniciado_em=now())
        if job is None:
            session.rollback()
            return
        session.info["ingest_job_id"] = job.id

        stage = "inicializacao"
        itens_base, unidades = _retry_plan(job)
        total = max(len(itens_base) + len(unidades), len(itens_base), 1)
        job.itens_total = total
        job.itens_ok = len(itens_base)
        job.itens_erro = 0
        job.progresso_pct = int(len(itens_base) / total * 90)
        job.erro_resumo = None
        job.resultado = {
            "itens": itens_base,
            "indicadores_recalculados": [],
            "cobertura_antes": None,
            "cobertura_depois": None,
            "delta_cobertura": None,
            "erro_sistema": None,
        }
        jobs_repository.add_log(
            session,
            job_id=job.id,
            fonte=job.fonte,
            status="executando",
            mensagem=f"Tentativa {job.tentativas}; {len(unidades)} unidade(s) pendente(s).",
        )
        session.commit()

        runner = _UNIT_RUNNER
        stage = "preparacao"
        if runner is None and unidades:
            from app.shared.ingestion.client import RealClientResolver

            if resolver is None:
                resolver = RealClientResolver()
                owns_resolver = True
            runner = _make_real_runner(resolver)

        novos: list[dict[str, Any]] = []
        ok_novos = erros_novos = 0
        for indice, (ente, chave) in enumerate(unidades, start=1):
            stage = f"unidade:{ente}:{chave}"
            try:
                assert runner is not None
                rows, mensagem, detalhe = _runner_result(runner(session, job, ente, chave))
                item = {
                    "ente": ente,
                    "chave": chave,
                    "ok": True,
                    "erro": None,
                    "silver_rows": rows,
                    "detalhe": detalhe,
                }
                novos.append(item)
                ok_novos += 1
                jobs_repository.add_log(
                    session,
                    job_id=job.id,
                    fonte=job.fonte,
                    cod_ibge=None if ente == "BR" else ente,
                    periodo=chave,
                    status="unidade_concluida",
                    mensagem=mensagem,
                )
            except JobTimeoutException:
                # Timeout é falha da entrega inteira, nunca uma falha funcional da unidade.
                raise
            except Exception as exc:
                session.rollback()
                job = jobs_repository.get_job(session, job_id)
                assert job is not None
                session.info["ingest_job_id"] = job.id
                erro = f"{exc.__class__.__name__}: {exc}"[:400]
                item = {
                    "ente": ente,
                    "chave": chave,
                    "ok": False,
                    "erro": erro,
                    "silver_rows": 0,
                    "detalhe": None,
                }
                novos.append(item)
                erros_novos += 1
                jobs_repository.add_log(
                    session,
                    job_id=job.id,
                    fonte=job.fonte,
                    cod_ibge=None if ente == "BR" else ente,
                    periodo=chave,
                    status="erro",
                    mensagem=erro,
                )

            processados = len(itens_base) + indice
            job.itens_ok = len(itens_base) + ok_novos
            job.itens_erro = erros_novos
            job.progresso_pct = min(int(processados / total * 90), 90)
            job.resultado = {
                **(job.resultado or {}),
                "itens": [*itens_base, *novos],
                "erro_sistema": None,
            }
            session.commit()

        itens = [*itens_base, *novos]
        entes_ok = sorted({str(item["ente"]) for item in itens if item.get("ok")})
        stage = "pos_job"
        recalc = _recalcular(session, job, entes_ok) if _RECALCULAR else {
            "indicadores_recalculados": [],
            "cobertura_antes": None,
            "cobertura_depois": None,
            "delta_cobertura": None,
        }
        job.resultado = {
            "itens": itens,
            **recalc,
            "erro_sistema": None,
            "resumo_execucao": _aggregate_run_result(job, itens),
        }
        job.itens_ok = sum(1 for item in itens if item.get("ok"))
        job.itens_erro = sum(1 for item in itens if not item.get("ok"))
        job.status = STATUS_CONCLUIDO if job.itens_erro == 0 else STATUS_FALHOU
        job.erro_resumo = (
            None
            if job.itens_erro == 0
            else f"{job.itens_erro} de {job.itens_total} unidade(s) falharam."
        )
        job.progresso_pct = 100
        job.terminado_em = now()
        jobs_repository.add_log(
            session,
            job_id=job.id,
            fonte=job.fonte,
            status=job.status,
            mensagem=job.erro_resumo or "Job concluído.",
        )
        _audit_result(session, job)
        session.commit()
    except Exception as exc:
        session.rollback()
        job = jobs_repository.get_job(session, job_id)
        if job is None:
            if isinstance(exc, JobTimeoutException) or propagate_unexpected:
                raise
        else:
            session.info["ingest_job_id"] = job.id
            erro = f"{exc.__class__.__name__}: {exc}"[:2000]
            resultado = dict(job.resultado or {})
            resultado.setdefault("itens", [])
            resultado.update(
                {
                    "indicadores_recalculados": resultado.get(
                        "indicadores_recalculados", []
                    ),
                    "cobertura_antes": resultado.get("cobertura_antes"),
                    "cobertura_depois": resultado.get("cobertura_depois"),
                    "delta_cobertura": resultado.get("delta_cobertura"),
                    "erro_sistema": {"fase": stage, "erro": erro},
                }
            )
            job.resultado = resultado
            job.status = STATUS_FALHOU
            job.erro_resumo = erro[:400]
            job.terminado_em = now()
            jobs_repository.add_log(
                session,
                job_id=job.id,
                fonte=job.fonte,
                status="erro",
                mensagem=f"Falha inesperada em {stage}: {erro}",
            )
            _audit_result(session, job)
            session.commit()
        if isinstance(exc, JobTimeoutException) or propagate_unexpected:
            raise
    finally:
        if resolver is not None and owns_resolver:
            resolver.close()


def _aggregate_run_result(
    job: IngestJob,
    itens: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Mantém o resultado funcional legado dentro do novo envelope assíncrono."""
    detalhes = [
        item["detalhe"]
        for item in itens
        if item.get("ok") and isinstance(item.get("detalhe"), dict)
    ]
    if not detalhes:
        return None
    observacoes = [str(row["observacao"]) for row in detalhes if row.get("observacao")]
    return {
        "fonte": job.fonte,
        "total_jobs": sum(int(row.get("total_jobs", 0) or 0) for row in detalhes),
        "ingeridos": sum(int(row.get("ingeridos", 0) or 0) for row in detalhes),
        "pulados": sum(int(row.get("pulados", 0) or 0) for row in detalhes),
        "silver_rows": sum(int(row.get("silver_rows", 0) or 0) for row in detalhes),
        "versoes_vigentes": sorted(
            {
                str(versao)
                for row in detalhes
                for versao in (row.get("versoes_vigentes") or [])
            }
        ),
        "pausado": any(bool(row.get("pausado")) for row in detalhes),
        "observacao": "; ".join(observacoes) if observacoes else None,
    }


def _cobertura_count(session: Session, job: IngestJob) -> int:
    from app.modules.ingestion.models import MartCoberturaFonte

    stmt = (
        select(func.count())
        .select_from(MartCoberturaFonte)
        .where(MartCoberturaFonte.fonte == job.fonte)
    )
    entes = [ente for ente in job.entes if ente != "BR"]
    if entes:
        stmt = stmt.where(MartCoberturaFonte.cod_ibge.in_(entes))
    anos = [int(p) for p in job.periodos if str(p).isdigit() and len(str(p)) == 4]
    if anos:
        stmt = stmt.where(MartCoberturaFonte.ano.in_(anos))
    return int(session.scalar(stmt) or 0)


def _rodar_checks_pos_carga(
    session: Session, job: IngestJob, entes: list[str]
) -> dict[str, Any]:
    """Executa os checks de qualidade dos entes carregados e emite os alertas."""
    from app.modules.quality import service as quality_service

    entes_verificados = 0
    total_falha = 0
    total_aviso = 0
    total_alertas = 0
    codigos_falha: set[str] = set()
    for cod in entes:
        periodo = _ultimo_periodo_rreo(session, cod)
        if periodo is None:
            continue
        try:
            saida = quality_service.executar_e_alertar(
                session, cod, periodo, job_id=job.id, org_id=job.org_id
            )
        except Exception:  # noqa: BLE001 — verificação não pode derrubar a carga
            continue
        entes_verificados += 1
        total_falha += saida.falha
        total_aviso += saida.aviso
        total_alertas += saida.alertas_emitidos
        codigos_falha |= set(saida.codigos_falha)
    return {
        "entes": entes_verificados,
        "falha": total_falha,
        "aviso": total_aviso,
        "alertas": total_alertas,
        "codigos_falha": sorted(codigos_falha),
    }


def _ultimo_periodo_rreo(session: Session, cod_ibge: str) -> str | None:
    from app.modules.ingestion.models import DimEntrega

    return session.scalar(
        select(DimEntrega.periodo)
        .where(
            DimEntrega.cod_ibge == cod_ibge,
            DimEntrega.relatorio == "RREO",
            DimEntrega.vigente.is_(True),
        )
        .order_by(DimEntrega.periodo.desc())
        .limit(1)
    )


def _recalcular(session: Session, job: IngestJob, entes_ok: list[str]) -> dict[str, Any]:
    """Materializa gold/cobertura e falha explicitamente se a fase pós-job quebrar."""
    entes = [ente for ente in entes_ok if ente != "BR"]
    nacional_ok = "BR" in entes_ok
    if not entes and not nacional_ok:
        return {
            "indicadores_recalculados": [],
            "cobertura_antes": None,
            "cobertura_depois": None,
            "delta_cobertura": None,
        }

    from app.modules.ingestion import cobertura as cobertura_mod
    from app.workers import materialize

    stats = materialize.materialize_scope(entes) if entes else {}
    if int(stats.get("falhas", 0)) > 0:
        raise RuntimeError(
            f"Materialização pós-job falhou para {stats['falhas']} ente(s)."
        )

    recalculados: list[str] = []
    if job.fonte == "tesouro_capag":
        # CAPAG chega como uma entrega nacional BR, mas alimenta fato por ente.
        # O INSERT…SELECT bulk é idempotente pela versão da entrega.
        materialize.materialize_capag_todos(session)
        recalculados.append("gold.fato_capag")

    # Serializa o refresh global para impedir deltas corrompidos por dois jobs concorrentes.
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _COVERAGE_LOCK})
    antes = _cobertura_count(session, job)
    cobertura_mod.refresh_cobertura(session)
    depois = _cobertura_count(session, job)

    if int(stats.get("rreo", 0)) > 0:
        recalculados.extend(
            [
                "gold.mart_indicador",
                "gold.fato_receita",
                "gold.fato_despesa",
                "gold.fato_resultado",
            ]
        )
    if int(stats.get("rgf", 0)) > 0:
        recalculados.extend(
            ["gold.fato_pessoal", "gold.fato_divida", "gold.fato_caixa_rap"]
        )
    if int(stats.get("carteira", 0)) > 0:
        recalculados.append("gold.mart_carteira")
    if int(stats.get("dca", 0)) > 0:
        recalculados.append("gold.fato_balanco")
    recalculados.append("gold.mart_cobertura_fonte")

    # Sprint 26: toda carga termina verificando o que acabou de materializar. Rodar os
    # checks aqui — e não num job separado — garante que nenhum dado entra em produção
    # sem passar pelas invariantes; o resultado fica em gold.data_quality_check.
    qualidade = _rodar_checks_pos_carga(session, job, entes)
    recalculados.append("gold.data_quality_check")
    return {
        "qualidade": qualidade,
        "indicadores_recalculados": sorted(set(recalculados)),
        "cobertura_antes": antes,
        "cobertura_depois": depois,
        "delta_cobertura": depois - antes,
    }
