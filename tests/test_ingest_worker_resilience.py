"""Resiliência determinística do worker RQ da Central de Dados."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from rq.exceptions import DuplicateJobError
from rq.job import Job, JobStatus
from rq.timeouts import JobTimeoutException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import admin_session
from app.modules.ingestion.jobs_models import IngestJob
from app.modules.ingestion.models import IngestionLog
from app.workers import ingest_jobs

FONTE = "siconfi_rreo"


@pytest.fixture
def isolated_runner():
    ingest_jobs.set_recalcular(False)
    ingest_jobs.set_unit_runner(None)
    yield
    ingest_jobs.set_unit_runner(None)
    ingest_jobs.set_recalcular(True)


def _insert_job(
    org_id: uuid.UUID,
    *,
    status: str = "na_fila",
    entes: list[str] | None = None,
    iniciado_em: datetime | None = None,
    tentativas: int = 0,
    resultado: dict[str, Any] | None = None,
) -> uuid.UUID:
    with admin_session() as session:
        job = IngestJob(
            org_id=org_id,
            fonte=FONTE,
            tipo="backfill",
            entes=entes or ["2304400"],
            periodos=["2024"],
            parametros={"run_request": {"fonte": FONTE, "anos": [2024]}},
            status=status,
            itens_total=len(entes or ["2304400"]),
            tentativas=tentativas,
            iniciado_em=iniciado_em,
            resultado=resultado,
        )
        session.add(job)
        session.commit()
        return job.id


def test_timeout_e_terminal_e_repropagado_ao_rq(make_org, isolated_runner):
    org = make_org()
    job_id = _insert_job(org.org_id)

    def timeout(
        session: Session,
        job: IngestJob,
        ente: str,
        chave: str,
    ) -> int:
        del session, job, ente, chave
        raise JobTimeoutException("limite RQ excedido")

    ingest_jobs.set_unit_runner(timeout)
    with pytest.raises(JobTimeoutException, match="limite RQ excedido"):
        ingest_jobs.run_job_async(str(job_id))

    with admin_session() as session:
        job = session.get(IngestJob, job_id)
        assert job is not None
        assert job.status == "falhou"
        assert job.terminado_em is not None
        assert job.resultado["erro_sistema"]["fase"] == "unidade:2304400:2024"
        assert "JobTimeoutException" in job.erro_resumo
        logs = list(
            session.scalars(select(IngestionLog).where(IngestionLog.job_id == job_id))
        )
        assert any("Falha inesperada" in (row.mensagem or "") for row in logs)


def test_falha_na_inicializacao_pos_claim_nao_deixa_executando(
    make_org, isolated_runner, monkeypatch
):
    org = make_org()
    job_id = _insert_job(org.org_id)

    def fail_init(job: IngestJob) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
        del job
        raise RuntimeError("checkpoint ilegível")

    monkeypatch.setattr(ingest_jobs, "_retry_plan", fail_init)
    with pytest.raises(RuntimeError, match="checkpoint ilegível"):
        ingest_jobs.run_job_async(str(job_id))

    with admin_session() as session:
        job = session.get(IngestJob, job_id)
        assert job is not None
        assert job.status == "falhou"
        assert job.terminado_em is not None
        assert job.resultado["erro_sistema"]["fase"] == "inicializacao"


def test_reconciliacao_preserva_checkpoint_e_roda_so_pendentes(
    make_org, isolated_runner
):
    org = make_org()
    entes = ["2304400", "2307650", "2310001"]
    checkpoint = {
        "itens": [
            {
                "ente": entes[0],
                "chave": "2024",
                "ok": True,
                "erro": None,
                "silver_rows": 11,
                "detalhe": {"checkpoint": "preservado"},
            },
            {
                "ente": entes[1],
                "chave": "2024",
                "ok": False,
                "erro": "worker interrompido",
                "silver_rows": 0,
                "detalhe": None,
            },
        ],
        "erro_sistema": None,
    }
    job_id = _insert_job(
        org.org_id,
        status="executando",
        entes=entes,
        iniciado_em=datetime.now(UTC) - timedelta(hours=2),
        tentativas=1,
        resultado=checkpoint,
    )

    cutoff = datetime.now(UTC) - timedelta(
        seconds=ingest_jobs.JOB_TIMEOUT + ingest_jobs.ABANDONED_GRACE_SECONDS
    )
    assert ingest_jobs.reconcile_abandoned(cutoff=cutoff) == 1

    calls: list[tuple[str, str]] = []

    def succeed(
        session: Session,
        job: IngestJob,
        ente: str,
        chave: str,
    ) -> int:
        del session, job
        calls.append((ente, chave))
        return 3

    ingest_jobs.set_unit_runner(succeed)
    ingest_jobs.run_job_async(str(job_id))

    assert calls == [(entes[1], "2024"), (entes[2], "2024")]
    with admin_session() as session:
        job = session.get(IngestJob, job_id)
        assert job is not None
        assert job.status == "concluido"
        assert job.tentativas == 2
        assert job.itens_ok == 3
        assert job.resultado["itens"][0]["detalhe"] == {
            "checkpoint": "preservado"
        }
        assert {item["ente"] for item in job.resultado["itens"]} == set(entes)
        recovered = list(
            session.scalars(
                select(IngestionLog).where(
                    IngestionLog.job_id == job_id,
                    IngestionLog.status == "recuperado",
                )
            )
        )
        assert len(recovered) == 1


def test_reconciliacao_ignora_execucao_ainda_dentro_do_timeout(make_org):
    org = make_org()
    job_id = _insert_job(
        org.org_id,
        status="executando",
        iniciado_em=datetime.now(UTC) - timedelta(minutes=5),
        tentativas=1,
    )
    cutoff = datetime.now(UTC) - timedelta(
        seconds=ingest_jobs.JOB_TIMEOUT + ingest_jobs.ABANDONED_GRACE_SECONDS
    )

    assert ingest_jobs.reconcile_abandoned(cutoff=cutoff) == 0
    with admin_session() as session:
        job = session.get(IngestJob, job_id)
        assert job is not None
        assert job.status == "executando"


def test_retry_de_erro_sistemico_preserva_sucessos_e_inclui_ausentes():
    entes = ["2304400", "2307650", "2310001"]
    job = IngestJob(
        org_id=uuid.uuid4(),
        fonte=FONTE,
        tipo="backfill",
        entes=entes,
        periodos=["2024"],
        parametros={"run_request": {"anos": [2024]}},
        tentativas=2,
        resultado={
            "itens": [
                {"ente": entes[0], "chave": "2024", "ok": True},
                {"ente": entes[1], "chave": "2024", "ok": False},
            ],
            "erro_sistema": {
                "fase": f"unidade:{entes[2]}:2024",
                "erro": "JobTimeoutException",
            },
        },
    )

    sucessos, pendentes = ingest_jobs._retry_plan(job)

    assert [(item["ente"], item["chave"]) for item in sucessos] == [
        (entes[0], "2024")
    ]
    assert pendentes == [(entes[1], "2024"), (entes[2], "2024")]


def test_enqueue_usa_unique_e_callback_de_falha(make_org):
    org = make_org()
    job_id = _insert_job(org.org_id)
    with admin_session() as session:
        job = session.get(IngestJob, job_id)
        assert job is not None
        queue = SimpleNamespace(
            connection=object(),
            calls=[],
        )

        def enqueue_call(**kwargs: Any) -> object:
            queue.calls.append(kwargs)
            return object()

        queue.enqueue_call = enqueue_call
        rq_id = ingest_jobs._enqueue_unique(queue, job)

    assert rq_id == f"ingest-{job_id}-tentativa-1"
    assert len(queue.calls) == 1
    assert queue.calls[0]["unique"] is True
    assert queue.calls[0]["timeout"] == ingest_jobs.JOB_TIMEOUT
    assert queue.calls[0]["on_failure"] is ingest_jobs.rq_failure_callback


def test_enqueue_duplicado_ativo_nao_cria_segunda_entrega(
    make_org, monkeypatch
):
    org = make_org()
    job_id = _insert_job(org.org_id)
    existing = SimpleNamespace(
        get_status=lambda refresh: JobStatus.QUEUED,
        delete=lambda **kwargs: pytest.fail(f"não deveria remover: {kwargs}"),
    )
    monkeypatch.setattr(
        Job,
        "fetch",
        classmethod(lambda cls, job_id, connection: existing),
    )
    calls: list[dict[str, Any]] = []

    class DuplicateQueue:
        connection = object()

        def enqueue_call(self, **kwargs: Any) -> None:
            calls.append(kwargs)
            raise DuplicateJobError

    with admin_session() as session:
        job = session.get(IngestJob, job_id)
        assert job is not None
        rq_id = ingest_jobs._enqueue_unique(DuplicateQueue(), job)

    assert rq_id == f"ingest-{job_id}-tentativa-1"
    assert len(calls) == 1


def test_pos_job_nacional_atualiza_cobertura(
    monkeypatch,
) -> None:
    from app.modules.ingestion import cobertura as cobertura_mod
    from app.workers import materialize

    job = SimpleNamespace(
        fonte="tesouro_fpm",
        entes=["BR"],
        periodos=["2026"],
    )
    contagens = iter([10, 13])
    refresh_calls: list[bool] = []
    monkeypatch.setattr(
        ingest_jobs,
        "_cobertura_count",
        lambda session, current_job: next(contagens),
    )
    monkeypatch.setattr(
        cobertura_mod,
        "refresh_cobertura",
        lambda session: refresh_calls.append(True) or 13,
    )
    monkeypatch.setattr(
        materialize,
        "materialize_scope",
        lambda entes: pytest.fail(f"fonte nacional não materializa escopo: {entes}"),
    )

    with admin_session() as session:
        resultado = ingest_jobs._recalcular(session, job, ["BR"])

    assert refresh_calls == [True]
    assert resultado["cobertura_antes"] == 10
    assert resultado["cobertura_depois"] == 13
    assert resultado["delta_cobertura"] == 3
    # Sprint 26: a verificação de qualidade roda ao fim de toda carga.
    assert resultado["indicadores_recalculados"] == [
        "gold.data_quality_check",
        "gold.mart_cobertura_fonte",
    ]
    assert "qualidade" in resultado


def test_pos_job_malha_atualiza_so_cobertura_sem_materializacao_fiscal(
    monkeypatch,
) -> None:
    from app.modules.ingestion import cobertura as cobertura_mod
    from app.modules.ingestion.models import FONTE_IBGE_MALHA
    from app.workers import materialize

    job = SimpleNamespace(
        fonte=FONTE_IBGE_MALHA,
        entes=["21"],
        periodos=["2022"],
        resultado={"itens": [{"ok": True, "silver_rows": 1}]},
    )
    contagens = iter([0, 1])
    refresh_calls: list[bool] = []
    monkeypatch.setattr(
        ingest_jobs,
        "_cobertura_count",
        lambda session, current_job: next(contagens),
    )
    monkeypatch.setattr(
        cobertura_mod,
        "refresh_cobertura",
        lambda session: refresh_calls.append(True) or 1,
    )
    monkeypatch.setattr(
        materialize,
        "materialize_scope",
        lambda entes: pytest.fail(f"malha não materializa fatos fiscais: {entes}"),
    )
    monkeypatch.setattr(
        ingest_jobs,
        "_rodar_checks_pos_carga",
        lambda session, current_job, entes: pytest.fail(
            f"malha não roda checks de RREO/RGF: {entes}"
        ),
    )

    with admin_session() as session:
        resultado = ingest_jobs._recalcular(session, job, ["21"])

    assert refresh_calls == [True]
    assert resultado == {
        "qualidade": {
            "entes": 0,
            "falha": 0,
            "aviso": 0,
            "alertas": 0,
            "codigos_falha": [],
        },
        "indicadores_recalculados": [
            "gold.geo_malha_uf",
            "gold.mart_cobertura_fonte",
        ],
        "cobertura_antes": 0,
        "cobertura_depois": 1,
        "delta_cobertura": 1,
    }

    job_historico = SimpleNamespace(
        fonte=FONTE_IBGE_MALHA,
        entes=["21"],
        periodos=["2022"],
        resultado={"itens": [{"ok": True, "silver_rows": 0}]},
    )
    contagens_historicas = iter([1, 1])
    monkeypatch.setattr(
        ingest_jobs,
        "_cobertura_count",
        lambda session, current_job: next(contagens_historicas),
    )
    with admin_session() as session:
        historico = ingest_jobs._recalcular(session, job_historico, ["21"])

    assert historico["indicadores_recalculados"] == ["gold.mart_cobertura_fonte"]
    assert historico["delta_cobertura"] == 0


def test_pos_job_capag_materializa_gold_e_cobertura(monkeypatch) -> None:
    from app.modules.ingestion import cobertura as cobertura_mod
    from app.workers import materialize

    job = SimpleNamespace(
        fonte="tesouro_capag",
        entes=["BR"],
        periodos=["2026"],
    )
    contagens = iter([0, 5570])
    capag_calls: list[bool] = []
    monkeypatch.setattr(
        ingest_jobs,
        "_cobertura_count",
        lambda session, current_job: next(contagens),
    )
    monkeypatch.setattr(
        cobertura_mod,
        "refresh_cobertura",
        lambda session: 5570,
    )
    monkeypatch.setattr(
        materialize,
        "materialize_scope",
        lambda entes: pytest.fail(f"CAPAG nacional não materializa escopo: {entes}"),
    )
    monkeypatch.setattr(
        materialize,
        "materialize_capag_todos",
        lambda session: capag_calls.append(True) or 5570,
    )

    with admin_session() as session:
        resultado = ingest_jobs._recalcular(session, job, ["BR"])

    assert capag_calls == [True]
    assert resultado["cobertura_antes"] == 0
    assert resultado["cobertura_depois"] == 5570
    assert resultado["delta_cobertura"] == 5570
    # Sprint 26: toda carga termina verificando o que materializou; a lista passa a
    # incluir a tabela de checks, e o resumo da verificação viaja no resultado do job.
    assert resultado["indicadores_recalculados"] == [
        "gold.data_quality_check",
        "gold.fato_capag",
        "gold.mart_cobertura_fonte",
    ]
    assert "qualidade" in resultado


def test_timeout_do_produtor_nao_sobrescreve_claim_concorrente(make_org):
    org = make_org()
    executing_id = _insert_job(
        org.org_id,
        status="executando",
        iniciado_em=datetime.now(UTC),
        tentativas=1,
    )
    queued_id = _insert_job(org.org_id)

    with admin_session() as session:
        ingest_jobs._queue_failure(
            session,
            executing_id,
            TimeoutError("resposta do Redis perdida após enqueue"),
        )
    with admin_session() as session:
        executing = session.get(IngestJob, executing_id)
        assert executing is not None
        assert executing.status == "executando"

    with admin_session() as session:
        ingest_jobs._queue_failure(
            session,
            queued_id,
            ConnectionError("Redis recusou a conexão"),
        )
    with admin_session() as session:
        queued = session.get(IngestJob, queued_id)
        assert queued is not None
        assert queued.status == "falhou"
        assert queued.resultado["erro_sistema"]["fase"] == "fila"
