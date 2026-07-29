"""Regras do monitor de limites (Módulo 3): lista, detalhe (memória/providências) e simulador."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.catalog import repository as catalog_repo
from app.modules.catalog import service as catalog_service
from app.modules.indicators import repository as indicators_repo
from app.modules.indicators import service as indicators_service
from app.modules.indicators.limites import LimiteLegal, classificar_faixa
from app.modules.limits import repository
from app.modules.limits.schemas import (
    LimiteDetail,
    LimiteItem,
    LimitesResponse,
    ProvidenciaOut,
    SerieItem,
    SimularRequest,
    SimularResponse,
)
from app.shared.envelope import DrillNodeRef
from app.shared.source_ref import SourceRef

# Indicadores do semáforo (Módulo 1) e o poder associado (quando houver).
SEMAFORO_INDICADORES = (
    "pessoal_executivo",
    "divida_consolidada_liquida",
    "saude_minimo",
    "educacao_mde",
)
INDICADOR_PODER = {"pessoal_executivo": "Executivo"}

# Providências legais por (indicador, faixa) — DADO (§9: apontar o dispositivo, não decidir).
_PROVIDENCIAS: list[tuple[str, str, str, str]] = [
    ("pessoal_executivo", "alerta",
     "Emissão de alerta pelo Tribunal de Contas ao ente.", "LRF art. 59, §1º, II"),
    ("pessoal_executivo", "prudencial",
     "Vedações do art. 22, parágrafo único: nomeação, aumento, hora extra e novas contratações.",
     "LRF art. 22, parágrafo único"),
    ("pessoal_executivo", "excedido",
     "Recondução ao limite em até 2 quadrimestres; vedações do art. 23.", "LRF art. 23"),
    ("divida_consolidada_liquida", "prudencial",
     "Monitoramento reforçado da trajetória da dívida consolidada líquida.", "LRF art. 59"),
    ("divida_consolidada_liquida", "excedido",
     "Recondução ao limite em 3 quadrimestres; vedação a novas operações de crédito.",
     "Res. SF 40/2001; LRF art. 31"),
    ("saude_minimo", "insuficiente",
     "Aplicação mínima em ASPS não atingida; recomposição no exercício seguinte.",
     "LC 141/2012 art. 25"),
    ("educacao_mde", "insuficiente",
     "Aplicação mínima em MDE (25%) não atingida.", "CF art. 212"),
    ("fundeb_profissionais", "insuficiente",
     "Percentual mínimo do FUNDEB em profissionais da educação não atingido.",
     "Lei 14.113/2020 art. 26"),
]


def seed_providencias(session: Session) -> None:
    """Popula ``gold.dim_providencia_legal`` (idempotente)."""
    for indicador, faixa, texto, base in _PROVIDENCIAS:
        repository.upsert_providencia(
            session, {"indicador": indicador, "faixa": faixa, "texto": texto, "base_legal": base}
        )


def _poder(indicador: str) -> str:
    return INDICADOR_PODER.get(indicador, "")


def _limite_dim(session: Session, indicador: str, esfera: str) -> LimiteLegal | None:
    row = catalog_repo.get_limite(
        session, indicador=indicador, esfera=esfera, poder=_poder(indicador)
    )
    if row is None:
        return None
    return LimiteLegal(
        indicador=row.indicador, esfera=row.esfera, poder=row.poder, sentido=row.sentido,
        teto_pct=row.teto_pct, alerta_pct=row.alerta_pct, prudencial_pct=row.prudencial_pct,
    )


def _distancias(
    sentido: str, valor_pct: Decimal | None, teto: Decimal, alerta: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    if valor_pct is None:
        return None, None
    if sentido == "piso":
        return valor_pct - teto, None  # folga acima do piso
    return teto - valor_pct, (alerta - valor_pct if alerta is not None else None)


def _esfera_do_ente(session: Session, cod_ibge: str) -> str:
    ente = catalog_service.refresh_dim_ente(session, cod_ibge)
    if ente is None or ente.esfera is None:
        raise AppError(
            status=422, title="Esfera desconhecida",
            detail=f"dim_ente sem esfera para {cod_ibge}.",
        )
    return ente.esfera


def build_limites(session: Session, cod_ibge: str, periodo: str) -> LimitesResponse:
    """Todos os limites do ente/período com faixa e distância ao teto/alerta."""
    esfera = _esfera_do_ente(session, cod_ibge)
    versao = indicators_repo.resolve_versao_rreo(session, cod_ibge=cod_ibge, periodo=periodo)
    itens: list[LimiteItem] = []
    if versao is not None:
        for mart in repository.list_mart_by_periodo(
            session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
        ):
            limite = _limite_dim(session, mart.indicador, esfera)
            sentido = limite.sentido if limite else "teto"
            teto = mart.teto_pct
            if teto is None:
                teto = limite.teto_pct if limite else Decimal(0)
            alerta = limite.alerta_pct if limite else None
            dist_teto, dist_alerta = _distancias(sentido, mart.valor_pct_rcl, teto, alerta)
            itens.append(
                LimiteItem(
                    indicador=mart.indicador,
                    esfera=esfera,
                    sentido=sentido,
                    valor_rs=mart.valor_rs,
                    valor_pct_rcl=mart.valor_pct_rcl,
                    faixa=mart.faixa,
                    teto_pct=teto,
                    alerta_pct=alerta,
                    prudencial_pct=limite.prudencial_pct if limite else None,
                    distancia_teto=dist_teto,
                    distancia_alerta=dist_alerta,
                    denominador=mart.denominador,
                    base_valor=mart.base_valor,
                )
            )
    return LimitesResponse(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao or "",
        itens=itens,
        source_ref=SourceRef(relatorio="RREO", periodo=periodo, versao_entrega=versao),
    )


def _periodo_breadcrumb(session: Session, periodo: str) -> list[DrillNodeRef]:
    return catalog_service.periodo_breadcrumb(session, periodo)


def _serie_historica(session: Session, cod_ibge: str, indicador: str) -> list[SerieItem]:
    serie: list[SerieItem] = []
    for periodo in repository.distinct_periodos_mart(
        session, cod_ibge=cod_ibge, indicador=indicador
    ):
        versao = indicators_repo.resolve_versao_rreo(
            session, cod_ibge=cod_ibge, periodo=periodo
        )
        if versao is None:
            continue
        mart = indicators_repo.get_mart_indicador(
            session, cod_ibge=cod_ibge, periodo=periodo, indicador=indicador, versao_entrega=versao
        )
        if mart is not None:
            serie.append(
                SerieItem(
                    periodo=periodo, valor_pct_rcl=mart.valor_pct_rcl,
                    faixa=mart.faixa, valor_rs=mart.valor_rs,
                )
            )
    return serie


def build_limite_detail(
    session: Session, cod_ibge: str, periodo: str, indicador: str, *, as_of: datetime | None = None
) -> LimiteDetail:
    """Detalhe do indicador: memória de cálculo, providências (da faixa) e série histórica."""
    esfera = _esfera_do_ente(session, cod_ibge)
    versao = indicators_repo.resolve_versao_rreo(
        session, cod_ibge=cod_ibge, periodo=periodo, as_of=as_of
    )
    if versao is None:
        raise AppError(status=404, title="Sem RREO", detail="Sem RREO vigente para o período.")
    mart = indicators_repo.get_mart_indicador(
        session, cod_ibge=cod_ibge, periodo=periodo, indicador=indicador, versao_entrega=versao
    )
    if mart is None:
        raise AppError(
            status=404, title="Indicador não calculado",
            detail=f"Sem mart_indicador para {indicador} em {periodo}.",
        )
    fato = indicators_repo.get_fato_rcl(
        session, cod_ibge=cod_ibge, periodo_ref=periodo, versao_entrega=versao
    )
    memoria = {
        "valor_rs": str(mart.valor_rs),
        "valor_pct_rcl": str(mart.valor_pct_rcl),
        "teto_pct": str(mart.teto_pct),
        "rcl_12m": str(fato.rcl_12m) if fato else None,
        "rcl_memoria": fato.memoria if fato else None,
        "formula": "valor_pct_rcl = valor_rs / rcl_12m × 100",
    }
    providencias = [
        ProvidenciaOut(faixa=p.faixa, texto=p.texto, base_legal=p.base_legal)
        for p in repository.providencias(session, indicador=indicador, faixa=mart.faixa or "")
    ]
    return LimiteDetail(
        cod_ibge=cod_ibge,
        periodo=periodo,
        indicador=indicador,
        esfera=esfera,
        faixa=mart.faixa,
        valor_rs=mart.valor_rs,
        valor_pct_rcl=mart.valor_pct_rcl,
        teto_pct=mart.teto_pct or Decimal(0),
        memoria=memoria,
        providencias=providencias,
        serie_historica=_serie_historica(session, cod_ibge, indicador),
        periodo_breadcrumb=_periodo_breadcrumb(session, periodo),
        source_ref=SourceRef(
            relatorio="RREO", anexo="Anexo 03", periodo=periodo, versao_entrega=versao
        ),
    )


def simular(
    session: Session,
    cod_ibge: str,
    periodo: str,
    indicador: str,
    req: SimularRequest,
    *,
    as_of: datetime | None = None,
) -> SimularResponse:
    """Recalcula a faixa para um ``novo_valor_rs`` ou ``delta_rs`` — **sem persistir**."""
    esfera = _esfera_do_ente(session, cod_ibge)
    limite = _limite_dim(session, indicador, esfera)
    if limite is None:
        raise AppError(
            status=404, title="Limite não cadastrado",
            detail=f"Sem limite para {indicador}/{esfera}.",
        )
    fato = indicators_service.obter_fato_rcl(session, cod_ibge, periodo, as_of=as_of)
    versao = fato.versao_entrega
    mart = indicators_repo.get_mart_indicador(
        session, cod_ibge=cod_ibge, periodo=periodo, indicador=indicador, versao_entrega=versao
    )
    valor_atual = mart.valor_rs if mart else None
    pct_atual = mart.valor_pct_rcl if mart else None
    faixa_atual = mart.faixa if mart else None

    if req.novo_valor_rs is not None:
        novo_valor = req.novo_valor_rs
    else:
        base = valor_atual if valor_atual is not None else Decimal(0)
        novo_valor = base + (req.delta_rs or Decimal(0))

    pct_simulado = (novo_valor / fato.rcl_12m) * Decimal(100)
    faixa_simulada = classificar_faixa(pct_simulado, limite)

    return SimularResponse(
        indicador=indicador,
        valor_rs_atual=valor_atual,
        valor_rs_simulado=novo_valor,
        valor_pct_atual=pct_atual,
        valor_pct_simulado=pct_simulado,
        faixa_atual=faixa_atual,
        faixa_simulada=faixa_simulada,
        teto_pct=limite.teto_pct,
        persistido=False,
    )
