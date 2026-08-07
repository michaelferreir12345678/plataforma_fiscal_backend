"""Regras de orquestração da ingestão (§7: regra só no service)."""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError
from app.modules.ingestion import cobertura as cobertura_mod
from app.modules.ingestion import integracoes, repository
from app.modules.ingestion.connectors import procedencia
from app.modules.ingestion.connectors.registry import (
    CONNECTOR_REGISTRY,
    FONTE_META,
    FONTE_RELATORIO,
)
from app.modules.ingestion.connectors.siconfi import valid_time_from_periodo
from app.modules.ingestion.models import (
    FONTE_DCA,
    FONTE_MSC,
    FONTE_RGF,
    FONTE_RREO,
    SilverDca,
    SilverMsc,
    SilverRgf,
    SilverRreo,
)
from app.modules.ingestion.repository import MedallionRepository
from app.modules.ingestion.schemas import (
    CoberturaItem,
    CoberturaResponse,
    CoberturaResumo,
    DataResponse,
    EndpointOut,
    EntregaStatus,
    FonteCatalogo,
    IntegracaoOut,
    ParametroOut,
    ProcedenciaOut,
    RunRequest,
    RunResult,
)
from app.shared.ingestion.base import BaseConnector, IngestionJob
from app.shared.ingestion.client import ClientResolver
from app.shared.scope import assert_ente_in_scope
from app.shared.source_ref import SourceRef

# Silver tipado por relatório (leitura as_of). MSC tem forma própria.
SILVER_MODEL_BY_FONTE: dict[str, type] = {
    FONTE_RREO: SilverRreo,
    FONTE_RGF: SilverRgf,
    FONTE_DCA: SilverDca,
    FONTE_MSC: SilverMsc,
}

_COBERTURA_CACHE_TTL_SECONDS = 30.0
_COBERTURA_CACHE_MAX_ENTRIES = 32
_COBERTURA_CACHE_MIN_ROWS = 100
_CoberturaIdentity = tuple[int, datetime | None]
_CoberturaCacheKey = tuple[
    str | None,
    str | None,
    int | None,
    int,
    int,
    _CoberturaIdentity,
]


@dataclass(frozen=True)
class _CachedCobertura:
    stored_at: float
    response: CoberturaResponse


_cobertura_cache: OrderedDict[_CoberturaCacheKey, _CachedCobertura] = OrderedDict()
_cobertura_cache_lock = threading.Lock()


def _cobertura_cache_get(key: _CoberturaCacheKey) -> CoberturaResponse | None:
    now = time.monotonic()
    with _cobertura_cache_lock:
        cached = _cobertura_cache.get(key)
        if cached is None:
            return None
        if now - cached.stored_at > _COBERTURA_CACHE_TTL_SECONDS:
            del _cobertura_cache[key]
            return None
        _cobertura_cache.move_to_end(key)
        return cached.response


def _cobertura_cache_put(
    key: _CoberturaCacheKey,
    response: CoberturaResponse,
) -> None:
    with _cobertura_cache_lock:
        _cobertura_cache[key] = _CachedCobertura(
            stored_at=time.monotonic(),
            response=response,
        )
        _cobertura_cache.move_to_end(key)
        while len(_cobertura_cache) > _COBERTURA_CACHE_MAX_ENTRIES:
            _cobertura_cache.popitem(last=False)


def _connector(fonte: str, client: Any) -> BaseConnector:
    cls = CONNECTOR_REGISTRY.get(fonte)
    if cls is None:
        raise AppError(
            status=400,
            title="Fonte desconhecida",
            detail=f"Fonte '{fonte}' não registrada. Válidas: {sorted(CONNECTOR_REGISTRY)}",
        )
    return cls(client, MedallionRepository())


