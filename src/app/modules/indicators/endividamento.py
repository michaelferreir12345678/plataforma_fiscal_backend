"""Garantias e operações de crédito — os dois limites da LRF que nunca eram apurados.

O ``dim_limite_legal`` declara os quatro tetos de endividamento desde sempre: dívida
consolidada líquida (120%/200%), **garantias (22%)**, **operações de crédito (16%)** e ARO
(7%). Só o primeiro era calculado. Os outros dois existiam como regra e não como número —
a plataforma sabia o limite e nunca dizia se o ente estava dentro dele.

## O denominador é a RCL **Ajustada**, não a RCL

É o erro mais fácil de cometer aqui, e o mais silencioso: a Resolução 43/2001 do Senado
manda apurar estes limites sobre a *Receita Corrente Líquida Ajustada*, que deduz da RCL as
transferências obrigatórias da União relativas às emendas individuais (CF, art. 166-A, §1º).
Usar a RCL cheia infla o denominador e **subestima** o percentual — o ente aparece com mais
folga do que tem.

O RGF publica a RCL Ajustada explicitamente (``ReceitaCorrenteLiquidaAjustada...``), então
não há por que recalculá-la: o número do ente é a fonte.

## A contraprova vem de graça

O Anexo 03 publica ``PercentualDoTotalDasGarantiasSobreARCL`` e o Anexo 04 publica o limite
em valor e em percentual. São o mesmo cálculo feito pelo ente — comparar o nosso com o dele
transforma cada apuração numa reconciliação, sem custo adicional.

## Garantia ausente é zero, e aqui isso é honesto

Só 8 dos 179 entes têm linha de garantia: conceder garantia é raro para município. Mas o
Anexo 03 **é** o demonstrativo das garantias, e 179 entes o entregam. Entregar o anexo sem
linha de garantia é o ente declarando que não concedeu nenhuma — diferente de não entregar
o anexo, que é ausência de informação. A distinção está codificada em
:func:`_total_garantias`: sem o anexo, ``None``; com o anexo e sem a linha, ``Decimal(0)``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.ingestion import repository as ingestion_repo
from app.modules.ingestion.models import SilverRgf

#: Conta do total de garantias concedidas (RGF Anexo 03).
CONTA_GARANTIAS = "TotalGarantiasConcedidas"
#: Percentual que o **próprio ente** publica — serve de contraprova do nosso cálculo.
CONTA_GARANTIAS_PCT = "PercentualDoTotalDasGarantiasSobreARCL"
#: Total das operações de crédito realizadas no exercício (RGF Anexo 04).
CONTA_OPERACOES = "RGF4OperacoesDeCreditoTotal"
#: Denominador legal dos limites de endividamento (Res. 43/2001 do Senado).
CONTA_RCL_AJUSTADA = "ReceitaCorrenteLiquidaAjustadaParaCalculoDosLimitesDeEndividamento"

_COLUNA_POR_QUADRIMESTRE = {
    "1": "Até o 1º Quadrimestre",
    "2": "Até o 2º Quadrimestre",
    "3": "Até o 3º Quadrimestre",
}

#: **O Anexo 04 nomeia a coluna de outro jeito.** Os anexos 02 e 03 identificam o
#: quadrimestre ("Até o 2º Quadrimestre"); o 04 diz apenas "de Referência", porque só
#: publica o quadrimestre corrente. Usar o nome dos outros anexos aqui devolvia `None`
#: silenciosamente, e Fortaleza aparecia com R$ 0 de operações de crédito quando tinha
#: R$ 495 milhões — folga inventada num limite de endividamento.
_COLUNA_ANEXO_04 = "Até o Quadrimestre de Referência (a)"


def coluna_acumulada(periodo_rgf: str, *, anexo: str = "02") -> str | None:
    """Coluna do acumulado até o quadrimestre, no vocabulário **daquele anexo**."""
    if "-Q" not in periodo_rgf:
        return None
    if anexo == "04":
        return _COLUNA_ANEXO_04
    return _COLUNA_POR_QUADRIMESTRE.get(periodo_rgf[-1])


def _valor(
    session: Session,
    *,
    cod_ibge: str,
    periodo: str,
    versao: str,
    conta: str,
    marca_anexo: str,
    coluna: str | None = None,
) -> Decimal | None:
    stmt = select(SilverRgf.valor).where(
        SilverRgf.cod_ibge == cod_ibge,
        SilverRgf.periodo == periodo,
        SilverRgf.versao_entrega == versao,
        SilverRgf.anexo.ilike(f"%{marca_anexo}%"),
        SilverRgf.cod_conta == conta,
    )
    if coluna is not None:
        stmt = stmt.where(SilverRgf.coluna == coluna)
    bruto = session.scalar(stmt.limit(1))
    return Decimal(bruto) if bruto is not None else None


def _valor_vigente(
    session: Session,
    *,
    cod_ibge: str,
    periodo: str,
    versao: str,
    conta: str,
    marca_anexo: str,
    coluna: str | None,
    republicavel: bool,
    as_of: datetime | None = None,
) -> Decimal | None:
    """Valor de ``conta``, olhando as entregas subsequentes do exercício que o republicam.

    O RGF republica os quadrimestres já decorridos a cada entrega nova — é assim que a
    retificação chega, sem versão nova do mesmo período (§2 CLAUDE.md, regra 3 —
    bitemporalidade: "retificação supera a versão anterior, não apaga"). Os Anexos 02/03
    publicam esse comparativo por quadrimestre ("Até o Nº Quadrimestre"); a entrega do 2º
    quadrimestre republica a coluna do 1º com o valor corrigido, e a do 3º repete a do 2º.
    Ler só a entrega do próprio período trava no primeiro valor publicado (A15) — em
    Fortaleza/vizinhos, isso produziu RCL subestimada e limites de endividamento errados.

    ``republicavel=False`` (Anexo 01 — pessoal — e Anexo 04 — operações de crédito) pula
    a busca: essas contas **não têm** coluna por quadrimestre — cada entrega publica só o
    quadrimestre corrente, sem restatement —, e "olhar para frente" acharia o total de
    OUTRO quadrimestre, não uma correção do mesmo. Verificado no acervo: 4.168 linhas de
    ``ReceitaCorrenteLiquidaAjustada`` (Anexo 01) usam sempre a coluna "Valor", nunca
    "Até o Nº Quadrimestre" — não há retificação recuperável ali.
    """
    if not republicavel or coluna is None or "-Q" not in periodo:
        return _valor(
            session, cod_ibge=cod_ibge, periodo=periodo, versao=versao,
            conta=conta, marca_anexo=marca_anexo, coluna=coluna,
        )
    ano_str, _, quad_str = periodo.rpartition("-Q")
    try:
        ano, indice = int(ano_str), int(quad_str)
    except ValueError:
        return _valor(
            session, cod_ibge=cod_ibge, periodo=periodo, versao=versao,
            conta=conta, marca_anexo=marca_anexo, coluna=coluna,
        )
    encontrado: Decimal | None = None
    for candidato_idx in range(indice, 4):
        periodo_cand = f"{ano}-Q{candidato_idx}"
        versao_cand = (
            versao if periodo_cand == periodo
            else ingestion_repo.resolve_versao(
                session, cod_ibge=cod_ibge, relatorio="RGF", periodo=periodo_cand, as_of=as_of,
            )
        )
        if versao_cand is None:
            continue
        valor = _valor(
            session, cod_ibge=cod_ibge, periodo=periodo_cand, versao=versao_cand,
            conta=conta, marca_anexo=marca_anexo, coluna=coluna,
        )
        if valor is not None:
            encontrado = valor  # a entrega mais recente que publicar vence (retificação supera)
    return encontrado


def _anexo_entregue(
    session: Session, *, cod_ibge: str, periodo: str, versao: str, marca: str
) -> bool:
    """O ente entregou este anexo? Distingue 'declarou zero' de 'não informou'."""
    return (
        session.scalar(
            select(SilverRgf.id)
            .where(
                SilverRgf.cod_ibge == cod_ibge,
                SilverRgf.periodo == periodo,
                SilverRgf.versao_entrega == versao,
                SilverRgf.anexo.ilike(f"%{marca}%"),
            )
            .limit(1)
        )
        is not None
    )


def rcl_ajustada(
    session: Session, *, cod_ibge: str, periodo: str, versao: str, as_of: datetime | None = None
) -> Decimal | None:
    """RCL Ajustada publicada pelo ente — o denominador legal destes limites.

    Procura no Anexo 02 e, na falta, no 03: os dois a publicam, e um ente pode entregar um
    sem o outro. Recalculá-la a partir da RCL exigiria reproduzir a dedução das emendas
    individuais, quando o próprio ente já publicou o resultado. Republicação entre
    entregas do mesmo exercício é considerada (A15) — ver :func:`_valor_vigente`.
    """
    coluna = coluna_acumulada(periodo)
    for marca in ("02", "03"):
        valor = _valor_vigente(
            session, cod_ibge=cod_ibge, periodo=periodo, versao=versao,
            conta=CONTA_RCL_AJUSTADA, marca_anexo=marca, coluna=coluna,
            republicavel=True, as_of=as_of,
        )
        if valor is not None and valor > 0:
            return valor
    return None


def _total_garantias(
    session: Session, *, cod_ibge: str, periodo: str, versao: str, as_of: datetime | None = None
) -> Decimal | None:
    """Total de garantias concedidas. ``None`` = anexo não entregue; ``0`` = nenhuma.

    Republicação entre entregas do mesmo exercício é considerada (A15).
    """
    coluna = coluna_acumulada(periodo)
    valor = _valor_vigente(
        session, cod_ibge=cod_ibge, periodo=periodo, versao=versao,
        conta=CONTA_GARANTIAS, marca_anexo="03", coluna=coluna,
        republicavel=True, as_of=as_of,
    )
    if valor is not None:
        return valor
    if _anexo_entregue(session, cod_ibge=cod_ibge, periodo=periodo, versao=versao, marca="03"):
        # O Anexo 03 **é** o demonstrativo das garantias. Entregá-lo sem linha de garantia
        # é o ente declarando que não concedeu nenhuma — informação, não ausência dela.
        return Decimal(0)
    return None


def total_operacoes_credito(
    session: Session, *, cod_ibge: str, periodo: str, versao: str
) -> Decimal | None:
    """Operações de crédito contratadas no exercício, acumuladas até o quadrimestre.

    **Sem republicação (A15):** o Anexo 04 nomeia a coluna "de Referência" — publica só o
    quadrimestre corrente, sem comparativo dos anteriores (ver docstring de
    :func:`_valor_vigente`). Não há valor "mais recente" a recuperar aqui.
    """
    coluna = coluna_acumulada(periodo, anexo="04")
    valor = _valor(
        session, cod_ibge=cod_ibge, periodo=periodo, versao=versao,
        conta=CONTA_OPERACOES, marca_anexo="04", coluna=coluna,
    )
    if valor is not None:
        return valor
    if _anexo_entregue(session, cod_ibge=cod_ibge, periodo=periodo, versao=versao, marca="04"):
        return Decimal(0)
    return None


def total_garantias(
    session: Session, *, cod_ibge: str, periodo: str, versao: str, as_of: datetime | None = None
) -> Decimal | None:
    """Fachada pública de :func:`_total_garantias`."""
    return _total_garantias(
        session, cod_ibge=cod_ibge, periodo=periodo, versao=versao, as_of=as_of
    )


def percentual_publicado_garantias(
    session: Session, *, cod_ibge: str, periodo: str, versao: str, as_of: datetime | None = None
) -> Decimal | None:
    """O percentual que o ente publicou — contraprova do nosso cálculo, quando existe.

    **Filtrar a coluna é o que faz a contraprova valer.** Sem isso vinha a linha "SALDO DO
    EXERCÍCIO ANTERIOR" (0,43% no Ceará) e a comparação acusava divergência contra o nosso
    0,28% — que é exatamente o que o ente publica para o mesmo quadrimestre. Uma
    reconciliação que compara períodos diferentes produz alarme falso, e alarme falso gasta
    a confiança que a verdadeira vai precisar. Republicação considerada (A15), para
    continuar comparando a mesma coisa que :func:`_total_garantias`/:func:`rcl_ajustada`.
    """
    return _valor_vigente(
        session, cod_ibge=cod_ibge, periodo=periodo, versao=versao,
        conta=CONTA_GARANTIAS_PCT, marca_anexo="03", coluna=coluna_acumulada(periodo),
        republicavel=True, as_of=as_of,
    )


def materializar_limites_endividamento(
    session: Session,
    *,
    cod_ibge: str,
    periodo_rgf: str,
    versao: str,
    as_of: datetime | None = None,
) -> list[str]:
    """Apura garantias e operações de crédito e os grava no mart.

    Devolve os indicadores efetivamente materializados. Um indicador **não** é gravado
    quando falta o insumo — nem com zero, nem com a base errada: um limite apurado sobre
    denominador inventado é pior que limite ausente, porque parece conformidade.
    """
    from app.modules.indicators import service as indicators
    from app.shared import periodo as periodo_util
    from app.shared.source_ref import SourceRef

    base = rcl_ajustada(
        session, cod_ibge=cod_ibge, periodo=periodo_rgf, versao=versao, as_of=as_of
    )
    if base is None or base <= 0:
        return []

    # **O mart fala em bimestre.** Os insumos vêm do RGF (quadrimestral), mas
    # `gold.mart_indicador` é ancorado no bimestre do RREO — é assim que a DCL e o pessoal
    # já são gravados, e é o período que a página de Limites consulta. Gravar sob `2025-Q3`
    # fazia o indicador existir e não aparecer em tela nenhuma.
    periodo_mart = periodo_util.em_bimestre(periodo_rgf) or periodo_rgf

    gravados: list[str] = []
    for indicador, total, anexo in (
        (
            "garantias",
            total_garantias(
                session, cod_ibge=cod_ibge, periodo=periodo_rgf, versao=versao, as_of=as_of
            ),
            "Anexo 03",
        ),
        (
            "operacoes_credito",
            total_operacoes_credito(session, cod_ibge=cod_ibge, periodo=periodo_rgf, versao=versao),
            "Anexo 04",
        ),
    ):
        if total is None:
            continue
        indicators.classificar_sobre_base(
            session,
            cod_ibge,
            periodo_mart,
            indicador,
            total,
            base,
            # O nome do denominador viaja na linha para que nenhuma tela rotule isto como
            # "% da RCL": é da RCL **Ajustada**, e a diferença é a dedução das emendas.
            denominador="rcl_ajustada",
            versao_entrega=versao,
            as_of=as_of,
            source_ref=SourceRef(
                relatorio="RGF", anexo=anexo, periodo=periodo_rgf, versao_entrega=versao
            ),
        )
        gravados.append(indicador)
    return gravados
