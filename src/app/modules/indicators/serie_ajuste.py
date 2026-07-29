"""Ajustes de comparabilidade de série: deflação pelo IPCA e per capita (Sprint 25).

Responde à pergunta gerencial "a receita/despesa cresceu de verdade, ou só acompanhou a
inflação e a população?" — comparar exercícios em valores **nominais** engana.

Duas regras inegociáveis:

1. **Lacuna não vira zero.** Se falta um mês do IPCA entre o período e a base, o fator
   daquele ponto é ``None`` (a série aparece só em nominal), nunca uma inflação parcial.
2. **A população carrega o ano de referência.** O IBGE estima por ano; o per capita de
   2022 usa a população de 2022. Sem população do ano, cai para o ano mais próximo
   **anterior** e o ``pop_ano_ref`` denuncia a substituição na tela.

Base legal/estatística: IPCA (IBGE) na série 433 do SGS/BCB, ingerida pela Sprint 1B.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.indicators import repository
from app.modules.indicators.schemas import AjustePeriodo, SerieAjuste
from app.shared import periodo as periodo_util

FONTE_DEFLATOR = "silver.bcb_indice#433 (IPCA/IBGE via SGS-BCB)"
FONTE_POPULACAO_IBGE = "silver.ibge_populacao (estimativas IBGE)"
FONTE_POPULACAO_DIM = "gold.dim_ente.populacao (cadastro conformado)"

_CEM = Decimal(100)
_UM = Decimal(1)


def _meses_entre(inicio: tuple[int, int], fim: tuple[int, int]) -> list[tuple[int, int]]:
    """Meses de ``inicio`` (exclusivo) até ``fim`` (inclusivo), em ordem cronológica."""
    meses: list[tuple[int, int]] = []
    ano, mes = inicio
    while (ano, mes) < fim:
        mes += 1
        if mes > 12:
            ano, mes = ano + 1, 1
        meses.append((ano, mes))
    return meses


def _fator(
    ipca: dict[tuple[int, int], Decimal],
    de: tuple[int, int],
    para: tuple[int, int],
) -> Decimal | None:
    """Fator que leva preços de ``de`` para preços de ``para`` (``None`` se falta mês)."""
    if de == para:
        return _UM
    inverter = de > para
    inicio, fim = (para, de) if inverter else (de, para)
    fator = _UM
    for mes in _meses_entre(inicio, fim):
        variacao = ipca.get(mes)
        if variacao is None:
            return None
        fator *= _UM + variacao / _CEM
    if inverter:
        return _UM / fator if fator else None
    return fator


def _populacao(
    por_ano: dict[int, int], fallback: tuple[int, int | None] | None, ano: int
) -> tuple[int | None, int | None]:
    """População do ano (ou do ano anterior mais próximo) + o ano efetivamente usado."""
    if ano in por_ano:
        return por_ano[ano], ano
    anteriores = [a for a in por_ano if a < ano]
    if anteriores:
        usado = max(anteriores)
        return por_ano[usado], usado
    if fallback is not None:
        return fallback[0], fallback[1]
    return None, None


def calcular(
    session: Session, cod_ibge: str, periodos: list[str], base_periodo: str
) -> SerieAjuste:
    """Fatores de deflação e população de cada período da série, a preços de ``base``."""
    ipca = repository.ipca_mensal(session)
    por_ano = repository.populacao_por_ano(session, cod_ibge=cod_ibge)
    fallback = repository.populacao_dim_ente(session, cod_ibge=cod_ibge)

    base_mes = periodo_util.mes_final(base_periodo)
    base_ano = periodo_util.parse(base_periodo)[0] if base_mes is not None else None
    itens: list[AjustePeriodo] = []
    for p in periodos:
        mes = periodo_util.mes_final(p)
        ano = periodo_util.parse(p)[0] if mes is not None else None
        fator: Decimal | None = None
        if mes is not None and ano is not None and base_mes is not None and base_ano is not None:
            fator = _fator(ipca, (ano, mes), (base_ano, base_mes))
        pop, pop_ano = _populacao(por_ano, fallback, ano) if ano is not None else (None, None)
        itens.append(
            AjustePeriodo(
                periodo=p,
                fator_deflator=fator,
                ipca_acum_pct=(fator - _UM) * _CEM if fator is not None else None,
                populacao=pop,
                pop_ano_ref=pop_ano,
            )
        )

    com_fator = [i for i in itens if i.fator_deflator is not None]
    com_pop = [i for i in itens if i.populacao is not None]
    faltando = [i.periodo for i in itens if i.fator_deflator is None]
    observacao = None
    if faltando:
        observacao = (
            "Sem IPCA (série 433) para deflacionar "
            + ", ".join(faltando)
            + " — esses pontos só existem em valores nominais."
        )
    fonte_pop = None
    if com_pop:
        fonte_pop = FONTE_POPULACAO_IBGE if por_ano else FONTE_POPULACAO_DIM
    return SerieAjuste(
        base_periodo=base_periodo,
        deflator_disponivel=bool(com_fator),
        populacao_disponivel=bool(com_pop),
        fonte_deflator=FONTE_DEFLATOR,
        fonte_populacao=fonte_pop,
        observacao=observacao,
        itens=itens,
    )


def real(valor: Decimal | None, ajuste: AjustePeriodo | None) -> Decimal | None:
    """Valor a preços do período base (``None`` quando o deflator não existe)."""
    if valor is None or ajuste is None or ajuste.fator_deflator is None:
        return None
    return valor * ajuste.fator_deflator


def per_capita(valor: Decimal | None, ajuste: AjustePeriodo | None) -> Decimal | None:
    """Valor por habitante do ano do período (``None`` sem população conhecida)."""
    if valor is None or ajuste is None or not ajuste.populacao:
        return None
    return valor / Decimal(ajuste.populacao)


def indexar(ajuste: SerieAjuste) -> dict[str, AjustePeriodo]:
    return {i.periodo: i for i in ajuste.itens}