def run(session: Session, resolver: ClientResolver, req: RunRequest) -> RunResult:
    """Executa o backfill de ``req.fonte`` para os entes/anos/períodos informados.

    Respeita o toggle de ``op.integracao`` (Sprint 18): se a família da fonte está
    desligada, o conector **não roda** — nem o cliente HTTP é resolvido.
    """
    if not integracoes.integracao_ativa(session, req.fonte):
        familia = integracoes.FAMILY_BY_FONTE.get(req.fonte, req.fonte)
        return RunResult(
            fonte=req.fonte,
            total_jobs=0,
            ingeridos=0,
            pulados=0,
            silver_rows=0,
            versoes_vigentes=[],
            pausado=True,
            observacao=f"Integração '{familia}' desligada em op.integracao; conector pausado.",
        )
    connector = _connector(req.fonte, resolver.get(req.fonte))
    state: dict[str, Any] = {**req.model_dump(), "session": session}
    # Parâmetros extras de fontes de arquivo (url/escopo/formato/…) sobem para o topo.
    state.update(state.pop("params", {}) or {})
    jobs = connector.discover(state)
    ingeridos = pulados = silver_rows = 0
    versoes: set[str] = set()
    for job in jobs:
        result = connector.run(session, job, force=req.force)
        if result.status == "ingested":
            ingeridos += 1
        else:
            pulados += 1
        silver_rows += result.silver_rows
        if result.vigente:
            versoes.add(result.versao_entrega)

    return RunResult(
        fonte=req.fonte,
        total_jobs=len(jobs),
        ingeridos=ingeridos,
        pulados=pulados,
        silver_rows=silver_rows,
        versoes_vigentes=sorted(versoes),
    )


def _parse_periodo(periodo: str) -> tuple[int, int, str]:
    """``2024-B6`` → (2024, 6, 'B'); ``2024`` → (2024, 0, 'A'); ``2024-M07`` → (2024, 7, 'M')."""
    ano = int(str(periodo)[:4])
    if "-" not in periodo:
        return ano, 0, "A"
    marca = periodo.split("-", 1)[1]
    return ano, int(marca[1:]), marca[:1]


def _build_job_periodo(
    connector: BaseConnector,
    *,
    cod_ibge: str,
    periodo: str,
    versao: str,
    homologada_em: datetime | None,
) -> IngestionJob:
    """Constrói UM job para (ente, período) exato — usado na varredura de retificações."""
    from app.modules.ingestion.connectors.siconfi import RgfConnector

    ano, num, tipo = _parse_periodo(periodo)
    if isinstance(connector, RgfConnector):
        connector._periodicidade = tipo if tipo in ("Q", "S") else "Q"
    return connector.build_job(cod_ibge, ano, num, versao, homologada_em)  # type: ignore[attr-defined]


# Relatórios reingeríveis pela varredura de retificações (têm silver tipado + API).
_RETIFICAVEIS = {
    "RREO": FONTE_RREO,
    "RGF": FONTE_RGF,
    "DCA": FONTE_DCA,
    "MSC": FONTE_MSC,
}


def varrer_retificacoes(
    session: Session,
    resolver: ClientResolver,
    *,
    ente: str,
    relatorio: str | None = None,
    periodo: str | None = None,
) -> RunResult:
    """Varre ``silver.siconfi_extratos`` do ente e reprocessa as entregas ainda ausentes.

    Cada linha do extrato traz o evento de entrega (``data_status``) por relatório/período.
    Um ``data_status`` que ainda não tem entrega correspondente em ``gold.dim_entrega``
    indica uma **retificação** (ou primeira carga): reingerimos aquele relatório/período
    com ``versao = data_status`` e ``homologada_em = data_status``. Como a homologação é a
    mais recente, a nova entrega torna-se vigente **sem apagar** a anterior — e ``as_of``
    anterior reproduz a versão prévia (§6.5).

    ``relatorio``/``periodo`` restringem a varredura: o agendador varre só o que é recente
    e a apuração de um caso específico não precisa reprocessar o histórico inteiro do ente
    (cada reprocesso é uma ida à rede + payload grande em memória).
    """
    ingeridos = pulados = silver_rows = 0
    versoes: set[str] = set()
    for extrato in repository.list_extratos(session, cod_ibge=ente):
        if relatorio is not None and extrato.relatorio != relatorio:
            continue
        if periodo is not None and extrato.periodo != periodo:
            continue
        fonte = _RETIFICAVEIS.get(extrato.relatorio)
        if fonte is None or not integracoes.integracao_ativa(session, fonte):
            continue
        # Já existe entrega com essa versão? Então o evento já foi processado.
        if repository.entrega_existe(
            session,
            cod_ibge=ente,
            relatorio=extrato.relatorio,
            periodo=extrato.periodo,
            versao_entrega=extrato.versao,
        ):
            pulados += 1
            continue
        connector = _connector(fonte, resolver.get(fonte))
        job = _build_job_periodo(
            connector,
            cod_ibge=ente,
            periodo=extrato.periodo,
            versao=extrato.versao,
            homologada_em=extrato.homologada_em,
        )
        result = connector.run(session, job)
        if result.status == "ingested":
            ingeridos += 1
            silver_rows += result.silver_rows
            versoes.add(result.versao_entrega)
        else:
            pulados += 1
    return RunResult(
        fonte="retificacoes",
        total_jobs=ingeridos + pulados,
        ingeridos=ingeridos,
        pulados=pulados,
        silver_rows=silver_rows,
        versoes_vigentes=sorted(versoes),
    )


