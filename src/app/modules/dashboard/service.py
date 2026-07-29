"""Regras do Dashboard Executivo (Módulo 1): semáforo, KPIs, conformidade e destaques."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.dashboard.schemas import (
    DashboardResponse,
    DestaqueItem,
    KpiItem,
    SemaforoItem,
)
from app.modules.indicators import repository as indicators_repo
from app.modules.indicators import rotulos
from app.modules.indicators.models import MartIndicador
from app.modules.limits import repository as limits_repo
from app.modules.limits.service import SEMAFORO_INDICADORES, _esfera_do_ente, _limite_dim
from app.shared.source_ref import SourceRef

COR_POR_FAIXA: dict[str | None, str] = {
    "normal": "verde",
    "adequado": "verde",
    "alerta": "amarelo",
    "prudencial": "laranja",
    "excedido": "vermelho",
    "insuficiente": "vermelho",
    None: "cinza",
}

# Severidade crescente (para conformidade global e ordenação de destaques).
_SEVERIDADE = {"verde": 0, "cinza": 0, "amarelo": 1, "laranja": 2, "vermelho": 3}
_CONFORMIDADE_POR_SEVERIDADE = {0: "conforme", 1: "alerta", 2: "prudencial", 3: "critico"}
_ROTULO_INDICADOR = {
    "pessoal_executivo": "Pessoal (Executivo)",
    "divida_consolidada_liquida": "Dívida Consolidada Líquida",
    "saude_minimo": "Saúde (mínimo)",
    "educacao_mde": "Educação (MDE)",
}
# O que é 100% em cada indicador — mínimos constitucionais não se medem sobre a RCL.
_ROTULO_BASE = {
    "rcl": "da RCL",
    # Pessoal e dívida têm limite sobre a RCL **ajustada** (o próprio demonstrativo a
    # publica). Chamar as duas de "da RCL" apagaria a diferença que faz o percentual.
    "rcl_ajustada": "da RCL ajustada",
    "impostos_transferencias": "dos impostos e transferências",
    "fundeb": "das receitas do FUNDEB",
}


def _cor(faixa: str | None) -> str:
    return COR_POR_FAIXA.get(faixa, "cinza")


def build_dashboard(session: Session, cod_ibge: str, periodo: str) -> DashboardResponse:
    esfera = _esfera_do_ente(session, cod_ibge)
    versao = indicators_repo.resolve_versao_rreo(session, cod_ibge=cod_ibge, periodo=periodo)

    mart_por_indicador: dict[str, MartIndicador] = {}
    if versao is not None:
        for row in limits_repo.list_mart_by_periodo(
            session, cod_ibge=cod_ibge, periodo=periodo, versao_entrega=versao
        ):
            mart_por_indicador[row.indicador] = row

    semaforo: list[SemaforoItem] = []
    destaques: list[DestaqueItem] = []
    for indicador in SEMAFORO_INDICADORES:
        mart = mart_por_indicador.get(indicador)
        limite = _limite_dim(session, indicador, esfera)
        sentido = limite.sentido if limite else "teto"
        faixa = mart.faixa if mart else None
        cor = _cor(faixa)
        semaforo.append(
            SemaforoItem(
                indicador=indicador, faixa=faixa, cor=cor,
                valor_pct_rcl=mart.valor_pct_rcl if mart else None,
                teto_pct=mart.teto_pct if mart else (limite.teto_pct if limite else None),
                sentido=sentido,
                denominador=mart.denominador if mart else "rcl",
            )
        )
        if faixa is not None and cor != "verde":
            pct = f"{mart.valor_pct_rcl:.1f}%" if mart and mart.valor_pct_rcl is not None else "—"
            rotulo = _ROTULO_INDICADOR.get(indicador) or rotulos.rotulo(indicador)
            base = _ROTULO_BASE.get(mart.denominador if mart else "rcl", "base declarada")
            destaques.append(
                DestaqueItem(
                    indicador=indicador, faixa=faixa, cor=cor,
                    mensagem=f"{rotulo} em faixa '{faixa}' ({pct} {base}).",
                )
            )

    destaques.sort(key=lambda d: _SEVERIDADE.get(d.cor, 0), reverse=True)

    severidades = [_SEVERIDADE.get(s.cor, 0) for s in semaforo if s.faixa is not None]
    conformidade = (
        _CONFORMIDADE_POR_SEVERIDADE[max(severidades)] if severidades else "sem_dados"
    )

    fato = (
        indicators_repo.get_fato_rcl(
            session, cod_ibge=cod_ibge, periodo_ref=periodo, versao_entrega=versao
        )
        if versao is not None
        else None
    )
    kpis = [
        KpiItem(chave="rcl_12m", rotulo="RCL (12 meses)", unidade="R$",
                valor=fato.rcl_12m if fato else None, disponivel=fato is not None),
        KpiItem(chave="receita_corrente", rotulo="Receita corrente", unidade="R$",
                valor=fato.receita_corrente if fato else None, disponivel=fato is not None),
        KpiItem(chave="deducoes", rotulo="Deduções da RCL", unidade="R$",
                valor=fato.deducoes if fato else None, disponivel=fato is not None),
        # Despesa total e resultado primário chegam nas Sprints 5/8 (ainda não calculados).
        KpiItem(chave="despesa_total", rotulo="Despesa total", unidade="R$",
                valor=None, disponivel=False),
        KpiItem(chave="resultado_primario", rotulo="Resultado primário", unidade="R$",
                valor=None, disponivel=False),
    ]

    return DashboardResponse(
        cod_ibge=cod_ibge,
        periodo=periodo,
        versao_entrega=versao or "",
        conformidade=conformidade,
        semaforo=semaforo,
        kpis=kpis,
        destaques=destaques,
        source_ref=SourceRef(relatorio="RREO", periodo=periodo, versao_entrega=versao),
    )
