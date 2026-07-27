"""Regras da Sprint 14: treino das camadas de projeção, cruzamento de limite e cenário.

Fluxo: lê a série gold (``series``), roda as três camadas de modelo (``algorithms``),
marca o **cruzamento do limite legal** e materializa em ``gold.fato_projecao`` — sempre
com IC. O cenário (POST) recalcula sob premissas de gestor e **não persiste** salvo se
``salvar=True``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError
from app.modules.catalog import repository as catalog_repo
from app.modules.catalog import service as catalog_service
from app.modules.forecast import repository
from app.modules.forecast.algorithms import (
    ModeloInsuficiente,
    ResultadoModelo,
    holt_linear,
    projecao_fechamento,
    regressao_exogenas,
)
from app.modules.forecast.periodos import horizonte_periodos
from app.modules.forecast.schemas import (
    CenarioSalvo,
    CenarioSimularRequest,
    CenarioSimularResponse,
    CruzamentoLimite,
    LimiteImpacto,
    PontoHistorico,
    PontoProjecao,
    ProjecaoResponse,
)
from app.modules.forecast.series import (
    UNIDADE_PCT_RCL,
    ExogenasAlinhadas,
    SerieHistorica,
    alinhar_exogenas,
    carregar_serie,
    indicador_config,
)
from app.modules.indicators.limites import LimiteLegal, classificar_faixa
from app.shared.source_ref import SourceRef

# Preferência quando o modelo não é forçado (do mais informativo ao mais simples).
_PREFERENCIA = ("regressao_exogenas", "holt_winters", "fechamento")
_NIVEL_CONFIANCA = Decimal(95)


def _dec(valor: float | Decimal) -> Decimal:
    return Decimal(str(valor))


def _ente_esfera(session: Session, cod_ibge: str) -> str:
    ente = catalog_service.refresh_dim_ente(session, cod_ibge)
    if ente is None or ente.esfera is None:
        raise AppError(
            status=422,
            title="Esfera desconhecida",
            detail=f"dim_ente sem esfera para {cod_ibge} (ingerir siconfi_entes).",
        )
    return ente.esfera


def _limite_do_indicador(
    session: Session, serie: SerieHistorica, esfera: str
) -> LimiteLegal | None:
    """Limite legal comparável (só para indicadores expressos em % RCL)."""
    ref = serie.config.limite
    if ref is None or serie.unidade != UNIDADE_PCT_RCL:
        return None
    row = catalog_repo.get_limite(
        session, indicador=ref.indicador_limite, esfera=esfera, poder=ref.poder
    )
    if row is None:
        return None
    return LimiteLegal(
        indicador=row.indicador,
        esfera=row.esfera,
        poder=row.poder,
        sentido=row.sentido,
        teto_pct=row.teto_pct,
        alerta_pct=row.alerta_pct,
        prudencial_pct=row.prudencial_pct,
    )


def _rodar_modelos(
    valores: list[float], exog: ExogenasAlinhadas, horizonte: int
) -> dict[str, ResultadoModelo]:
    """Roda as três camadas; ignora as inviáveis para a série (sem inventar dado)."""
    resultados: dict[str, ResultadoModelo] = {}
    for nome, fn in (
        ("fechamento", lambda: projecao_fechamento(valores, horizonte)),
        ("holt_winters", lambda: holt_linear(valores, horizonte)),
        ("regressao_exogenas", lambda: regressao_exogenas(valores, exog.matriz, horizonte)),
    ):
        try:
            resultados[nome] = fn()
        except ModeloInsuficiente:
            continue
    if not resultados:
        raise AppError(
            status=422,
            title="Série insuficiente",
            detail="Histórico curto demais para qualquer modelo (mínimo 2 observações).",
        )
    return resultados


def _pontos_projecao(
    resultado: ResultadoModelo,
    periodos_futuro: list[str],
    limite: LimiteLegal | None,
) -> tuple[list[PontoProjecao], CruzamentoLimite]:
    pontos: list[PontoProjecao] = []
    cruzamento = CruzamentoLimite(aplicavel=limite is not None)
    for i, ponto in enumerate(resultado.pontos):
        periodo_alvo = periodos_futuro[i]
        valor = _dec(ponto.valor_previsto)
        teto = limite.teto_pct if limite is not None else None
        faixa: str | None = None
        cruza = False
        if limite is not None:
            faixa = classificar_faixa(valor, limite)
            cruza = (
                valor >= limite.teto_pct
                if limite.sentido == "teto"
                else valor < limite.teto_pct
            )
            if cruza and not cruzamento.cruza:
                cruzamento = CruzamentoLimite(
                    aplicavel=True,
                    cruza=True,
                    periodo_cruzamento=periodo_alvo,
                    passo_cruzamento=ponto.passo,
                    valor_no_cruzamento=valor,
                    teto_pct=limite.teto_pct,
                    indicador_limite=limite.indicador,
                    esfera=limite.esfera,
                )
        pontos.append(
            PontoProjecao(
                periodo_alvo=periodo_alvo,
                passo=ponto.passo,
                valor_previsto=valor,
                ic_inferior=_dec(ponto.ic_inferior),
                ic_superior=_dec(ponto.ic_superior),
                teto_pct=teto,
                faixa=faixa,
                cruza_limite=cruza,
            )
        )
    if limite is not None and not cruzamento.cruza:
        cruzamento = CruzamentoLimite(
            aplicavel=True,
            cruza=False,
            teto_pct=limite.teto_pct,
            indicador_limite=limite.indicador,
            esfera=limite.esfera,
        )
    return pontos, cruzamento


def _historico(serie: SerieHistorica) -> list[PontoHistorico]:
    return [
        PontoHistorico(
            periodo=p.periodo,
            valor=_dec(p.valor),
            versao_entrega=p.versao_entrega,
            as_of=p.as_of,
            source_ref=SourceRef(**p.source_ref),
        )
        for p in serie.pontos
    ]


def _persistir(
    session: Session,
    *,
    cod_ibge: str,
    indicador: str,
    unidade: str,
    horizonte: int,
    gerado_em: datetime,
    resultado: ResultadoModelo,
    pontos: list[PontoProjecao],
    source_ref: SourceRef,
) -> None:
    exog_fonte = resultado.memoria.get("exogenas_usadas")
    for ponto in pontos:
        repository.upsert_projecao(
            session,
            {
                "cod_ibge": cod_ibge,
                "indicador": indicador,
                "periodo_alvo": ponto.periodo_alvo,
                "horizonte": horizonte,
                "valor_previsto": ponto.valor_previsto,
                "ic_inferior": ponto.ic_inferior,
                "ic_superior": ponto.ic_superior,
                "nivel_confianca": _NIVEL_CONFIANCA,
                "unidade": unidade,
                "modelo": resultado.modelo,
                "teto_pct": ponto.teto_pct,
                "faixa": ponto.faixa,
                "cruza_limite": ponto.cruza_limite,
                "gerado_em": gerado_em,
                "source_ref": {**source_ref.model_dump(mode="json"), "exogenas": exog_fonte},
                "memoria": resultado.memoria,
            },
        )


def _montar_resposta(
    *,
    cod_ibge: str,
    serie: SerieHistorica,
    esfera: str,
    resultado: ResultadoModelo,
    pontos: list[PontoProjecao],
    cruzamento: CruzamentoLimite,
    horizonte: int,
    as_of: datetime | None,
    gerado_em: datetime | None,
    exog: ExogenasAlinhadas,
) -> ProjecaoResponse:
    ultimo = serie.pontos[-1]
    source_ref = SourceRef(**ultimo.source_ref)
    memoria = {
        **resultado.memoria,
        "modelos_disponiveis": _PREFERENCIA,
        "exogenas_fontes": exog.fontes,
        "periodos_historicos": serie.periodos,
        "aviso": "Projeção estatística; não é garantia de execução. IC a 95%.",
    }
    return ProjecaoResponse(
        cod_ibge=cod_ibge,
        indicador=serie.indicador,
        descricao=serie.descricao,
        unidade=serie.unidade,
        modelo=resultado.modelo,
        esfera=esfera,
        nivel_confianca=_NIVEL_CONFIANCA,
        horizonte=horizonte,
        as_of=as_of or ultimo.as_of,
        gerado_em=gerado_em,
        historico=_historico(serie),
        projecao=pontos,
        cruzamento=cruzamento,
        memoria=memoria,
        source_ref=source_ref,
    )


def build_projecao(
    session: Session,
    cod_ibge: str,
    indicador: str,
    *,
    horizonte: int = 4,
    modelo: str | None = None,
    as_of: datetime | None = None,
    persistir: bool = True,
    exog_override: ExogenasAlinhadas | None = None,
    choque_pct: float | None = None,
) -> ProjecaoResponse:
    """Projeta ``indicador`` do ente: histórico + projeção + IC + cruzamento de limite."""
    if indicador_config(indicador) is None:
        raise AppError(
            status=404,
            title="Indicador não projetável",
            detail=f"Indicador '{indicador}' não está no catálogo de previsões.",
        )
    esfera = _ente_esfera(session, cod_ibge)
    serie = carregar_serie(session, indicador, cod_ibge)
    if len(serie.pontos) < 2:
        raise AppError(
            status=422,
            title="Série insuficiente",
            detail=f"Sem histórico suficiente de '{indicador}' para {cod_ibge} (mínimo 2).",
        )

    periodos_futuro = horizonte_periodos(serie.periodos[-1], horizonte)
    exog = exog_override or alinhar_exogenas(
        session, cod_ibge, serie.periodos, periodos_futuro
    )
    resultados = _rodar_modelos(serie.valores, exog, horizonte)

    if modelo is not None and modelo not in resultados:
        raise AppError(
            status=422,
            title="Modelo indisponível",
            detail=f"Modelo '{modelo}' não é viável para esta série.",
        )
    escolhido = modelo or next(m for m in _PREFERENCIA if m in resultados)

    if choque_pct:
        resultados = {m: _aplicar_choque(r, choque_pct) for m, r in resultados.items()}

    limite = _limite_do_indicador(session, serie, esfera)
    gerado_em = datetime.now(UTC)

    pontos_por_modelo: dict[str, tuple[list[PontoProjecao], CruzamentoLimite]] = {}
    for nome, resultado in resultados.items():
        pontos_por_modelo[nome] = _pontos_projecao(resultado, periodos_futuro, limite)

    if persistir:
        ultimo = serie.pontos[-1]
        source_ref = SourceRef(**ultimo.source_ref)
        for nome, resultado in resultados.items():
            pontos, _ = pontos_por_modelo[nome]
            _persistir(
                session,
                cod_ibge=cod_ibge,
                indicador=indicador,
                unidade=serie.unidade,
                horizonte=horizonte,
                gerado_em=gerado_em,
                resultado=resultado,
                pontos=pontos,
                source_ref=source_ref,
            )

    pontos, cruzamento = pontos_por_modelo[escolhido]
    return _montar_resposta(
        cod_ibge=cod_ibge,
        serie=serie,
        esfera=esfera,
        resultado=resultados[escolhido],
        pontos=pontos,
        cruzamento=cruzamento,
        horizonte=horizonte,
        as_of=as_of,
        gerado_em=gerado_em if persistir else None,
        exog=exog,
    )


def _aplicar_choque(resultado: ResultadoModelo, choque_pct: float) -> ResultadoModelo:
    """Aplica um choque de crescimento composto por período sobre a projeção (cenário)."""
    fator = 1.0 + choque_pct / 100.0
    novos = []
    from app.modules.forecast.algorithms import PontoPrevisto  # local: evita ciclo aparente

    for ponto in resultado.pontos:
        mult = fator**ponto.passo
        novos.append(
            PontoPrevisto(
                passo=ponto.passo,
                valor_previsto=ponto.valor_previsto * mult,
                ic_inferior=ponto.ic_inferior * mult,
                ic_superior=ponto.ic_superior * mult,
            )
        )
    return ResultadoModelo(
        modelo=resultado.modelo,
        pontos=novos,
        memoria={**resultado.memoria, "choque_crescimento_pct": choque_pct},
    )


# --------------------------------------------------------------------------- #
# Cenário (POST /cenario/simular) — não persiste salvo se salvar=True
# --------------------------------------------------------------------------- #
def _periodos_por_ano(codigo: str) -> int:
    from app.modules.forecast.periodos import parse_periodo

    return {"B": 6, "Q": 3, "M": 12, "A": 1}[parse_periodo(codigo).cadencia]


def _overrides_exogenas(
    session: Session,
    cod_ibge: str,
    serie: SerieHistorica,
    periodos_futuro: list[str],
    req: CenarioSimularRequest,
) -> ExogenasAlinhadas:
    """Traduz as premissas macro do gestor em valores futuros das exógenas."""
    base = alinhar_exogenas(session, cod_ibge, serie.periodos, periodos_futuro)
    por_ano = _periodos_por_ano(serie.periodos[-1])
    overrides: dict[str, list[float]] = {}
    if req.ipca_aa_pct is not None and "IPCA" in base.matriz.nomes:
        overrides["IPCA"] = [req.ipca_aa_pct / 12.0] * len(periodos_futuro)
    if req.selic_aa_pct is not None and "SELIC" in base.matriz.nomes:
        overrides["SELIC"] = [req.selic_aa_pct / 12.0] * len(periodos_futuro)
    if req.fpm_variacao_pct is not None and "FPM" in base.matriz.nomes:
        media = base.media_por_nome["FPM"]
        overrides["FPM"] = [media * (1 + req.fpm_variacao_pct / 100.0)] * len(periodos_futuro)
    _ = por_ano  # documentado: conversão anual→período (mensal) já embutida acima
    if not overrides:
        return base
    return alinhar_exogenas(
        session, cod_ibge, serie.periodos, periodos_futuro, overrides_futuro=overrides
    )


def _impacto_limites(
    session: Session, esfera: str, base_rs: Decimal
) -> tuple[list[LimiteImpacto], list[LimiteImpacto]]:
    """Traduz uma projeção em R$ (RCL/receita) para tetos/pisos legais em R$."""
    tetos: list[LimiteImpacto] = []
    pisos: list[LimiteImpacto] = []
    for row in catalog_repo.list_limites(session):
        if row.esfera != esfera:
            continue
        valor_rs = (row.teto_pct / Decimal(100)) * base_rs
        impacto = LimiteImpacto(
            indicador=row.indicador,
            descricao=None,
            sentido=row.sentido,
            limite_pct=row.teto_pct,
            valor_limite_rs=valor_rs,
        )
        (tetos if row.sentido == "teto" else pisos).append(impacto)
    return tetos, pisos


def _impacto_pct(limite: LimiteLegal, pct: Decimal) -> LimiteImpacto:
    faixa = classificar_faixa(pct, limite)
    cruza = pct >= limite.teto_pct if limite.sentido == "teto" else pct < limite.teto_pct
    return LimiteImpacto(
        indicador=limite.indicador,
        sentido=limite.sentido,
        limite_pct=limite.teto_pct,
        pct_projetado=pct,
        faixa=faixa,
        cruza=cruza,
    )


def simular_cenario(
    session: Session,
    principal: Principal,
    cod_ibge: str,
    indicador: str,
    req: CenarioSimularRequest,
) -> CenarioSimularResponse:
    """Recalcula a projeção sob premissas do gestor; só grava se ``req.salvar``."""
    if indicador_config(indicador) is None:
        raise AppError(
            status=404,
            title="Indicador não projetável",
            detail=f"Indicador '{indicador}' não está no catálogo de previsões.",
        )

    base = build_projecao(
        session, cod_ibge, indicador, horizonte=req.horizonte, modelo=req.modelo, persistir=False
    )
    serie = carregar_serie(session, indicador, cod_ibge)
    periodos_futuro = horizonte_periodos(serie.periodos[-1], req.horizonte)
    exog = _overrides_exogenas(session, cod_ibge, serie, periodos_futuro, req)

    cenario = build_projecao(
        session,
        cod_ibge,
        indicador,
        horizonte=req.horizonte,
        modelo=base.modelo,
        persistir=False,
        exog_override=exog,
        choque_pct=req.crescimento_indicador_pct,
    )

    esfera = base.esfera or _ente_esfera(session, cod_ibge)
    tetos, pisos = _impacto_cenario(session, serie, cenario, esfera, req)

    memoria: dict[str, Any] = {
        "premissas": req.model_dump(exclude_none=True),
        "modelo_base": base.modelo,
        "conversao_macro": "IPCA/Selic anual → contribuição mensal (÷12); FPM vs média histórica",
        "observacao_minimos": (
            "Mínimos (saúde/educação) usam base impostos+transf.; aqui a RCL projetada "
            "é proxy explícita do teto/piso em R$ — não substitui a apuração oficial."
        ),
        "nao_persistente": not req.salvar,
    }

    persistido = False
    cenario_id: str | None = None
    if req.salvar:
        if principal.org_id is None:
            raise AppError(
                status=403,
                title="Sem organização",
                detail="Salvar cenário exige organização ativa.",
            )
        salvo = repository.criar_cenario(
            session,
            {
                "org_id": principal.org_id,
                "ente": cod_ibge,
                "indicador": indicador,
                "nome": req.nome,
                "parametros": req.model_dump(exclude_none=True),
                "resultado": {
                    "projecao": [p.model_dump(mode="json") for p in cenario.projecao],
                    "cruzamento": cenario.cruzamento.model_dump(mode="json"),
                },
                "criado_por": principal.usuario_id,
            },
        )
        persistido = True
        cenario_id = str(salvo.id)

    return CenarioSimularResponse(
        persistido=persistido,
        cenario_id=cenario_id,
        cod_ibge=cod_ibge,
        indicador=indicador,
        horizonte=req.horizonte,
        base=base,
        cenario=cenario,
        impacto_limites=tetos,
        impacto_minimos=pisos,
        memoria=memoria,
        source_refs=[base.source_ref],
    )


def _impacto_cenario(
    session: Session,
    serie: SerieHistorica,
    cenario: ProjecaoResponse,
    esfera: str,
    req: CenarioSimularRequest,
) -> tuple[list[LimiteImpacto], list[LimiteImpacto]]:
    final = cenario.projecao[-1].valor_previsto
    if serie.unidade == UNIDADE_PCT_RCL:
        limite = _limite_do_indicador(session, serie, esfera)
        if limite is None:
            return [], []
        return [_impacto_pct(limite, final)], []
    # BRL (RCL/receita): aplica crescimento de RCL do cenário à base e projeta tetos/pisos.
    base_rs = final
    if req.crescimento_rcl_pct:
        base_rs = final * (Decimal(1) + _dec(req.crescimento_rcl_pct) / Decimal(100))
    return _impacto_limites(session, esfera, base_rs)


# --------------------------------------------------------------------------- #
# Cenários salvos
# --------------------------------------------------------------------------- #
def listar_cenarios(
    session: Session, principal: Principal, cod_ibge: str | None = None
) -> list[CenarioSalvo]:
    if principal.org_id is None:
        return []
    return [
        CenarioSalvo(
            id=str(c.id),
            ente=c.ente,
            indicador=c.indicador,
            nome=c.nome,
            parametros=c.parametros,
            criado_em=c.criado_em,
        )
        for c in repository.list_cenarios(session, org_id=principal.org_id, ente=cod_ibge)
    ]


def obter_cenario(
    session: Session, principal: Principal, cenario_id: uuid.UUID
) -> CenarioSalvo:
    if principal.org_id is None:
        raise AppError(status=403, title="Sem organização", detail="Requer organização ativa.")
    c = repository.get_cenario(session, org_id=principal.org_id, cenario_id=cenario_id)
    if c is None:
        raise AppError(status=404, title="Cenário não encontrado", detail=str(cenario_id))
    return CenarioSalvo(
        id=str(c.id),
        ente=c.ente,
        indicador=c.indicador,
        nome=c.nome,
        parametros=c.parametros,
        criado_em=c.criado_em,
    )