def _job_from_bronze(fonte: str, bronze: Any) -> IngestionJob:
    return IngestionJob(
        fonte=fonte,
        relatorio=FONTE_RELATORIO[fonte],
        cod_ibge=bronze.cod_ibge,
        ano=int(str(bronze.periodo)[:4]),
        periodo=bronze.periodo,
        versao=bronze.versao,
        homologada_em=None,
        valid_time=valid_time_from_periodo(bronze.periodo),
    )


def replay(
    session: Session,
    resolver: ClientResolver,
    *,
    ente: str,
    periodo: str,
    fonte: str | None = None,
) -> RunResult:
    """Reprocessa o silver a partir do bronze já armazenado (sem tocar a rede).

    Usado quando um extrato de entregas indica que o ente/período foi afetado.
    """
    fontes = [fonte] if fonte else list(CONNECTOR_REGISTRY)
    repo = MedallionRepository()
    total = silver_rows = 0
    versoes: set[str] = set()
    for f in fontes:
        if not integracoes.integracao_ativa(session, f):
            continue  # integração desligada: não reprocessa esse conector
        connector = _connector(f, resolver.get(f))
        for bronze in repository.list_bronze(session, fonte=f, cod_ibge=ente, periodo=periodo):
            job = _job_from_bronze(f, bronze)
            entrega = repo.register_entrega(session, job, bronze.hash_payload)
            silver_rows += connector.to_silver(session, job, bronze.payload, bronze.versao)
            if entrega.vigente:
                versoes.add(entrega.versao_entrega)
            total += 1
    return RunResult(
        fonte=fonte or "todas",
        total_jobs=total,
        ingeridos=total,
        pulados=0,
        silver_rows=silver_rows,
        versoes_vigentes=sorted(versoes),
    )


def status(session: Session, *, fonte: str | None = None) -> list[EntregaStatus]:
    """Status por ente/relatório/período (versões e vigência)."""
    relatorio = FONTE_RELATORIO.get(fonte) if fonte else None
    entregas = repository.list_entregas(session, relatorio=relatorio)
    return [
        EntregaStatus(
            cod_ibge=e.cod_ibge,
            relatorio=e.relatorio,
            periodo=e.periodo,
            versao_entrega=e.versao_entrega,
            vigente=e.vigente,
            homologada_em=e.homologada_em,
        )
        for e in entregas
    ]


def _integracao_out(row: Any) -> IntegracaoOut:
    return IntegracaoOut(
        id=str(row.id),
        codigo=row.codigo,
        nome=row.nome,
        descricao=row.descricao,
        categoria=row.categoria,
        ativo=row.ativo,
        fontes=list(row.fontes or []),
        atualizado_em=row.atualizado_em,
    )


def list_integracoes(session: Session) -> list[IntegracaoOut]:
    """Lista as integrações (materialize-on-read: semeia as 9 famílias se faltarem)."""
    if repository.count_integracoes(session) < len(integracoes.FAMILIES):
        integracoes.seed_integracoes(session)
    return [_integracao_out(r) for r in repository.list_integracoes(session)]


