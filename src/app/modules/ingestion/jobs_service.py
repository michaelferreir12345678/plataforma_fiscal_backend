"""Regras dos jobs de ingestão (Central de Dados, Sprint 24).

O banco é a fonte de verdade do ciclo de vida. Criação, confirmação, cancelamento e retry
são auditados com JSON completo; as transições disputadas usam UPDATE condicional no
repository. A execução em si é entregue ao RQ pelo módulo de worker.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError, ScopeForbiddenError
from app.modules.ingestion import integracoes, jobs_repository
from app.modules.ingestion.connectors.registry import CONNECTOR_REGISTRY, FONTE_META
from app.modules.ingestion.jobs_models import (
    STATUS_EXECUTANDO,
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
    SaudeFila,
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
    meta = FONTE_META.get(fonte)
    if meta is not None and meta.agrupar_por_uf:
        ufs: list[str] = []
        for codigo in normalizados:
            if not codigo.isdigit() or len(codigo) not in (2, 7):
                raise AppError(
                    status=422,
                    title="Código IBGE inválido",
                    detail=(
                        f"'{codigo}' não identifica UF nem município. "
                        "Use 2 dígitos para UF ou 7 para município."
                    ),
                )
            uf = codigo if len(codigo) == 2 else codigo[:2]
            if uf not in ufs:
                ufs.append(uf)
        return ufs
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
    meta = FONTE_META.get(create.fonte)
    anos = (
        [meta.ano_fixo]
        if meta is not None and meta.ano_fixo is not None
        else list(payload.get("anos") or create.anos)
    )
    periodos = list(payload.get("periodos") or [])
    if create.fonte == "bcb":
        return len(payload.get("series") or (11, 189, 433))

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
    fonte: str,
    entes: list[str],
) -> None:
    permitidos = scope.carteira_scope_ibges(session, principal)
    meta = FONTE_META.get(fonte)
    if meta is not None and meta.agrupar_por_uf:
        ufs_permitidas = {
            codigo[:2] for codigo in permitidos if codigo.isdigit() and len(codigo) in (2, 7)
        }
        for uf in entes:
            if uf not in ufs_permitidas:
                raise ScopeForbiddenError(uf)
        return
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
    meta = FONTE_META.get(fonte)
    if (
        tipo != "replay"
        and not anos
        and not _fonte_nacional(fonte)
        and not (meta is not None and meta.ano_fixo is not None)
    ):
        raise AppError(status=422, title="Sem exercícios", detail="Informe ao menos um ano.")

    _validar_assinatura(session, principal)
    _validar_entes_no_escopo(session, principal, fonte, entes)


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
    meta = FONTE_META.get(create.fonte)
    anos = (
        [meta.ano_fixo]
        if meta is not None and meta.ano_fixo is not None
        else list(create.anos)
    )
    payload = {
        **extras,
        "fonte": create.fonte,
        "entes": [] if entes == ["BR"] and _fonte_nacional(create.fonte) else entes,
        "anos": anos,
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
    if run_payload is not None and create.tipo != "replay":
        # O endpoint legado entrega seu payload pronto; normalize-o também, para que o
        # worker use exatamente as mesmas unidades persistidas no job durável.
        run_payload = {
            **run_payload,
            "entes": [] if entes == ["BR"] and _fonte_nacional(create.fonte) else entes,
        }
        meta = FONTE_META.get(create.fonte)
        if meta is not None and meta.ano_fixo is not None:
            run_payload["anos"] = [meta.ano_fixo]
        if meta is not None and meta.agrupar_por_uf and not run_payload.get("versao"):
            # Persiste uma captura única no job: todas as UFs e eventuais retries usam
            # a mesma versão, mas uma nova execução pode reativar conteúdo já visto.
            run_payload["versao"] = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    estimativa = estimar(create, run_payload=run_payload)
    chaves_planejadas = (
        list(create.periodos)
        if create.tipo == "replay"
        else [str(a) for a in ((run_payload or {}).get("anos") or create.anos)]
    )

    audit_base = {
        "job_id": None,
        "fonte": create.fonte,
        "tipo": create.tipo,
        "entes": entes,
        "entes_solicitados": list(create.entes),
        "periodos": chaves_planejadas,
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

    periodos_display = chaves_planejadas
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
                len(entes) * max(len(chaves_planejadas), 1),
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


def saude_fila(session: Session) -> SaudeFila:
    """Diz se existe alguém consumindo a fila — e não só quantos jobs esperam.

    Sem isso, um worker ausente é indistinguível de um worker ocupado: os dois exibem
    ``na_fila``. Quatro jobs já ficaram parados por dez minutos sem uma linha de aviso.
    """
    aguardando = jobs_repository.contar_por_status(session, STATUS_NA_FILA)
    executando = jobs_repository.contar_por_status(session, STATUS_EXECUTANDO)
    consumidores, vivos, profundidade, redis_ok, detalhe = ingest_jobs.inspecionar_fila()

    saude = SaudeFila(
        consumidores=consumidores,
        consumidores_vivos=vivos,
        aguardando=aguardando,
        executando=executando,
        fila_redis=profundidade,
        redis_disponivel=redis_ok,
        detalhe=detalhe,
    )
    if ingest_jobs.eager_enabled():
        # No modo de teste a execução é inline: não há consumidor e não cabe alarme.
        return saude.model_copy(update={"consumidores_vivos": max(vivos, 1), "detalhe": None})
    if saude.detalhe is None and saude.parada:
        saude.detalhe = (
            "Nenhum worker de ingestão está consumindo a fila. Os jobs continuam "
            "persistidos e começam sozinhos assim que um worker subir "
            "(`python -m app.workers.ingest_worker`)."
        )
    elif saude.detalhe is None and aguardando and vivos == 0 and executando:
        saude.detalhe = (
            "Há execução em curso, mas nenhum worker deu sinal de vida recente. Se o "
            "processo tiver caído, o lease é recuperado automaticamente e o job volta "
            "para a fila sem perder o que já foi carregado."
        )
    return saude


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
    _validar_entes_no_escopo(session, principal, atual.fonte, list(atual.entes))
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


# --- Leque de fontes (atualizar tudo de um exercício) ------------------------

#: Sentinela aceita em ``IngestJobCreate.fonte`` para dizer "todas as fontes".
FONTE_TODAS = "*"


def _ordem_topologica(fontes: list[str]) -> list[str]:
    """Ordena respeitando ``dependencias`` do registro.

    RGF depende de RREO; o cronograma do SADIPEM depende das operações contratadas; o
    PDF dos mínimos depende do RREO. Disparar fora de ordem não quebra — cada conector
    é idempotente —, mas faz a fonte derivada rodar contra uma base que ainda não
    chegou, e o resultado é uma lacuna que só some na próxima carga.
    """
    pendentes = list(fontes)
    saida: list[str] = []
    visto: set[str] = set()
    # Kahn simplificado: o grafo é pequeno e raso, e um ciclo (que não deve existir)
    # não pode travar a carga — o resto entra na ordem em que veio.
    while pendentes:
        avancou = False
        for fonte in list(pendentes):
            meta = FONTE_META.get(fonte)
            deps = set(meta.dependencias) if meta else set()
            if deps <= visto or not (deps & set(pendentes)):
                saida.append(fonte)
                visto.add(fonte)
                pendentes.remove(fonte)
                avancou = True
        if not avancou:
            saida.extend(pendentes)
            break
    return saida


def fontes_do_leque(session: Session) -> tuple[list[str], list[str]]:
    """``(a disparar, ignoradas)`` — respeitando os toggles de integração (Sprint 18).

    Fonte com integração desligada **não** entra: o toggle existe justamente para
    pausar um conector, e "atualizar tudo" não pode ser um atalho que o contorna.

    Fonte que exige configuração ausente também fica fora. Ela falharia em toda execução,
    sempre pelo mesmo motivo — e falha previsível não é informação: ensina o operador a
    ignorar o painel de erros, e aí o erro que importa passa despercebido.
    """
    disparar: list[str] = []
    ignoradas: list[str] = []
    for fonte, meta in FONTE_META.items():
        if getattr(meta, "requer_configuracao", None):
            ignoradas.append(fonte)
        elif integracoes.integracao_ativa(session, fonte):
            disparar.append(fonte)
        else:
            ignoradas.append(fonte)
    return _ordem_topologica(disparar), sorted(ignoradas)


def criar_leque(
    session: Session, principal: Principal, create: IngestJobCreate
) -> list[IngestJobCreateResult]:
    """Cria **um job por fonte** para o mesmo exercício.

    Deliberadamente não é um job só: um mega-job que atravessa 17 conectores e três
    APIs externas falha inteiro por causa de um, não diz onde parou e não dá para
    retomar pela metade. Um job por fonte mantém cada um observável na fila, com
    retentativa própria — e o operador vê qual fonte travou.

    Fonte de escopo **nacional** (BCB, CAPAG, cadastro de entes) não recebe a lista de
    entes: iterá-la por município repetiria a mesma chamada 185 vezes.
    """
    fontes, _ignoradas = fontes_do_leque(session)
    resultados: list[IngestJobCreateResult] = []
    for fonte in fontes:
        meta = FONTE_META.get(fonte)
        sem_entes = bool(meta and not meta.consulta_por_ente)
        pedido = create.model_copy(
            update={
                "fonte": fonte,
                "entes": [] if sem_entes else list(create.entes),
                # A confirmação já foi dada para o leque inteiro; repeti-la por fonte
                # transformaria um clique em dezessete.
                "confirmar": True,
            }
        )
        resultados.append(_criar(session, principal, pedido))
    return resultados


def estimar_leque(
    session: Session, create: IngestJobCreate, fontes: list[str]
) -> int:
    """Soma das estimativas de cada fonte — o alcance real do clique, antes dele."""
    total = 0
    for fonte in fontes:
        meta = FONTE_META.get(fonte)
        sem_entes = bool(meta and not meta.consulta_por_ente)
        pedido = create.model_copy(
            update={"fonte": fonte, "entes": [] if sem_entes else list(create.entes)}
        )
        total += estimar(pedido)
    return total
