"""Endpoints admin de ingestão (§ Sprint 1). Todos exigem a capacidade 'administrar'."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_db, require_capability
from app.core.errors import AppError
from app.modules.ingestion import jobs_service, service
from app.modules.ingestion.jobs_schemas import (
    IngestJobCreate,
    IngestJobCreateResult,
    IngestJobOut,
    RetificacaoItem,
    SaudeFila,
)
from app.modules.ingestion.schemas import (
    CoberturaResponse,
    DataResponse,
    EntregaStatus,
    FonteCatalogo,
    IntegracaoOut,
    IntegracaoPatch,
    ProcedenciaOut,
    RunRequest,
)
from app.shared.ingestion.client import ClientResolver, RealClientResolver

router = APIRouter(prefix="/admin/ingestion", tags=["ingestion"])

# Router separado para os toggles de integração (mesmo módulo, prefixo /admin/integracoes).
integracoes_router = APIRouter(prefix="/admin/integracoes", tags=["admin"])


@integracoes_router.get("", response_model=list[IntegracaoOut])
def listar_integracoes(
    _: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> list[IntegracaoOut]:
    """Uma linha por fonte das Sprints 1/1B, com o toggle atual (semeia se faltar)."""
    return service.list_integracoes(session)


@integracoes_router.patch("/{codigo}", response_model=IntegracaoOut)
def patch_integracao(
    codigo: str,
    _: IntegracaoPatch,
    __: Principal = Depends(require_capability("administrar")),
) -> IntegracaoOut:
    """Bloqueia mutação global por administradores de um tenant.

    ``op.integracao`` não possui ``org_id``: pausar uma família interrompe a ingestão
    de todas as organizações. Essa operação pertence ao control plane da plataforma.
    """
    raise AppError(
        status=403,
        title="Operação global restrita",
        detail=(
            f"A integração '{codigo.upper()}' só pode ser alterada pelo control plane "
            "da plataforma."
        ),
        type_="urn:plataforma-fiscal:error:platform-admin-required",
    )


def get_client_resolver() -> Iterator[ClientResolver]:
    """Resolve o cliente HTTP por fonte (sobrescrito nos testes por um resolver falso)."""
    resolver = RealClientResolver()
    try:
        yield resolver
    finally:
        resolver.close()


@router.get("/status", response_model=list[EntregaStatus])
def ingestion_status(
    fonte: str | None = Query(None, description="Filtra por fonte (ex.: siconfi_rreo)."),
    _: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> list[EntregaStatus]:
    return service.status(session, fonte=fonte)


@router.post("/run", response_model=IngestJobCreateResult, status_code=202)
def ingestion_run(
    req: RunRequest,
    response: Response,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
    resolver: ClientResolver = Depends(get_client_resolver),
) -> IngestJobCreateResult:
    """Adapta o contrato legado para um job durável e retorna sem esperar a ingestão."""
    result = jobs_service.criar_job_legacy_run(
        session,
        principal,
        req,
        eager_resolver=resolver,
    )
    if result.precisa_confirmacao:
        response.status_code = 200
    return result


@router.post("/replay", response_model=IngestJobCreateResult, status_code=202)
def ingestion_replay(
    response: Response,
    ente: str = Query(..., description="Código IBGE do ente afetado."),
    periodo: str = Query(..., description="Período canônico (ex.: 2024-B6)."),
    fonte: str | None = Query(None, description="Fonte específica; vazio = todas."),
    confirmar: bool = Query(False, description="Confirma explicitamente uma ação custosa."),
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
    resolver: ClientResolver = Depends(get_client_resolver),
) -> IngestJobCreateResult:
    """Adapta o replay legado para um job durável, executado fora da requisição."""
    result = jobs_service.criar_job_legacy_replay(
        session,
        principal,
        ente=ente,
        periodo=periodo,
        fonte=fonte,
        confirmar=confirmar,
        eager_resolver=resolver,
    )
    if result.precisa_confirmacao:
        response.status_code = 200
    return result


@router.get("/fontes", response_model=list[FonteCatalogo])
def ingestion_fontes(
    _: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> list[FonteCatalogo]:
    """Catálogo das fontes + última execução/OK, período recente e defasagem (spec 1B)."""
    return service.listar_fontes(session)


@router.get("/fontes/{fonte}/procedencia", response_model=ProcedenciaOut)
def ingestion_fonte_procedencia(
    fonte: str,
    _: Principal = Depends(require_capability("administrar")),
) -> ProcedenciaOut:
    """De onde exatamente vem o dado desta fonte: endereços, parâmetros e exemplos reais.

    Rastreabilidade de origem — a contraparte, no plano da ingestão, do ``source_ref`` que
    acompanha cada número (§6.3). O ``source_ref`` diz de qual entrega o valor saiu; isto
    diz de qual endereço a entrega saiu, e com quais parâmetros.

    Não toca no banco: a procedência é declaração de código, conferida contra os conectores
    na suíte de testes.
    """
    return service.obter_procedencia(fonte)


@router.get("/cobertura", response_model=CoberturaResponse)
def ingestion_cobertura(
    fonte: str | None = Query(None, description="Filtra por fonte (ex.: siconfi_rreo)."),
    uf: str | None = Query(None, description="Filtra pela UF (2 dígitos IBGE, ex.: 23=CE)."),
    ano: int | None = Query(None, description="Filtra pelo exercício."),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    _: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> CoberturaResponse:
    """Matriz por período; cada página seleciona grupos completos fonte×ente×ano."""
    return service.cobertura(session, fonte=fonte, uf=uf, ano=ano, page=page, page_size=page_size)


@router.post("/cobertura/refresh", response_model=dict)
def ingestion_cobertura_refresh(
    _: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> dict:
    """Re-materializa ``gold.mart_cobertura_fonte`` a partir do estado atual do medallion."""
    n = service.refresh_cobertura(session)
    return {"linhas": n}


@router.get("/data", response_model=DataResponse)
def ingestion_data(
    fonte: str = Query(..., description="Fonte tipada (siconfi_rreo/rgf/dca/msc)."),
    ente: str = Query(..., description="Código IBGE."),
    periodo: str = Query(..., description="Período canônico."),
    as_of: datetime | None = Query(
        None, description="Reproduz a versão vigente naquele instante (§6.5)."
    ),
    _: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> DataResponse:
    """Leitura silver 'as of': versão vigente (default) ou histórica (``as_of``)."""
    return service.read_data(session, fonte=fonte, ente=ente, periodo=periodo, as_of=as_of)


# --- Central de Dados: jobs assíncronos de ingestão (Sprint 24) ---
# Todos os pontos de entrada, inclusive /run e /replay, convergem para a mesma fila.
@router.post("/jobs", response_model=IngestJobCreateResult, status_code=202)
def criar_job(
    body: IngestJobCreate,
    response: Response,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> IngestJobCreateResult:
    """Enfileira um job de ingestão. Acima do limiar, exige ``confirmar=true`` (ação custosa).

    ``fonte="*"`` abre o **leque**: um job por fonte ativa, na ordem de dependência.
    Deliberadamente não é um job só — dezessete conectores e três APIs externas num
    job único falham inteiros por causa de um, e não dizem onde pararam.
    """
    if body.fonte == jobs_service.FONTE_TODAS:
        fontes, ignoradas = jobs_service.fontes_do_leque(session)
        if not body.confirmar:
            # O leque é sempre ação custosa: confirmar por fonte viraria dezessete
            # cliques, então a confirmação é uma só, aqui, com o alcance à vista.
            response.status_code = 200
            return IngestJobCreateResult(
                precisa_confirmacao=True,
                estimativa_itens=jobs_service.estimar_leque(session, body, fontes),
                limiar=0,
            )
        resultados = jobs_service.criar_leque(session, principal, body)
        total = sum(r.estimativa_itens for r in resultados)
        return IngestJobCreateResult(
            precisa_confirmacao=False,
            estimativa_itens=total,
            limiar=0,
            job=resultados[0].job if resultados else None,
        )
    result = jobs_service.criar_job(session, principal, body)
    if result.precisa_confirmacao:
        response.status_code = 200  # nada foi aceito ainda: aguarda confirmação
    return result


@router.get("/jobs", response_model=list[IngestJobOut])
def listar_jobs(
    status: str | None = Query(None, description="na_fila|executando|concluido|falhou|cancelado."),
    fonte: str | None = Query(None, description="Filtra por fonte."),
    _: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> list[IngestJobOut]:
    """Jobs do escopo (RLS por org): em andamento e histórico."""
    return jobs_service.listar(session, status=status, fonte=fonte)


@router.get("/jobs/saude", response_model=SaudeFila)
def saude_da_fila(
    _: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> SaudeFila:
    """Existe worker consumindo? Sem isso, 'Na fila' é indistinguível de 'ninguém escuta'."""
    return jobs_service.saude_fila(session)


@router.get("/jobs/{job_id}", response_model=IngestJobOut)
def obter_job(
    job_id: uuid.UUID,
    _: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> IngestJobOut:
    """Progresso, erros por item e resultado (contrato do polling do frontend)."""
    return jobs_service.obter(session, job_id)


@router.post("/jobs/{job_id}/cancelar", response_model=IngestJobOut)
def cancelar_job(
    job_id: uuid.UUID,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> IngestJobOut:
    """Cancela um job **só enquanto está na fila** (impede a execução)."""
    return jobs_service.cancelar(session, principal, job_id)


@router.post("/jobs/{job_id}/retry", response_model=IngestJobOut)
def retry_job(
    job_id: uuid.UUID,
    principal: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> IngestJobOut:
    """Reexecuta um job que **falhou**, reprocessando apenas as unidades com erro."""
    return jobs_service.retry(session, principal, job_id)


@router.get("/retificacoes", response_model=list[RetificacaoItem])
def listar_retificacoes(
    desde: datetime | None = Query(None, description="Só retificações homologadas a partir daqui."),
    _: Principal = Depends(require_capability("administrar")),
    session: Session = Depends(get_db),
) -> list[RetificacaoItem]:
    """Entregas que superaram uma versão anterior (retificação bitemporal, §6.5)."""
    return jobs_service.retificacoes(session, desde=desde)
