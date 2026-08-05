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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError
from app.modules.catalog import repository as catalog_repo
from app.modules.catalog import service as catalog_service
from app.modules.forecast import cenarios, espaco_fiscal, premissas, repository, sanidade
from app.modules.forecast.algorithms import (
    ModeloInsuficiente,
    ResultadoModelo,
    holt_linear,
    projecao_fechamento,
    regressao_exogenas,
)
from app.modules.forecast.periodos import horizonte_periodos
from app.modules.forecast.premissas import anual_para_mensal
from app.modules.forecast.schemas import (
    CenarioSalvo,
    CenarioSimularRequest,
    CenarioSimularResponse,
    ComparacaoModelosResponse,
    CruzamentoLimite,
    EspacoFiscalOut,
    ImpactoContratoDivida,
    LimiteImpacto,
    ModeloComparado,
    PontoHistorico,
    PontoProjecao,
    PremissaObservada,
    PremissasResponse,
    ProjecaoResponse,
    ReconducaoOut,
)
from app.modules.forecast.series import (
    UNIDADE_BRL,
    UNIDADE_PCT_RCL,
    ExogenasAlinhadas,
    SerieHistorica,
    alinhar_exogenas,
    carregar_serie,
    indicador_config,
)
from app.modules.indicators.limites import LimiteLegal, classificar_faixa
from app.modules.indicators.models import FatoRcl
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


def _base_em_reais(
    session: Session, cod_ibge: str, serie: SerieHistorica
) -> tuple[Decimal | None, str | None]:
    """Base de conversão da margem percentual para reais.

    Para indicadores em % da RCL, a base é a **RCL do último período observado** — não a
    projetada. Projetar o denominador junto com o numerador embutiria duas incertezas no
    mesmo número e faria a margem oscilar por razão que o gestor não consegue atribuir. A
    tela diz de qual período a base saiu; quem quiser o efeito de uma RCL diferente usa o
    cenário, onde essa premissa é explícita.

    Devolve ``None`` quando não há RCL — e aí a margem existe só em pontos percentuais, o
    que é honesto. Zero aqui anunciaria margem nenhuma a quem talvez tenha bastante.
    """
    if serie.unidade != UNIDADE_PCT_RCL:
        return None, None
    ultimo = serie.pontos[-1].periodo
    valor = session.scalar(
        select(FatoRcl.rcl_12m)
        .where(FatoRcl.cod_ibge == cod_ibge, FatoRcl.periodo_ref == ultimo)
        .order_by(FatoRcl.versao_entrega.desc())
        .limit(1)
    )
    if valor is not None:
        return Decimal(valor), ultimo
    # Sem o período exato, a RCL mais recente do ente ainda é uma base defensável — e
    # melhor que nenhuma —, desde que a resposta **diga qual período ela é**. Devolver a
    # base sem o período faria uma conversão de 2022 parecer contemporânea.
    linha = session.execute(
        select(FatoRcl.rcl_12m, FatoRcl.periodo_ref)
        .where(FatoRcl.cod_ibge == cod_ibge, FatoRcl.rcl_12m > 0)
        .order_by(FatoRcl.periodo_ref.desc())
        .limit(1)
    ).first()
    return (Decimal(linha[0]), linha[1]) if linha else (None, None)


