"""Cobertura honesta: para quantos entes e períodos **esta página** de fato responde.

Uma tela que abre para qualquer ente e responde para um não é uma tela incompleta — é uma
tela que engana. A auditoria mediu: os mínimos constitucionais estão apurados para **1**
ente contra 180 com RREO ingerido; a MSC, para 1; SIOPS e SIOPE, para 1. Nada na interface
dizia isso, e o gestor que abre Saúde & Educação e vê "sem dado para este ente" conclui que
o **ente** não entregou — quando o que falta é a nossa carga.

A diferença entre as duas leituras é grande: a primeira faz o gestor cobrar o próprio
setor contábil; a segunda faz cobrar a plataforma. Só a segunda é verdade.

## Escopo é o do usuário, não o do país

"1 de 5.570" é irrelevante para uma Sefaz que monitora 184 municípios. A cobertura é
sempre medida **dentro da carteira de quem pergunta** — é o denominador que o gestor
reconhece, e é o único sobre o qual ele pode agir.

## Página → fontes já era dado

`FONTE_META.paginas_impactadas` declara, por fonte, quais telas deixam de ter dado se ela
falhar. Invertê-lo dá o mapa que esta cobertura precisa. Reusar o que já existe evita a
segunda lista, que envelheceria em silêncio (§6 do CLAUDE.md).
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.coverage.schemas import (
    CoberturaFonteItem,
    CoberturaIndicadorItem,
    CoberturaPagina,
    EnteCobertura,
    EscopoCobertura,
)
from app.modules.indicators.models import MartIndicador
from app.modules.ingestion.connectors.registry import FONTE_META
from app.modules.ingestion.models import MartCoberturaFonte

#: Indicadores que cada página apresenta. Complementa o mapa de fontes: a página de
#: saúde/educação depende do RREO (que quase todo ente tem) **e** dos mínimos apurados
#: (que quase nenhum tem) — declarar só a fonte esconderia justamente a lacuna.
INDICADORES_POR_PAGINA: dict[str, tuple[str, ...]] = {
    "dashboard": ("pessoal_executivo", "divida_consolidada_liquida", "resultado_primario_rcl"),
    "limites": (
        "pessoal_executivo",
        "divida_consolidada_liquida",
        "saude_minimo",
        "educacao_mde",
        "fundeb_profissionais",
    ),
    "saude-educacao": ("saude_minimo", "educacao_mde", "fundeb_profissionais"),
    "divida": ("divida_consolidada_liquida",),
    "resultado": ("resultado_primario_rcl",),
    "benchmarking": ("pessoal_executivo", "divida_consolidada_liquida", "rcl_per_capita"),
    "carteira": ("pessoal_executivo", "divida_consolidada_liquida"),
}


@lru_cache(maxsize=1)
def fontes_por_pagina() -> dict[str, tuple[str, ...]]:
    """Inverte ``FONTE_META.paginas_impactadas``. Em cache: o mapa é imutável em execução."""
    inverso: dict[str, list[str]] = defaultdict(list)
    for fonte, meta in FONTE_META.items():
        for pagina in meta.paginas_impactadas:
            inverso[pagina].append(fonte)
    return {pagina: tuple(sorted(fontes)) for pagina, fontes in inverso.items()}


def _observacao(
    ente: EnteCobertura, escopo: EscopoCobertura, lacunas: list[str]
) -> str | None:
    """A frase que separa "o ente não entregou" de "a plataforma não carregou".

    É o produto desta funcionalidade. Sem ela, a ausência de dado tem duas leituras
    possíveis e o gestor escolhe a errada — a que culpa o próprio setor contábil.
    """
    if lacunas:
        faltando = ", ".join(lacunas[:3])
        resto = f" e mais {len(lacunas) - 3}" if len(lacunas) > 3 else ""
        return (
            f"Esta página depende de dados que a plataforma ainda não carregou para a maior "
            f"parte dos entes ({faltando}{resto}). A ausência aqui é da nossa carga, não da "
            f"entrega do ente."
        )
    if not ente.tem_dado and escopo.entes_com_dado > 0:
        return (
            f"{escopo.entes_com_dado} de {escopo.entes_no_escopo} entes do seu escopo têm "
            f"dado para esta página; este não tem. Verifique se o ente publicou o relatório "
            f"no período selecionado."
        )
    return None


def build_cobertura_pagina(
    session: Session,
    *,
    pagina: str,
    cod_ibge: str,
    periodo: str | None,
    entes_do_escopo: set[str],
) -> CoberturaPagina:
    """Cobertura real da página, medida dentro do escopo e do período pedidos.

    ``periodo`` não é mero rótulo: quando informado, todas as contagens e o selo do ente
    são restritos a ele. Sem esse predicado, pedir 2024-B6 podia devolver ``tem_dado=true``
    porque havia qualquer entrega de 2025-B6 — exatamente a conclusão errada para uma tela
    vazia no período selecionado.
    """
    mapa = fontes_por_pagina()
    if pagina not in mapa and pagina not in INDICADORES_POR_PAGINA:
        raise AppError(
            status=404,
            title="Página desconhecida",
            detail=(
                f"Não há mapa de cobertura para '{pagina}'. Páginas conhecidas: "
                f"{', '.join(sorted(set(mapa) | set(INDICADORES_POR_PAGINA)))}."
            ),
        )

    fontes = mapa.get(pagina, ())
    escopo_lista = sorted(entes_do_escopo)
    if not escopo_lista:
        # Escopo vazio não é cobertura zero: é ausência de carteira. Consultar o banco
        # com `IN ()` devolveria zeros que se leem como "a plataforma não tem dado".
        return CoberturaPagina(
            pagina=pagina,
            ente=EnteCobertura(cod_ibge=cod_ibge, tem_dado=False, periodo_solicitado=periodo),
            escopo=EscopoCobertura(entes_no_escopo=0, entes_com_dado=0),
            observacao=(
                "Sua organização não tem entes na carteira, então não há cobertura a medir. "
                "Peça ao administrador para incluir os entes que você acompanha."
            ),
        )

    itens_fonte: list[CoberturaFonteItem] = []
    for fonte in fontes:
        stmt_fonte = select(
            func.count(func.distinct(MartCoberturaFonte.cod_ibge)),
            func.max(MartCoberturaFonte.periodo),
        ).where(
            MartCoberturaFonte.fonte == fonte,
            MartCoberturaFonte.cod_ibge.in_(escopo_lista),
        )
        if periodo:
            stmt_fonte = stmt_fonte.where(MartCoberturaFonte.periodo == periodo)
        linhas = session.execute(stmt_fonte).one()
        meta = FONTE_META.get(fonte)
        itens_fonte.append(
            CoberturaFonteItem(
                fonte=fonte,
                descricao=meta.descricao if meta else None,
                orgao=meta.orgao if meta else None,
                entes_com_dado=int(linhas[0] or 0),
                periodo_mais_recente=linhas[1],
            )
        )

    itens_indicador: list[CoberturaIndicadorItem] = []
    for indicador in INDICADORES_POR_PAGINA.get(pagina, ()):
        stmt_indicador = select(
            func.count(func.distinct(MartIndicador.cod_ibge)),
            func.max(MartIndicador.periodo),
        ).where(
            MartIndicador.indicador == indicador,
            MartIndicador.cod_ibge.in_(escopo_lista),
        )
        if periodo:
            stmt_indicador = stmt_indicador.where(MartIndicador.periodo == periodo)
        linhas = session.execute(stmt_indicador).one()
        itens_indicador.append(
            CoberturaIndicadorItem(
                indicador=indicador,
                entes_com_dado=int(linhas[0] or 0),
                periodo_mais_recente=linhas[1],
            )
        )

    # O ente da vez tem dado? "Tem" significa: alguma fonte da página o alcança.
    ente_tem = 0
    ente_periodo: str | None = None
    if fontes:
        filtro_ente = (
            MartCoberturaFonte.cod_ibge == cod_ibge,
            MartCoberturaFonte.fonte.in_(fontes),
        )
        stmt_ente = select(func.count()).select_from(MartCoberturaFonte).where(*filtro_ente)
        stmt_periodo = select(func.max(MartCoberturaFonte.periodo)).where(*filtro_ente)
        if periodo:
            stmt_ente = stmt_ente.where(MartCoberturaFonte.periodo == periodo)
            stmt_periodo = stmt_periodo.where(MartCoberturaFonte.periodo == periodo)
        ente_tem = session.scalar(stmt_ente) or 0
        ente_periodo = session.scalar(stmt_periodo)

    total_escopo = len(entes_do_escopo)
    # **Qual número vai em destaque.** O máximo entre as fontes seria o mais generoso e o
    # menos verdadeiro: a página de saúde/educação diria "171 de 185" porque 171 entes têm
    # RREO — quando o que ela apresenta são os mínimos, apurados para 1. O gestor leria a
    # cobertura do insumo como se fosse a do produto.
    #
    # Quando a página declara indicadores, eles **são** o produto, e a cobertura é a do
    # mais restritivo: é ele que decide se a tela responde. Sem indicadores declarados, a
    # melhor aproximação disponível é a fonte de maior alcance.
    com_dado = (
        min(i.entes_com_dado for i in itens_indicador)
        if itens_indicador
        else max((f.entes_com_dado for f in itens_fonte), default=0)
    )
    # Uma lacuna é o indicador que cobre menos de um décimo do escopo: é o caso dos
    # mínimos constitucionais (1 de 180). O corte é arbitrário de propósito e explícito —
    # o que não é arbitrário é chamar de lacuna aquilo que a tela apresenta como normal.
    lacunas = [
        i.indicador
        for i in itens_indicador
        if total_escopo > 0 and i.entes_com_dado <= max(1, total_escopo // 10)
    ]

    ente = EnteCobertura(
        cod_ibge=cod_ibge,
        tem_dado=bool(ente_tem),
        periodo_mais_recente=ente_periodo,
        periodo_solicitado=periodo,
    )
    escopo = EscopoCobertura(entes_no_escopo=total_escopo, entes_com_dado=com_dado)
    return CoberturaPagina(
        pagina=pagina,
        ente=ente,
        escopo=escopo,
        fontes=itens_fonte,
        indicadores=itens_indicador,
        lacunas=lacunas,
        observacao=_observacao(ente, escopo, lacunas),
    )