def set_integracao_ativa(
    session: Session, *, codigo: str, ativo: bool, atualizado_por: uuid.UUID | None
) -> IntegracaoOut:
    """Liga/desliga uma integração; a mudança afeta a orquestração dos conectores."""
    if repository.count_integracoes(session) < len(integracoes.FAMILIES):
        integracoes.seed_integracoes(session)
    row = repository.set_integracao_ativo(
        session, codigo=codigo.upper(), ativo=ativo, atualizado_por=atualizado_por
    )
    if row is None:
        raise AppError(
            status=404,
            title="Integração não encontrada",
            detail=f"Código '{codigo}'. Válidos: {', '.join(integracoes.FAMILY_CODES)}.",
        )
    return _integracao_out(row)


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {col.name: getattr(row, col.name) for col in row.__table__.columns}


def read_data(
    session: Session,
    *,
    principal: Principal,
    fonte: str,
    ente: str,
    periodo: str,
    as_of: datetime | None = None,
) -> DataResponse:
    """Leitura silver 'as of' (§6.5): resolve a versão efetiva e retorna suas linhas.

    **A22 (E1):** o gate de escopo é conferido aqui, e não só no roteador, porque é dentro
    dele que vive o gate de **licença**. O dado do SICONFI é público, então não há
    vazamento entre organizações; o que estava furado era o contrato comercial — uma conta
    licenciada para um município lia o silver de qualquer um dos 5.598. Repetir o
    ``assert`` no serviço fecha o caminho programático (worker, script, outro módulo), que
    não passa pelo roteador.
    """
    assert_ente_in_scope(session, principal, ente)
    model = SILVER_MODEL_BY_FONTE.get(fonte)
    if model is None:
        raise AppError(
            status=400,
            title="Fonte sem leitura tipada",
            detail=f"Leitura as_of não suportada para a fonte '{fonte}'.",
        )
    relatorio = FONTE_RELATORIO[fonte]
    versao = repository.resolve_versao(
        session, cod_ibge=ente, relatorio=relatorio, periodo=periodo, as_of=as_of
    )
    rows: list[dict[str, Any]] = []
    if versao is not None:
        rows = [
            _row_to_dict(r)
            for r in repository.read_silver(
                session, model, cod_ibge=ente, periodo=periodo, versao_entrega=versao
            )
        ]
    return DataResponse(
        fonte=fonte,
        cod_ibge=ente,
        periodo=periodo,
        versao_entrega=versao,
        as_of=as_of,
        total=len(rows),
        rows=rows,
        source_ref=SourceRef(relatorio=relatorio, periodo=periodo, versao_entrega=versao),
    )


# --- Catálogo de fontes e cobertura (Sprint 21 / instrumento da Sprint 20) ---
def refresh_cobertura(session: Session) -> int:
    """Materializa ``gold.mart_cobertura_fonte`` (idempotente). Retorna nº de linhas."""
    cobertura_mod.seed_catalogo(session)
    return cobertura_mod.refresh_cobertura(session)


def _acesso_da_fonte(fonte: str) -> str | None:
    """Tipo de acesso da fonte, quando declarado. Fonte sem procedência não inventa rótulo."""
    p = procedencia.PROCEDENCIA.get(fonte)
    return p.acesso if p is not None else None


def obter_procedencia(fonte: str) -> ProcedenciaOut:
    """Origem completa de uma fonte: endereços, parâmetros explicados e exemplos reais.

    É material de auditoria: quem desconfia de um número tem de conseguir chegar à mesma
    consulta que a plataforma fez. Por isso a resposta traz a URL de exemplo já com valores
    reais — clicável — e não apenas o modelo com marcadores.
    """
    dados = procedencia.PROCEDENCIA.get(fonte)
    meta = FONTE_META.get(fonte)
    if dados is None or meta is None:
        raise AppError(
            status=404,
            title="Fonte desconhecida",
            detail=f"Não há fonte {fonte!r} no catálogo de ingestão.",
        )
    return ProcedenciaOut(
        fonte=fonte,
        descricao=meta.descricao,
        orgao=meta.orgao,
        familia=meta.familia,
        cadencia=meta.cadencia,
        acesso=dados.acesso,
        acesso_rotulo=procedencia.ACESSO_ROTULO[dados.acesso],
        portal=dados.portal,
        documentacao=dados.documentacao,
        licenca=dados.licenca,
        autenticacao=dados.autenticacao,
        como_funciona=dados.como_funciona,
        endpoints=[
            EndpointOut(
                metodo=e.metodo,
                url=e.url,
                formato=e.formato,
                o_que_traz=e.o_que_traz,
                parametros=[
                    ParametroOut(
                        nome=x.nome, exemplo=x.exemplo, significado=x.significado
                    )
                    for x in e.parametros
                ],
                exemplo=e.exemplo,
                observacao=e.observacao,
            )
            for e in dados.endpoints
        ],
        paginas_impactadas=list(meta.paginas_impactadas),
        dependencias=list(meta.dependencias),
        requer_configuracao=meta.requer_configuracao,
    )