def _espaco_e_reconducao(
    session: Session,
    cod_ibge: str,
    serie: SerieHistorica,
    esfera: str,
    pontos: list[PontoProjecao],
) -> tuple[EspacoFiscalOut | None, ReconducaoOut | None]:
    """Margem até o limite na ponta do horizonte, e o que a lei exige se ela for negativa.

    A projeção já respondia *quando* o limite seria cruzado. Faltava *quanto ainda cabe* —
    e é essa a metade que vira decisão administrativa, porque empenho se assina em reais.
    """
    limite = _limite_do_indicador(session, serie, esfera)
    if limite is None or not pontos:
        return None, None
    final = pontos[-1]
    base_rs, base_periodo = _base_em_reais(session, cod_ibge, serie)
    espaco = espaco_fiscal.calcular(
        limite,
        final.valor_previsto,
        base_rs=base_rs,
        base_nome="rcl",
        base_periodo=base_periodo,
        periodo_alvo=final.periodo_alvo,
    )
    esforco = espaco_fiscal.esforco_reconducao(espaco)
    return (
        EspacoFiscalOut(**espaco.__dict__),
        ReconducaoOut(**esforco.__dict__),
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
    espaco: EspacoFiscalOut | None = None,
    reconducao: ReconducaoOut | None = None,
    saneamento: dict | None = None,
) -> ProjecaoResponse:
    ultimo = serie.pontos[-1]
    source_ref = SourceRef(**ultimo.source_ref)
    memoria = {
        **resultado.memoria,
        "modelos_disponiveis": _PREFERENCIA,
        "exogenas_fontes": exog.fontes,
        "periodos_historicos": serie.periodos,
        "aviso": "Projeção estatística; não é garantia de execução. IC a 95%.",
        # Nunca omitir que o treino ignorou observação: quem lê a projeção precisa saber
        # que ela ignorou, qual, e por quê.
        "saneamento": saneamento or {"pontos_excluidos": 0},
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
        espaco_fiscal=espaco,
        reconducao=reconducao,
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

    # **A série é saneada antes do treino.** Um ponto impossível — 324,49% da RCL em
    # despesa de pessoal, no acervo, por RCL corrompida na entrega — é consumido pelo
    # modelo sem reclamação e devolvido como projeção plausível. Excluir e dizer é a
    # única saída honesta: recusar tudo por causa de um ponto deixaria o gestor sem
    # resposta onde há onze observações boas.
    saneada = sanidade.sanear(serie.periodos, serie.valores, unidade=serie.unidade)
    periodos_futuro = horizonte_periodos(saneada.periodos[-1], horizonte)
    exog = exog_override or alinhar_exogenas(
        session, cod_ibge, saneada.periodos, periodos_futuro
    )
    resultados = _rodar_modelos(saneada.valores, exog, horizonte)

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
    espaco, reconducao = _espaco_e_reconducao(session, cod_ibge, serie, esfera, pontos)
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
        espaco=espaco,
        reconducao=reconducao,
        saneamento=saneada.memoria(),
    )


_ROTULO_MODELO = {
    "fechamento": "Fechamento (run-rate)",
    "holt_winters": "Holt (nível + tendência)",
    "regressao_exogenas": "Regressão com exógenas (FPM/IPCA/Selic)",
}
_MOTIVO_INDISPONIVEL = {
    "fechamento": "Exige ao menos 2 observações da série.",
    "holt_winters": "Exige ao menos 2 observações da série.",
    "regressao_exogenas": (
        "Exige graus de liberdade suficientes: séries curtas não sustentam a regressão "
        "com exógenas."
    ),
}


def comparar_modelos(
    session: Session,
    cod_ibge: str,
    indicador: str,
    *,
    horizonte: int = 4,
    as_of: datetime | None = None,
) -> ComparacaoModelosResponse:
    """As três camadas lado a lado, para o mesmo ente/indicador/horizonte (Sprint 25E).

    O gestor pergunta "por que esse número e não outro?". Mostrar só o modelo escolhido
    responde pela metade. Aqui aparecem os três — inclusive o **indisponível**, com o
    motivo —, a amplitude do IC de cada um (a medida honesta de incerteza) e o critério
    da escolha. Não há ranking por acurácia: a série é curta demais para um backtest que
    não seja ruído, e fingir um erro fora da amostra seria pior que não medir.
    """
    if indicador_config(indicador) is None:
        raise AppError(
            status=404,
            title="Indicador não projetável",
            detail=f"Indicador '{indicador}' não está no catálogo de previsões.",
        )
    serie = carregar_serie(session, indicador, cod_ibge)
    if len(serie.pontos) < 2:
        raise AppError(
            status=422,
            title="Série insuficiente",
            detail=f"Sem histórico suficiente de '{indicador}' para {cod_ibge} (mínimo 2).",
        )
    esfera = _ente_esfera(session, cod_ibge)
    periodos_futuro = horizonte_periodos(serie.periodos[-1], horizonte)
    exog = alinhar_exogenas(session, cod_ibge, serie.periodos, periodos_futuro)
    resultados = _rodar_modelos(serie.valores, exog, horizonte)
    limite = _limite_do_indicador(session, serie, esfera)
    escolhido = next(m for m in _PREFERENCIA if m in resultados)

    comparados: list[ModeloComparado] = []
    for nome in _PREFERENCIA:
        resultado = resultados.get(nome)
        if resultado is None:
            comparados.append(
                ModeloComparado(
                    modelo=nome,
                    rotulo=_ROTULO_MODELO[nome],
                    disponivel=False,
                    motivo_indisponivel=_MOTIVO_INDISPONIVEL[nome],
                )
            )
            continue
        pontos, cruzamento = _pontos_projecao(resultado, periodos_futuro, limite)
        final = pontos[-1]
        amplitudes = [
            float(p.ic_superior) - float(p.ic_inferior)
            for p in pontos
            if p.ic_superior is not None and p.ic_inferior is not None
        ]
        memoria = dict(resultado.memoria)
        erro = memoria.get("erro_padrao_residuos", memoria.get("erro_padrao_incremento"))
        r2 = memoria.get("r2")
        n_obs = memoria.get("n_obs")
        comparados.append(
            ModeloComparado(
                modelo=nome,
                rotulo=_ROTULO_MODELO[nome],
                disponivel=True,
                escolhido=nome == escolhido,
                valor_final=final.valor_previsto,
                ic_inferior_final=final.ic_inferior,
                ic_superior_final=final.ic_superior,
                amplitude_ic_media=(
                    _dec(sum(amplitudes) / len(amplitudes)) if amplitudes else None
                ),
                erro_padrao=_dec(float(erro)) if isinstance(erro, int | float) else None,
                r2=_dec(float(r2)) if isinstance(r2, int | float) else None,
                n_obs=int(n_obs) if isinstance(n_obs, int) else None,
                cruza_limite=bool(cruzamento.cruza),
                periodo_cruzamento=cruzamento.periodo_cruzamento,
                memoria=memoria,
            )
        )
    ultimo = serie.pontos[-1]
    return ComparacaoModelosResponse(
        cod_ibge=cod_ibge,
        indicador=indicador,
        descricao=serie.descricao,
        unidade=serie.unidade,
        horizonte=horizonte,
        periodos_projetados=periodos_futuro,
        n_periodos_historicos=len(serie.pontos),
        criterio_escolha=(
            "Ordem de preferência: regressão com exógenas → Holt → fechamento; usa-se a "
            "primeira **viável** para a série. Não é escolha por acurácia medida — com "
            f"{len(serie.pontos)} observações, um backtest mediria ruído."
        ),
        modelos=comparados,
        exogenas_fontes=exog.fontes,
        aviso="Projeção estatística; não é garantia de execução. IC a 95%.",
        source_ref=SourceRef(**ultimo.source_ref),
    )


