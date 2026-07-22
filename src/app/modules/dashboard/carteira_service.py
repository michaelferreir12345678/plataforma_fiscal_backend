"""Regras da visão agregada de carteira (Módulo 2, Sprint 4).

Consome a gold (``mart_carteira``, materializada de ``mart_indicador``) e a carteira
operacional (``op``, sob RLS). Não recalcula indicadores fiscais — apenas projeta a
faixa em conformidade, ordena por risco e consolida por faixa. O escopo (carteira,
subconjunto do usuário, ampliação estadual por UF) vem de ``shared/scope.py``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError, ScopeForbiddenError
from app.modules.catalog import repository as catalog_repo
from app.modules.catalog.models import DimEnte
from app.modules.dashboard import carteira_repository as carteira_repo
from app.modules.dashboard.carteira_schemas import (
    CarteiraEnteRow,
    CarteiraLoteRequest,
    CarteiraMapaResponse,
    CarteiraResumoResponse,
    IndicadorFaixa,
    LoteJobOut,
    MapaEnte,
    ResumoIndicador,
)
from app.modules.dashboard.models import LOTE_ACOES, MartCarteira
from app.modules.dashboard.service import COR_POR_FAIXA
from app.modules.indicators import repository as indicators_repo
from app.modules.limits import repository as limits_repo
from app.modules.tenancy import repository as tenancy_repo
from app.shared import scope
from app.shared.envelope import ListEnvelope
from app.shared.pagination import PageParams, paginate
from app.shared.source_ref import SourceRef

# faixa (por indicador) -> status de conformidade do ente
FAIXA_CONFORMIDADE: dict[str, str] = {
    "normal": "conforme",
    "adequado": "conforme",
    "alerta": "alerta",
    "prudencial": "prudencial",
    "excedido": "critico",
    "insuficiente": "critico",
}
# severidade crescente (para pior conformidade e ranking de risco)
CONFORMIDADE_SEVERIDADE: dict[str, int] = {
    "sem_dados": 0, "conforme": 0, "alerta": 1, "prudencial": 2, "critico": 3,
}
CONFORMIDADE_COR: dict[str, str] = {
    "conforme": "verde", "alerta": "amarelo", "prudencial": "laranja",
    "critico": "vermelho", "sem_dados": "cinza",
}
_STATUSES: tuple[str, ...] = ("conforme", "alerta", "prudencial", "critico", "sem_dados")


def conformidade_de_faixa(faixa: str | None) -> str:
    """Projeta a faixa de um indicador no status de conformidade do ente."""
    return FAIXA_CONFORMIDADE.get(faixa or "", "sem_dados")


def _cor_faixa(faixa: str | None) -> str:
    return COR_POR_FAIXA.get(faixa, "cinza")


def _porte(populacao: int | None) -> str | None:
    """Porte por população (faixas usuais; ``None`` se população desconhecida)."""
    if populacao is None:
        return None
    if populacao < 50_000:
        return "pequeno"
    if populacao < 200_000:
        return "medio"
    if populacao < 1_000_000:
        return "grande"
    return "metropole"


# --- Materialização (mart_indicador -> mart_carteira) ---
def refresh_mart_carteira(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> int:
    """Materializa o snapshot vigente de um ente/período em ``mart_carteira``.

    Lê os indicadores já calculados (``mart_indicador`` da versão vigente) e projeta
    faixa + conformidade. Retorna quantas linhas foram materializadas.
    """
    versao = indicators_repo.resolve_versao_rreo(
        session, cod_ibge=cod_ibge, periodo=periodo, as_of=as_of
    )
    if versao is None:
        return 0
    n = 0
    for mart in limits_repo.list_mart_by_periodo(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
    ):
        carteira_repo.upsert_mart_carteira(
            session,
            {
                "cod_ibge": cod_ibge,
                "periodo": periodo,
                "indicador": mart.indicador,
                "faixa": mart.faixa,
                "valor_pct": mart.valor_pct_rcl,
                "conformidade_status": conformidade_de_faixa(mart.faixa),
                "versao_entrega": versao,
            },
        )
        n += 1
    return n


def refresh_scope(session: Session, principal: Principal, periodo: str) -> int:
    """Materializa ``mart_carteira`` para todos os entes no escopo (operacional)."""
    total = 0
    for cod in sorted(scope.carteira_scope_ibges(session, principal)):
        total += refresh_mart_carteira(session, cod, periodo)
    return total


# --- Consolidação / ranking / mapa ---
def _mart_por_ente(
    session: Session, cods: set[str], periodo: str
) -> dict[str, list[MartCarteira]]:
    por_ente: dict[str, list[MartCarteira]] = defaultdict(list)
    for row in carteira_repo.list_mart_by_scope(session, cods_ibge=cods, periodo=periodo):
        por_ente[row.cod_ibge].append(row)
    return por_ente


def _pior_conformidade(rows: list[MartCarteira]) -> str:
    if not rows:
        return "sem_dados"
    pior = max(rows, key=lambda r: CONFORMIDADE_SEVERIDADE[r.conformidade_status])
    return pior.conformidade_status


def _source_ref(periodo: str) -> SourceRef:
    return SourceRef(relatorio="RREO", periodo=periodo)


def build_resumo(
    session: Session, principal: Principal, periodo: str
) -> CarteiraResumoResponse:
    """Consolidação por faixa/conformidade no escopo (drill UP do ente para o todo)."""
    scope_cods = scope.carteira_scope_ibges(session, principal)
    por_ente = _mart_por_ente(session, scope_cods, periodo)

    por_conformidade = {s: 0 for s in _STATUSES}
    for cod in scope_cods:
        por_conformidade[_pior_conformidade(por_ente.get(cod, []))] += 1

    ind_total: Counter[str] = Counter()
    ind_faixa: dict[str, Counter[str]] = defaultdict(Counter)
    ind_conf: dict[str, Counter[str]] = defaultdict(Counter)
    for rows in por_ente.values():
        for r in rows:
            ind_total[r.indicador] += 1
            ind_faixa[r.indicador][r.faixa or "sem_faixa"] += 1
            ind_conf[r.indicador][r.conformidade_status] += 1

    por_indicador = [
        ResumoIndicador(
            indicador=ind,
            total=ind_total[ind],
            por_faixa=dict(ind_faixa[ind]),
            por_conformidade=dict(ind_conf[ind]),
        )
        for ind in sorted(ind_total)
    ]
    return CarteiraResumoResponse(
        periodo=periodo,
        total_entes=len(scope_cods),
        entes_com_dados=len(por_ente),
        por_conformidade=por_conformidade,
        por_indicador=por_indicador,
        source_ref=_source_ref(periodo),
    )


def _ente_row(
    cod: str,
    dim: DimEnte | None,
    rows: list[MartCarteira],
    carteira_meta: tuple[str | None, str | None],
) -> CarteiraEnteRow:
    indicadores = [
        IndicadorFaixa(
            indicador=r.indicador,
            faixa=r.faixa,
            cor=_cor_faixa(r.faixa),
            valor_pct=r.valor_pct,
            conformidade_status=r.conformidade_status,
        )
        for r in rows
    ]
    conformidade = _pior_conformidade(rows)
    risco_score = sum(CONFORMIDADE_SEVERIDADE[r.conformidade_status] for r in rows)
    grupo, tag = carteira_meta
    populacao = dim.populacao if dim is not None else None
    return CarteiraEnteRow(
        cod_ibge=cod,
        nome=dim.nome if dim is not None else None,
        uf=dim.uf if dim is not None else None,
        regiao=dim.regiao if dim is not None else None,
        porte=_porte(populacao),
        populacao=populacao,
        grupo=grupo,
        tag=tag,
        conformidade=conformidade,
        cor=CONFORMIDADE_COR[conformidade],
        risco_score=risco_score,
        indicadores=indicadores,
    )


def _ordenar(rows: list[CarteiraEnteRow], ordenar: str) -> list[CarteiraEnteRow]:
    if ordenar == "nome":
        return sorted(rows, key=lambda r: ((r.nome or "").lower(), r.cod_ibge))
    if ordenar == "codigo":
        return sorted(rows, key=lambda r: r.cod_ibge)
    # risco (default): pior conformidade, depois soma de severidades, depois código.
    return sorted(
        rows,
        key=lambda r: (-CONFORMIDADE_SEVERIDADE[r.conformidade], -r.risco_score, r.cod_ibge),
    )


def list_entes(
    session: Session,
    principal: Principal,
    periodo: str,
    *,
    params: PageParams,
    ordenar: str = "risco",
    porte: str | None = None,
    regiao: str | None = None,
    tag: str | None = None,
) -> ListEnvelope[CarteiraEnteRow]:
    """Grade/ranking paginado do escopo; ordenável por risco, filtrável por porte/região/tag."""
    scope_cods = scope.carteira_scope_ibges(session, principal)
    por_ente = _mart_por_ente(session, scope_cods, periodo)
    dim_map = {d.cod_ibge: d for d in catalog_repo.list_dim_entes(session, scope_cods)}
    carteira_rows = (
        tenancy_repo.list_carteira(session, principal.org_id)
        if principal.org_id is not None
        else []
    )
    carteira_map = {c.cod_ibge: (c.grupo, c.tag) for c in carteira_rows}

    universe = set(dim_map) | set(por_ente)
    linhas = [
        _ente_row(cod, dim_map.get(cod), por_ente.get(cod, []), carteira_map.get(cod, (None, None)))
        for cod in universe
    ]

    if porte is not None:
        linhas = [r for r in linhas if r.porte == porte]
    if regiao is not None:
        linhas = [r for r in linhas if (r.regiao or "").lower() == regiao.lower()]
    if tag is not None:
        linhas = [r for r in linhas if r.tag == tag]

    linhas = _ordenar(linhas, ordenar)
    total = len(linhas)
    page_items = linhas[params.offset : params.offset + params.limit]
    return paginate(page_items, total, params, source_ref=_source_ref(periodo))


def build_mapa(
    session: Session,
    principal: Principal,
    periodo: str,
    *,
    indicador: str | None = None,
) -> CarteiraMapaResponse:
    """Uma cor por ente para o coroplético. ``indicador`` ausente ⇒ pior conformidade."""
    scope_cods = scope.carteira_scope_ibges(session, principal)
    dim_entes = catalog_repo.list_dim_entes(session, scope_cods)
    por_ente = _mart_por_ente(session, scope_cods, periodo)

    entes: list[MapaEnte] = []
    if indicador is not None:
        for dim in dim_entes:
            row = next(
                (r for r in por_ente.get(dim.cod_ibge, []) if r.indicador == indicador), None
            )
            faixa = row.faixa if row else None
            status = row.conformidade_status if row else "sem_dados"
            entes.append(
                MapaEnte(
                    cod_ibge=dim.cod_ibge, uf=dim.uf, faixa=faixa, cor=_cor_faixa(faixa),
                    valor_pct=row.valor_pct if row else None, conformidade_status=status,
                )
            )
        legenda = {f: _cor_faixa(f) for f in sorted({e.faixa for e in entes if e.faixa})}
    else:
        for dim in dim_entes:
            status = _pior_conformidade(por_ente.get(dim.cod_ibge, []))
            entes.append(
                MapaEnte(
                    cod_ibge=dim.cod_ibge, uf=dim.uf, faixa=None, cor=CONFORMIDADE_COR[status],
                    valor_pct=None, conformidade_status=status,
                )
            )
        legenda = dict(CONFORMIDADE_COR)

    entes.sort(key=lambda e: e.cod_ibge)
    return CarteiraMapaResponse(
        periodo=periodo, indicador=indicador, legenda=legenda, entes=entes,
        source_ref=_source_ref(periodo),
    )


# --- Ações em lote ---
def enfileirar_lote(
    session: Session, principal: Principal, acao: str, req: CarteiraLoteRequest
) -> LoteJobOut:
    """Valida escopo e persiste um job de lote (relatório/alerta) — enfileirado."""
    if acao not in LOTE_ACOES:
        raise AppError(
            status=404, title="Ação inválida",
            detail=f"Ação '{acao}' desconhecida (use {', '.join(LOTE_ACOES)}).",
        )
    if principal.org_id is None:
        raise AppError(status=400, title="Sem organização", detail="Principal sem org ativa.")

    scope_cods = scope.carteira_scope_ibges(session, principal)
    if req.entes:
        selecionados = list(dict.fromkeys(req.entes))
        for ente in selecionados:
            if ente not in scope_cods:
                raise ScopeForbiddenError(ente)
    else:
        selecionados = sorted(scope_cods)

    if not selecionados:
        raise AppError(
            status=422, title="Escopo vazio",
            detail="Nenhum ente no escopo para a ação em lote.",
        )

    job = carteira_repo.insert_lote_job(
        session,
        org_id=principal.org_id,
        acao=acao,
        periodo=req.periodo,
        entes=selecionados,
        filtro={"periodo": req.periodo} if req.periodo else None,
        criado_por=principal.usuario_id,
    )
    return LoteJobOut.model_validate(job)