def listar_fontes(session: Session) -> list[FonteCatalogo]:
    """Catálogo persistido + observabilidade (execução, defasagem e cobertura)."""
    # Materialize-on-read garante que um deploy com fonte nova atualize o catálogo
    # mesmo antes do primeiro refresh de cobertura. A leitura abaixo, contudo, vem
    # exclusivamente da tabela: entradas adicionais persistidas também aparecem.
    cobertura_mod.seed_catalogo(session)
    if repository.count_integracoes(session) < len(integracoes.FAMILIES):
        integracoes.seed_integracoes(session)
    runs = repository.last_run_por_fonte(session)
    cob = repository.cobertura_por_fonte(session)
    itens: list[FonteCatalogo] = []
    for meta in repository.list_catalogo_fontes(session):
        run = runs.get(meta.fonte, {})
        c = cob.get(meta.fonte, {})
        itens.append(
            FonteCatalogo(
                fonte=meta.fonte,
                familia=meta.familia,
                relatorio=meta.relatorio,
                descricao=meta.descricao,
                cadencia=meta.cadencia,
                orgao=meta.orgao,
                url_origem=meta.url_origem,
                escopo=meta.escopo,
                parser_versao=meta.parser_versao,
                paginas_impactadas=list(meta.paginas_impactadas),
                dependencias=list(meta.dependencias),
                ativo=integracoes.integracao_ativa(session, meta.fonte),
                ultima_execucao=run.get("ultima"),
                ultima_execucao_ok=run.get("ultima_ok"),
                periodo_mais_recente=c.get("periodo_recente"),
                defasagem_periodos=c.get("defasagem"),
                entes_cobertos=c.get("entes", 0),
                registros_cobertos=c.get("registros", 0),
                tipo_acesso=_acesso_da_fonte(meta.fonte),
            )
        )
    return itens


def cobertura(
    session: Session,
    *,
    fonte: str | None = None,
    uf: str | None = None,
    ano: int | None = None,
    page: int = 1,
    page_size: int = 100,
) -> CoberturaResponse:
    """Matriz por período, paginada em grupos completos fonte×ente×ano."""
    identity = repository.cobertura_identity(
        session,
        fonte=fonte,
        uf=uf,
        ano=ano,
    )
    cache_key: _CoberturaCacheKey | None = None
    if identity[0] >= _COBERTURA_CACHE_MIN_ROWS:
        cache_key = (fonte, uf, ano, page, page_size, identity)
        cached = _cobertura_cache_get(cache_key)
        if cached is not None:
            return cached

    rows, total = repository.query_cobertura(
        session, fonte=fonte, uf=uf, ano=ano, page=page, page_size=page_size
    )
    total_linhas, entes, periodos, fontes = repository.cobertura_resumo(
        session, fonte=fonte, uf=uf, ano=ano
    )
    response = CoberturaResponse(
        data=[
            CoberturaItem(
                fonte=r.fonte,
                cod_ibge=r.cod_ibge,
                uf=r.uf,
                ano=r.ano,
                periodo=r.periodo,
                n_registros=r.n_registros,
                versao_entrega_vigente=r.versao_entrega_vigente,
                ingerido_em=r.ingerido_em,
                defasagem_periodos=r.defasagem_periodos,
            )
            for r in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
        resumo=CoberturaResumo(
            total_linhas=total_linhas, entes=entes, periodos=periodos, fontes=fontes
        ),
    )
    if cache_key is not None:
        _cobertura_cache_put(cache_key, response)
    return response