def _combinar_choques(*pcts: float | None) -> float | None:
    """Compõe choques de crescimento independentes por multiplicação, não por soma.

    Dois choques de +5% cada não valem +10%: valem ``1,05 × 1,05 − 1 = 10,25%``. É a mesma
    armadilha da conversão anual→mensal corrigida na Sprint C1 — o erro da soma linear é
    pequeno com valores baixos e cresce a cada passo do horizonte.

    Devolve ``None`` quando nenhum ``pct`` foi informado, para não introduzir um choque de
    "zero" onde a resposta correta é "nenhuma premissa adicional".
    """
    fator = 1.0
    informado = False
    for pct in pcts:
        if pct:
            fator *= 1.0 + pct / 100.0
            informado = True
    return (fator - 1.0) * 100.0 if informado else None


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
def _overrides_exogenas(
    session: Session,
    cod_ibge: str,
    serie: SerieHistorica,
    periodos_futuro: list[str],
    req: CenarioSimularRequest,
) -> ExogenasAlinhadas:
    """Traduz as premissas macro do gestor em valores futuros das exógenas."""
    base = alinhar_exogenas(session, cod_ibge, serie.periodos, periodos_futuro)
    overrides: dict[str, list[float]] = {}
    # **A conversão anual→mensal é composta, não linear.** Dividir por 12 parece inofensivo
    # e não é: 12% ao ano viram 1,0% ao mês em vez de 0,9489% — 5,4% a mais na premissa,
    # composto a cada passo do horizonte. As séries do BCB são variações mensais, então é
    # nesta unidade que o override tem de entrar.
    if req.ipca_aa_pct is not None and "IPCA" in base.matriz.nomes:
        mensal = float(anual_para_mensal(req.ipca_aa_pct))
        overrides["IPCA"] = [mensal] * len(periodos_futuro)
    if req.selic_aa_pct is not None and "SELIC" in base.matriz.nomes:
        mensal = float(anual_para_mensal(req.selic_aa_pct))
        overrides["SELIC"] = [mensal] * len(periodos_futuro)
    if req.fpm_variacao_pct is not None and "FPM" in base.matriz.nomes:
        media = base.media_por_nome["FPM"]
        overrides["FPM"] = [media * (1 + req.fpm_variacao_pct / 100.0)] * len(periodos_futuro)
    if not overrides:
        return base
    return alinhar_exogenas(
        session, cod_ibge, serie.periodos, periodos_futuro, overrides_futuro=overrides
    )


