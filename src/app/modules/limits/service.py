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
from app.modules.ingestion import repository as ingestion_repo
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
from app.shared.ausencia import ausencia_de_entrega
from app.shared.envelope import DrillNodeRef
from app.shared.source_ref import SourceRef

# Indicadores do semáforo (Módulo 1) e o poder associado (quando houver).
SEMAFORO_INDICADORES = (
    "pessoal_executivo",
    "divida_consolidada_liquida",
    # Os dois limites de endividamento que a plataforma declarava e nunca apurava. A
    # primeira apuração já encontrou um município com 16,80% de operações de crédito
    # contra o teto de 16% — estouro que ninguém tinha como ver.
    "operacoes_credito",
    "garantias",
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
    ("operacoes_credito", "alerta",
     "Emissão de alerta pelo Tribunal de Contas ao ente.", "LRF art. 59, §1º, II"),
    ("operacoes_credito", "prudencial",
     "Contratação de novas operações exige demonstração de que o limite não será "
     "ultrapassado; o Ministério da Fazenda verifica antes de autorizar.",
     "Res. 43/2001 do Senado, art. 7º, I · LRF art. 32"),
    ("operacoes_credito", "excedido",
     "Vedada a contratação de nova operação de crédito enquanto o limite estiver "
     "ultrapassado, ressalvado o refinanciamento do principal da dívida mobiliária.",
     "Res. 43/2001 do Senado, art. 7º · LRF art. 33"),
    ("garantias", "alerta",
     "Emissão de alerta pelo Tribunal de Contas ao ente.", "LRF art. 59, §1º, II"),
    ("garantias", "prudencial",
     "Nova garantia depende de contragarantia em valor igual ou superior e de "
     "adimplência do garantido.", "LRF art. 40, §1º · Res. 43/2001, art. 9º"),
    ("garantias", "excedido",
     "Vedada a concessão de nova garantia enquanto o montante exceder o limite.",
     "Res. 43/2001 do Senado, art. 9º"),
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
    sentido: str, valor_pct: Decimal | None, teto: Decimal | None, alerta: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    # Sem teto não existe distância até o teto. Devolver 0 diria "está exatamente no
    # limite" para um indicador que não tem limite nenhum.
    if valor_pct is None or teto is None:
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


def build_limites(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> LimitesResponse:
    """Todos os limites do ente/período com faixa e distância ao teto/alerta."""
    esfera = _esfera_do_ente(session, cod_ibge)
    versao = indicators_repo.resolve_versao_rreo(
        session, cod_ibge=cod_ibge, periodo=periodo, as_of=as_of
    )
    efetivo_as_of = (
        ingestion_repo.effective_as_of(
            session,
            cod_ibge=cod_ibge,
            relatorio="RREO",
            periodo=periodo,
            versao_entrega=versao,
            requested=as_of,
        )
        if versao is not None
        else as_of
    )
    itens: list[LimiteItem] = []
    if versao is not None:
        for mart in repository.list_mart_by_periodo(
            session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
        ):
            limite = _limite_dim(session, mart.indicador, esfera)
            if limite is None:
                # A17: sem entrada em dim_limite_legal, o indicador é **gerencial**
                # (rcl_per_capita, investimento_rcl, resultado_primario_rcl —
                # registrados sem faixa/teto por indicators/gerenciais.py, de
                # propósito). O Monitor de Limites é a tela de conformidade contra
                # teto/piso legal; sem limite, não há "distância ao teto" nem "faixa"
                # que façam sentido aqui — o item herdava a formatação de moeda em
                # milhões e o rótulo "teto 0%" de um indicador medido em R$/hab
                # (Fortaleza: R$ 4.870,66/hab virava "R$ 0,0 M"). Esses indicadores já
                # têm o lugar certo: o Benchmarking (Módulo 12), que os formata pela
                # unidade real (`formatBenchmarkValue`).
                continue
            sentido = limite.sentido
            teto = mart.teto_pct if mart.teto_pct is not None else limite.teto_pct
            alerta = limite.alerta_pct
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
                    prudencial_pct=limite.prudencial_pct,
                    distancia_teto=dist_teto,
                    distancia_alerta=dist_alerta,
                    denominador=mart.denominador,
                    base_valor=mart.base_valor,
                )
            )
    return LimitesResponse(
        cod_ibge=cod_ibge,
        periodo=periodo,
        as_of=efetivo_as_of,
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
        raise ausencia_de_entrega(
            session,
            cod_ibge=cod_ibge,
            relatorio="RREO",
            periodo=periodo,
            title="Sem RREO",
            detail=f"Sem RREO vigente para {cod_ibge} em {periodo}.",
        )
    efetivo_as_of = ingestion_repo.effective_as_of(
        session,
        cod_ibge=cod_ibge,
        relatorio="RREO",
        periodo=periodo,
        versao_entrega=versao,
        requested=as_of,
    )
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
        as_of=efetivo_as_of,
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
