"""Espaço fiscal — quanto ainda cabe, em reais, antes de o limite ser rompido.

A projeção existente responde *quando* o limite será cruzado. Um gestor que precisa
decidir sobre um reajuste, uma contratação ou uma operação de crédito precisa da outra
metade da resposta: **quanto ainda cabe**. "Cruza o teto em 2026-B2" não se transforma em
decisão; "há R$ 41,3 milhões de margem, e o reajuste que você estuda consome R$ 52
milhões" se transforma.

## Por que em reais, e não só em pontos percentuais

O limite da LRF é percentual (54% da RCL), mas nenhuma decisão administrativa é tomada em
pontos percentuais. O ordenador de despesa assina empenho em reais. Converter a margem
para reais é o que liga o indicador ao ato — e a conversão é exata, não uma aproximação:
``margem_rs = (teto% − projetado%) / 100 × RCL``.

## Quando o limite já foi rompido, o número muda de nome

Margem negativa não é "margem negativa": é o **corte necessário** para voltar ao limite. São
o mesmo número com sinais e significados opostos, e apresentá-los sob o mesmo rótulo faria
o gestor ler folga onde há dívida de ajuste. Por isso :class:`EspacoFiscal` carrega
``situacao`` — a palavra que diz qual dos dois casos está na tela.

## Recondução (LRF art. 23)

Excedido o limite de pessoal, a lei não pede "melhorar": pede eliminar o excesso em **dois
quadrimestres**, ao menos um terço no primeiro. :func:`esforco_reconducao` traduz isso em
quanto a despesa precisa cair por período — que é o que se leva à mesa de negociação.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.modules.indicators.limites import LimiteLegal

#: Prazo legal de recondução ao limite de despesa com pessoal (LRF art. 23, caput).
QUADRIMESTRES_RECONDUCAO = 2
#: Fração mínima do excesso a eliminar no primeiro quadrimestre (LRF art. 23, caput).
FRACAO_PRIMEIRO_QUADRIMESTRE = Decimal("0.3333333333")

SITUACAO_FOLGA = "folga"
SITUACAO_EXCEDIDO = "excedido"
SITUACAO_NAO_APLICAVEL = "nao_aplicavel"


@dataclass(frozen=True)
class EspacoFiscal:
    """Margem (ou excesso) entre a posição projetada e o limite legal.

    ``margem_pp`` e ``margem_rs`` são **sempre positivos**: o que distingue folga de
    excesso é ``situacao``. Um número negativo rotulado "margem" convida à leitura errada
    justamente no caso em que o erro custa mais caro.
    """

    indicador: str
    sentido: str
    situacao: str
    limite_pct: Decimal
    projetado_pct: Decimal
    #: Distância até o limite, em pontos percentuais da base.
    margem_pp: Decimal
    #: A mesma distância convertida em reais sobre a base projetada.
    margem_rs: Decimal | None
    #: Base (RCL, ou impostos+transferências nos mínimos) usada na conversão.
    base_rs: Decimal | None
    base_nome: str
    #: Período de onde a base saiu. Não é o mesmo que ``periodo_alvo``: a base é observada
    #: e o alvo é projetado, e quando a RCL do período exato falta, a base vem da mais
    #: recente — dizer qual é o que permite ao gestor julgar se a conversão serve.
    base_periodo: str | None = None
    periodo_alvo: str | None = None


def calcular(
    limite: LimiteLegal,
    projetado_pct: Decimal,
    *,
    base_rs: Decimal | None,
    base_nome: str = "rcl",
    base_periodo: str | None = None,
    periodo_alvo: str | None = None,
) -> EspacoFiscal:
    """Margem até o limite na posição projetada.

    Vale para teto e para piso — com a semântica invertida, que é a fonte clássica de erro
    nesta base: num teto, folga é estar **abaixo**; num piso, é estar **acima**. Calcular a
    diferença sempre no mesmo sentido produziria "excedido" para um ente que cumpre o
    mínimo de saúde com sobra.
    """
    if limite.sentido == "piso":
        diferenca = projetado_pct - limite.teto_pct
    else:
        diferenca = limite.teto_pct - projetado_pct

    situacao = SITUACAO_FOLGA if diferenca >= 0 else SITUACAO_EXCEDIDO
    margem_pp = abs(diferenca)
    # `base_rs` ausente não é zero: sem a base, a conversão para reais não existe, e
    # inventá-la com zero anunciaria margem nenhuma a quem talvez tenha bastante.
    margem_rs = (margem_pp / Decimal(100)) * base_rs if base_rs is not None else None

    return EspacoFiscal(
        indicador=limite.indicador,
        sentido=limite.sentido,
        situacao=situacao,
        limite_pct=limite.teto_pct,
        projetado_pct=projetado_pct,
        margem_pp=margem_pp,
        margem_rs=margem_rs,
        base_rs=base_rs,
        base_nome=base_nome,
        base_periodo=base_periodo,
        periodo_alvo=periodo_alvo,
    )


@dataclass(frozen=True)
class EsforcoReconducao:
    """O que a LRF exige de quem excedeu o limite de despesa com pessoal.

    Art. 23: eliminado o excesso em **dois quadrimestres**, sendo pelo menos um terço no
    primeiro. Não é meta de gestão — é obrigação, e o descumprimento aciona as vedações do
    §3º (receber transferências voluntárias, obter garantia, contratar operação de crédito).
    """

    aplicavel: bool
    excesso_pp: Decimal
    excesso_rs: Decimal | None
    #: Redução mínima no primeiro quadrimestre (um terço do excesso).
    primeiro_quadrimestre_pp: Decimal
    primeiro_quadrimestre_rs: Decimal | None
    #: Redução no segundo quadrimestre (o restante).
    segundo_quadrimestre_pp: Decimal
    segundo_quadrimestre_rs: Decimal | None
    fundamento: str = "LRF art. 23 · vedações do §3º em caso de descumprimento"


def esforco_reconducao(espaco: EspacoFiscal) -> EsforcoReconducao:
    """Traduz o excesso projetado no cronograma de redução que a lei impõe.

    Devolve ``aplicavel=False`` quando não há excesso — e nesse caso os valores são zero,
    não ``None``: "nada a reconduzir" é uma resposta, diferente de "não sei".
    """
    zero = Decimal(0)
    if espaco.situacao != SITUACAO_EXCEDIDO or espaco.sentido != "teto":
        return EsforcoReconducao(
            aplicavel=False,
            excesso_pp=zero,
            excesso_rs=zero if espaco.margem_rs is not None else None,
            primeiro_quadrimestre_pp=zero,
            primeiro_quadrimestre_rs=zero if espaco.margem_rs is not None else None,
            segundo_quadrimestre_pp=zero,
            segundo_quadrimestre_rs=zero if espaco.margem_rs is not None else None,
        )

    excesso_pp = espaco.margem_pp
    primeiro_pp = excesso_pp * FRACAO_PRIMEIRO_QUADRIMESTRE
    segundo_pp = excesso_pp - primeiro_pp

    def em_reais(pp: Decimal) -> Decimal | None:
        if espaco.base_rs is None:
            return None
        return (pp / Decimal(100)) * espaco.base_rs

    return EsforcoReconducao(
        aplicavel=True,
        excesso_pp=excesso_pp,
        excesso_rs=em_reais(excesso_pp),
        primeiro_quadrimestre_pp=primeiro_pp,
        primeiro_quadrimestre_rs=em_reais(primeiro_pp),
        segundo_quadrimestre_pp=segundo_pp,
        segundo_quadrimestre_rs=em_reais(segundo_pp),
    )