def premissas_observadas(session: Session, cod_ibge: str) -> PremissasResponse:
    """As premissas de cenário como o mundo as reporta, para a tela abrir ancorada.

    A tela abria com IPCA 4,5% e Selic 10,5% escritos no código do frontend — dois números
    que alguém digitou uma vez e que apareciam com a mesma aparência de qualquer valor
    informado. O acervo tem as séries reais, e a Selic observada é **14,28%**: quem
    aceitasse o padrão simulava com 3,8 pontos percentuais de erro sem saber que havia um
    padrão.
    """
    itens = [
        PremissaObservada(**p.__dict__) for p in premissas.observadas(session, cod_ibge)
    ]
    return PremissasResponse(
        cod_ibge=cod_ibge,
        premissas=itens,
        nota=(
            "Valores observados, não projeções de mercado. São o ponto de partida do "
            "cenário; altere-os para simular. Onde a série não sustenta o cálculo, a "
            "premissa vem vazia com o motivo — nenhum valor de fábrica ocupa o lugar."
        ),
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


def _impacto_contrato_divida(
    session: Session,
    cod_ibge: str,
    serie: SerieHistorica,
    cenario: ProjecaoResponse,
    esfera: str,
    req: CenarioSimularRequest,
) -> ImpactoContratoDivida | None:
    """Quanto um novo contrato hipotético consome do teto de 120%/200% da RCL (DCL).

    Escopo mínimo (ficha Sprint G1): calcula o impacto no teto sem persistir o contrato — a
    operação de crédito em si nasce no SADIPEM, não num cenário "e se?". O principal soma à
    DCL projetada **de uma vez**, porque a LRF conta a dívida a partir da contratação, não
    da amortização: o impacto aparece mesmo com a operação em carência.
    """
    contrato = req.novo_contrato_divida
    if contrato is None or serie.indicador != "divida":
        return None
    limite = _limite_do_indicador(session, serie, esfera)
    if limite is None:
        return None
    base_rs, base_periodo = _base_em_reais(session, cod_ibge, serie)
    pct_atual = cenario.projecao[-1].valor_previsto
    principal = _dec(contrato.principal_rs)
    fundamento = (
        "LRF art. 30, §7º c/c Resolução do Senado 43/2001 — a operação conta na Dívida "
        "Consolidada Líquida a partir da contratação, independentemente de carência."
    )
    if not base_rs:
        # Sem RCL para converter, zero anunciaria "sem impacto" onde a resposta honesta é
        # "não dá para converter" — a mesma regra de `EspacoFiscalOut.margem_rs`.
        return ImpactoContratoDivida(
            principal_rs=principal,
            prazo_meses=contrato.prazo_meses,
            carencia_meses=contrato.carencia_meses,
            taxa_aa_pct=_dec(contrato.taxa_aa_pct),
            pct_rcl_adicional=None,
            pct_rcl_resultante=None,
            teto_pct=limite.teto_pct,
            faixa=None,
            cruza=False,
            base_rs=None,
            base_periodo=None,
            fundamento=f"{fundamento} Sem RCL no acervo para converter o principal em % da RCL.",
        )
    pct_adicional = (principal / base_rs) * Decimal(100)
    pct_resultante = pct_atual + pct_adicional
    faixa = classificar_faixa(pct_resultante, limite)
    cruza = (
        pct_resultante >= limite.teto_pct
        if limite.sentido == "teto"
        else pct_resultante < limite.teto_pct
    )
    return ImpactoContratoDivida(
        principal_rs=principal,
        prazo_meses=contrato.prazo_meses,
        carencia_meses=contrato.carencia_meses,
        taxa_aa_pct=_dec(contrato.taxa_aa_pct),
        pct_rcl_adicional=pct_adicional,
        pct_rcl_resultante=pct_resultante,
        teto_pct=limite.teto_pct,
        faixa=faixa,
        cruza=cruza,
        base_rs=base_rs,
        base_periodo=base_periodo,
        fundamento=fundamento,
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

    # Robustez (Sprint G1): FUNDEB (receita) e reajuste de folha (pessoal) têm significado
    # fiscal próprio — cada um só se aplica ao indicador a que pertence, e compõe com o
    # choque genérico por multiplicação (ver `_combinar_choques`), nunca por soma.
    choque_extra: float | None = None
    if indicador == "pessoal":
        choque_extra = req.reajuste_folha_pct
    elif serie.unidade == UNIDADE_BRL:
        choque_extra = req.fundeb_variacao_pct
    choque_efetivo = _combinar_choques(req.crescimento_indicador_pct, choque_extra)

    cenario = build_projecao(
        session,
        cod_ibge,
        indicador,
        horizonte=req.horizonte,
        modelo=base.modelo,
        persistir=False,
        exog_override=exog,
        choque_pct=choque_efetivo,
    )

    esfera = base.esfera or _ente_esfera(session, cod_ibge)
    tetos, pisos = _impacto_cenario(session, serie, cenario, esfera, req)
    contrato_impacto = _impacto_contrato_divida(session, cod_ibge, serie, cenario, esfera, req)

    # Uma premissa informada que não se aplica ao indicador corrente não pode desaparecer
    # em silêncio — foi exatamente esse silêncio que manteve `crescimento_rcl_pct` inerte
    # no ramo PCT_RCL por uma sprint inteira (A20). Aqui a lista viaja sempre, vazia quando
    # não há nada a avisar.
    avisos_premissas: list[str] = []
    if req.fundeb_variacao_pct and serie.unidade != UNIDADE_BRL:
        avisos_premissas.append(
            "fundeb_variacao_pct informado mas não se aplica a indicadores em % RCL "
            "(pessoal/dívida) — o efeito de FUNDEB sobre esses indicadores passa pela RCL; "
            "use crescimento_rcl_pct."
        )
    if req.reajuste_folha_pct and indicador != "pessoal":
        avisos_premissas.append(
            "reajuste_folha_pct informado mas só se aplica ao indicador 'pessoal'."
        )
    if req.novo_contrato_divida and indicador != "divida":
        avisos_premissas.append(
            "novo_contrato_divida informado mas só se aplica ao indicador 'divida'."
        )

    memoria: dict[str, Any] = {
        "premissas": req.model_dump(exclude_none=True),
        "modelo_base": base.modelo,
        "conversao_macro": (
            "IPCA/Selic de taxa anual para mensal por capitalização composta "
            "((1+a)^(1/12)−1, não a÷12); FPM como variação sobre a média histórica"
        ),
        "choque_efetivo_pct": choque_efetivo,
        "choque_efetivo_composicao": (
            "crescimento_indicador_pct × "
            f"{'reajuste_folha_pct' if indicador == 'pessoal' else 'fundeb_variacao_pct'} "
            "— composição multiplicativa, não soma"
            if choque_extra
            else None
        ),
        "avisos_premissas": avisos_premissas,
        "observacao_minimos": (
            "Mínimos (saúde/educação) usam base impostos+transf.; aqui a RCL projetada "
            "é proxy explícita do teto/piso em R$ — não substitui a apuração oficial."
        ),
        "nao_persistente": not req.salvar,
    }

    persistido = False
    cenario_id: str | None = None
    versao_salva: int | None = None
    if req.salvar:
        # A gravação vive em `cenarios.salvar` porque é lá que a **procedência** é
        # capturada. Gravar por outro caminho produziria versão sem saber sobre qual
        # entrega o cálculo rodou — e uma versão assim não reproduz nada.
        alvo = uuid.UUID(req.cenario_id) if req.cenario_id else None
        salvo, versao = cenarios.salvar(
            session,
            principal,
            cod_ibge=cod_ibge,
            indicador=indicador,
            req=req,
            resultado={
                "projecao": [p.model_dump(mode="json") for p in cenario.projecao],
                "cruzamento": cenario.cruzamento.model_dump(mode="json"),
                "espaco_fiscal": (
                    cenario.espaco_fiscal.model_dump(mode="json")
                    if cenario.espaco_fiscal
                    else None
                ),
            },
            modelo=base.modelo,
            cenario_id=alvo,
        )
        persistido = True
        cenario_id = str(salvo.id)
        versao_salva = versao.versao

    return CenarioSimularResponse(
        persistido=persistido,
        cenario_id=cenario_id,
        versao=versao_salva,
        cod_ibge=cod_ibge,
        indicador=indicador,
        horizonte=req.horizonte,
        base=base,
        cenario=cenario,
        impacto_limites=tetos,
        impacto_minimos=pisos,
        impacto_contrato_divida=contrato_impacto,
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
        # A20: `crescimento_rcl_pct` era aplicado só no ramo BRL — Pessoal e Dívida (os
        # dois indicadores com teto mais severo) ignoravam o slider por inteiro. O que a
        # série carrega aqui já É a razão numerador/RCL; um crescimento assumido da RCL sem
        # crescimento correspondente do numerador **dilui** o indicador — dividir pelo
        # fator de crescimento reproduz esse efeito sem precisar reprojetar o numerador em
        # R$ separadamente.
        pct_final = final
        if req.crescimento_rcl_pct:
            fator = Decimal(1) + _dec(req.crescimento_rcl_pct) / Decimal(100)
            if fator > 0:
                pct_final = final / fator
        return [_impacto_pct(limite, pct_final)], []
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
