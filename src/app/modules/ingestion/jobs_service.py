"""Regras dos jobs de ingestão (Central de Dados, Sprint 24).

O banco é a fonte de verdade do ciclo de vida. Criação, confirmação, cancelamento e retry
são auditados com JSON completo; as transições disputadas usam UPDATE condicional no
repository. A execução em si é entregue ao RQ pelo módulo de worker.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError, ScopeForbiddenError
from app.modules.ingestion import jobs_repository
from app.modules.ingestion.connectors.registry import CONNECTOR_REGISTRY, FONTE_META
from app.modules.ingestion.jobs_models import (
    STATUS_FALHOU,
    STATUS_NA_FILA,
    TIPOS,
    IngestJob,
)
from app.modules.ingestion.jobs_schemas import (
    IngestionLogOut,
    IngestJobCreate,
    IngestJobCreateResult,
    IngestJobOut,
    RetificacaoItem,
)
from app.modules.ingestion.schemas import RunRequest
from app.modules.tenancy import repository as tenancy_repo
from app.shared import scope
from app.workers import ingest_jobs

# Acima deste custo estimado (requisições/unidades internas), exige confirmação explícita.
LIMIAR_CONFIRMACAO = 50
FONTE_TODAS = "todas"

_PERIODOS_PADRAO = {
    "mensal": 12,
    "bimestral": 6,
    "quadrimestral": 3,
    "anual": 1,
    "diaria": 1,
    "continua": 1,
    "eventual": 1,
}


def _fonte_nacional(fonte: str) -> bool:
    meta = FONTE_META.get(fonte)
    return meta is not None and meta.escopo == "nacional"


def _normalizar_entes(fonte: str, entes: list[str]) -> list[str]:
    normalizados = list(dict.fromkeys(str(e).strip() for e in entes if str(e).strip()))
    if not normalizados and _fonte_nacional(fonte):
        return ["BR"]
    return normalizados


def estimar(
    create: IngestJobCreate,
    *,
    run_payload: dict[str, Any] | None = None,
) -> int:
    """Estima o custo real melhor que o simples ``ente × ano``.

    Para fontes periódicas, inclui os períodos internos que o connector ``discover`` cria
    (RREO=6, RGF=3, MSC/mensais=12). Para replay de todas as fontes, inclui o fan-out.
    """
    entes = _normalizar_entes(create.fonte, create.entes)
    if create.tipo == "replay":
        n_fontes = len(CONNECTOR_REGISTRY) if create.fonte == FONTE_TODAS else 1
        return len(entes) * len(create.periodos) * n_fontes

    payload = run_payload or {}
    anos = list(payload.get("anos") or create.anos)
    periodos = list(payload.get("periodos") or [])
    if create.fonte == "bcb":
        return len(payload.get("series") or (11, 189, 433))

    meta = FONTE_META.get(create.fonte)
    multiplicador = (
        len(periodos)
        if periodos
        else _PERIODOS_PADRAO.get(meta.cadencia if meta is not None else "", 1)
    )
    n_entes = 1 if _fonte_nacional(create.fonte) else len(entes)
    return n_entes * max(len(anos), 1) * multiplicador


def _validar_assinatura(session: Session, principal: Principal) -> None:
    if principal.org_id is None:
        raise AppError(status=403, title="Sem organização", detail="Principal sem org ativa.")
    assinatura = tenancy_repo.get_assinatura(session, org_id=principal.org_id)
    if assinatura is not None and assinatura.status != "ativa":
        raise AppError(
            status=403,
            title="Assinatura inativa",
            detail=(
                "A organização possui assinatura "
                f"'{assinatura.status}' e não pode iniciar ingestões."
            ),
            type_="urn:plataforma-fiscal:error:inactive-subscription",
        )


def _validar_entes_no_escopo(
    session: Session,
    principal: Principal,
    entes: list[str],
) -> None:
    permitidos = scope.carteira_scope_ibges(session, principal)
    for ente in entes:
        if ente != "BR" and ente not in permitidos:
            raise ScopeForbiddenError(ente)


def _validar(
    session: Session,
    principal: Principal,
    *,
    fonte: str,
    tipo: str,
    entes: list[str],
    anos: list[int],
    periodos: list[str],
) -> None:
    if fonte not in CONNECTOR_REGISTRY and not (tipo == "replay" and fonte == FONTE_TODAS):
        raise AppError(
            status=404,
            title="Fonte desconhecida",
            detail=f"Fonte '{fonte}' não está no registro de conectores.",
        )
    if tipo not in TIPOS:
        raise AppError(status=422, title="Tipo inválido", detail=f"use {', '.join(TIPOS)}.")
    if not entes:
        raise AppError(status=422, title="Sem entes", detail="Selecione ao menos um ente.")
    if tipo == "replay" and not periodos:
        raise AppError(status=422, title="Sem períodos", detail="Replay exige períodos.")
    if tipo != "replay" and not anos and not _fonte_nacional(fonte):
        raise AppError(status=422, title="Sem exercícios", detail="Informe ao menos um ano.")

    _validar_assinatura(session, principal)
    _validar_entes_no_escopo(session, principal, entes)


def _audit_json(
    session: Session,
    *,
    principal: Principal | None = None,
    org_id: uuid.UUID | None = None,
    usuario_id: uuid.UUID | None = None,
    acao: str,
    payload: dict[str, Any],
) -> None:
    if principal is not None:
        org_id = principal.org_id
        usuario_id = principal.usuario_id
    envelope = {
        "usuario_id": str(usuario_id) if usuario_id else None,
        "org_id": str(org_id) if org_id else None,
        **payload,
    }
    tenancy_repo.insert_audit_log(
        session,
        org_id=org_id,
        usuario_id=usuario_id,
        acao=acao,
        recurso=json.dumps(envelope, ensure_ascii=False, sort_keys=True, default=str),
    )


def audit_job_result(session: Session, job: IngestJob) -> None:
    """Auditoria final chamada pelo worker, inclusive para falha inesperada."""
    _audit_json(
        session,
        org_id=job.org_id,
        usuario_id=job.criado_por,
        acao="ingestao.job.resultado",
        payload={
            "job_id": str(job.id),
            "fonte": job.fonte,
            "tipo": job.tipo,
            "entes": list(job.entes),
            "periodos": list(job.periodos),
            "parametros": job.parametros,
            "resultado": {
                "status": job.status,
                "progresso_pct": job.progresso_pct,
                "itens_total": job.itens_total,
                "itens_ok": job.itens_ok,
                "itens_erro": job.itens_erro,
                "tentativas": job.tentativas,
                "erro_resumo": job.erro_resumo,
                "detalhe": job.resultado,
            },
        },
    )


def _run_payload_from_create(create: IngestJobCreate, entes: list[str]) -> dict[str, Any]:
    extras = dict(create.parametros)
    extras.pop("confirmar", None)
    payload = {
        **extras,
        "fonte": create.fonte,
        "entes": [] if entes == ["BR"] and _fonte_nacional(create.fonte) else entes,
        "anos": list(create.anos),
        "versao": create.versao,
    }
    return RunRequest.model_validate(payload).model_dump(mode="json", exclude={"confirmar"})


def _criar(
    session: Session,
    principal: Principal,
    create: IngestJobCreate,
    *,
    run_payload: dict[str, Any] | None = None,
    eager_resolver: Any | None = None,
) -> IngestJobCreateResult:
    entes = _normalizar_entes(create.fonte, create.entes)
    _validar(
        session,
        principal,
        fonte=create.fonte,
        tipo=create.tipo,
        entes=entes,
        anos=create.anos,
        periodos=create.periodos,
    )
    if run_payload is None and create.tipo != "replay":
        run_payload = _run_payload_from_create(create, entes)
    estimativa = estimar(create, run_payload=run_payload)

    audit_base = {
        "job_id": None,
        "fonte": create.fonte,
        "tipo": create.tipo,
        "entes": entes,
        "periodos": list(create.periodos)
        if create.tipo == "replay"
        else [str(a) for a in create.anos],
        "parametros": run_payload if create.tipo != "replay" else dict(create.parametros),
        "confirmar": create.confirmar,
        "estimativa_itens": estimativa,
        "limiar": LIMIAR_CONFIRMACAO,
    }
    if estimativa > LIMIAR_CONFIRMACAO and not create.confirmar:
        _audit_json(
            session,
            principal=principal,
            acao="ingestao.job.confirmacao_requerida",
            payload={**audit_base, "resultado": {"precisa_confirmacao": True}},
        )
        return IngestJobCreateResult(
            precisa_confirmacao=True,
            estimativa_itens=estimativa,
            limiar=LIMIAR_CONFIRMACAO,
        )

    periodos_display = (
        list(create.periodos) if create.tipo == "replay" else [str(a) for a in create.anos]
    )
    parametros = (
        {
            **dict(create.parametros),
            "replay": {
                "fonte": None if create.fonte == FONTE_TODAS else create.fonte,
            },
            "confirmar": create.confirmar,
        }
        if create.tipo == "replay"
        else {"run_request": run_payload or {}, "confirmar": create.confirmar}
    )
    job = jobs_repository.create_job(
        session,
        {
            "org_id": principal.org_id,
            "criado_por": principal.usuario_id,
            "fonte": create.fonte,
            "tipo": create.tipo,
            "entes": entes,
            "periodos": periodos_display,
            "parametros": parametros,
            "status": STATUS_NA_FILA,
            "itens_total": max(
                len(entes)
                * max(
                    len(create.periodos if create.tipo == "replay" else create.anos),
                    1,
                ),
                1,
            ),
        },
    )
    job.log_ref = f"/admin/ingestion/jobs/{job.id}#logs"
    session.flush()
    _audit_json(
        session,
        principal=principal,
        acao="ingestao.job.criar",
        payload={
            **audit_base,
            "job_id": str(job.id),
            "resultado": {"status": STATUS_NA_FILA, "precisa_confirmacao": False},
        },
    )
    ingest_jobs.enqueue(session, job, eager_resolver=eager_resolver)
    return IngestJobCreateResult(
        precisa_confirmacao=False,
        estimativa_itens=estimativa,
        limiar=LIMIAR_CONFIRMACAO,
        job=_to_out(job),
    )


def criar_job(
    session: Session, principal: Principal, create: IngestJobCreate
) -> IngestJobCreateResult:
    return _criar(session, principal, create)


def criar_job_legacy_run(
    session: Session,
    principal: Principal,
    req: RunRequest,
    *,
    eager_resolver: Any | None = None,
) -> IngestJobCreateResult:
    """Adapta o payload legado completo para o mesmo job persistido/RQ."""
    payload = req.model_dump(mode="json", exclude={"confirmar"})
    create = IngestJobCreate(
        fonte=req.fonte,
        tipo="run",
        entes=list(req.entes),
        anos=list(req.anos),
        versao=req.versao,
        parametros={},
        confirmar=req.confirmar,
    )
    return _criar(
        session,
        principal,
        create,
        run_payload=payload,
        eager_resolver=eager_resolver,
    )


def criar_job_legacy_replay(
    session: Session,
    principal: Principal,
    *,
    ente: str,
    periodo: str,
    fonte: str | None,
    confirmar: bool,
    eager_resolver: Any | None = None,
) -> IngestJobCreateResult:
    create = IngestJobCreate(
        fonte=fonte or FONTE_TODAS,
        tipo="replay",
        entes=[ente],
        periodos=[periodo],
        parametros={"payload_legado": {"ente": ente, "periodo": periodo, "fonte": fonte}},
        confirmar=confirmar,
    )
    return _criar(session, principal, create, eager_resolver=eager_resolver)


def listar(
    session: Session, *, status: str | None = None, fonte: str | None = None
) -> list[IngestJobOut]:
    return [_to_out(j) for j in jobs_repository.list_jobs(session, status=status, fonte=fonte)]


def obter(session: Session, job_id: uuid.UUID) -> IngestJobOut:
    job = jobs_repository.get_job(session, job_id)
    if job is None:
        raise AppError(status=404, title="Job inexistente", detail=str(job_id))
    logs = [
        IngestionLogOut.model_validate(row)
        for row in jobs_repository.list_logs(session, job_id)
    ]
    return _to_out(job, logs=logs)


def cancelar(session: Session, principal: Principal, job_id: uuid.UUID) -> IngestJobOut:
    atual = jobs_repository.get_job(session, job_id)
    if atual is None:
        raise AppError(status=404, title="Job inexistente", detail=str(job_id))
    job = jobs_repository.cancel_job(session, job_id, terminado_em=ingest_jobs.now())
    if job is None:
        session.expire_all()
        atual = jobs_repository.get_job(session, job_id)
        raise AppError(
            status=409,
            title="Não cancelável",
            detail=(
                "Só é possível cancelar um job na fila "
                f"(status atual: {atual.status if atual else 'inexistente'})."
            ),
        )
    _audit_json(
        session,
        principal=principal,
        acao="ingestao.job.cancelar",
        payload={
            "job_id": str(job.id),
            "fonte": job.fonte,
            "tipo": job.tipo,
            "entes": list(job.entes),
            "periodos": list(job.periodos),
            "parametros": job.parametros,
            "resultado": {"status": job.status},
        },
    )
    # O banco é a autoridade do cancelamento. Confirma a transição antes de tocar no
    # Redis; se a remoção da entrega falhar ou um worker já a tiver recebido, o claim SQL
    # verá ``cancelado`` e não executará. A ordem inversa poderia deixar um job ``na_fila``
    # sem entrega caso o commit da requisição falhasse.
    session.commit()
    ingest_jobs.cancel_rq(job)
    return _to_out(job)


def retry(session: Session, principal: Principal, job_id: uuid.UUID) -> IngestJobOut:
    _validar_assinatura(session, principal)
    atual = jobs_repository.get_job(session, job_id)
    if atual is None:
        raise AppError(status=404, title="Job inexistente", detail=str(job_id))
    # O escopo pode ter sido reduzido desde a criação (carteira ou membership_escopo).
    # Retry é uma nova ação privilegiada e deve revalidar o estado de autorização atual.
    _validar_entes_no_escopo(session, principal, list(atual.entes))
    if atual.status != STATUS_FALHOU:
        raise AppError(
            status=409,
            title="Não reexecutável",
            detail=f"Só se reexecuta um job que falhou (status atual: {atual.status}).",
        )
    resultado = atual.resultado or {}
    retry_total = len(ingest_jobs.pending_retry_units(atual))
    tem_item_falho = any(
        not item.get("ok") for item in resultado.get("itens", [])
    )
    if not tem_item_falho and not resultado.get("erro_sistema"):
        raise AppError(
            status=409,
            title="Sem itens reexecutáveis",
            detail="O job falhou, mas não registra itens ou fase de sistema para retry.",
        )

    job = jobs_repository.retry_job(session, job_id, itens_total=retry_total)
    if job is None:
        session.expire_all()
        atual = jobs_repository.get_job(session, job_id)
        raise AppError(
            status=409,
            title="Não reexecutável",
            detail=f"O job já mudou de estado ({atual.status if atual else 'inexistente'}).",
        )
    _audit_json(
        session,
        principal=principal,
        acao="ingestao.job.retry",
        payload={
            "job_id": str(job.id),
            "fonte": job.fonte,
            "tipo": job.tipo,
            "entes": list(job.entes),
            "periodos": list(job.periodos),
            "parametros": job.parametros,
            "resultado": {"status": job.status, "itens_reenfileirados": retry_total},
        },
    )
    ingest_jobs.enqueue(session, job)
    return _to_out(job)


def retificacoes(session: Session, *, desde: datetime | None = None) -> list[RetificacaoItem]:
    return jobs_repository.retificacoes(session, desde=desde)


def _to_out(
    job: IngestJob, *, logs: list[IngestionLogOut] | None = None
) -> IngestJobOut:
    result = IngestJobOut.model_validate(job)
    if logs is not None:
        result = result.model_copy(update={"logs": logs})
    return result
