"""Caminhos de junção sancionados entre as tabelas consultáveis (Sprint IA-2).

O risco que esta lista existe para conter **não** é o ``JOIN`` que falha — esse aparece.
É o que funciona e multiplica linha: juntar ``mart_indicador`` a ``dim_entrega`` sem
``versao_entrega`` na chave faz cada retificação duplicar o ente, e a contagem dobra sem
que nada estoure. Por isso ``condicao`` (o filtro que precisa viajar junto) e ``nota`` (o
que acontece se ela for esquecida) são parte do caminho, não comentário sobre ele.

A lição de origem é a IA-1b: **a vigência é resolvida pelo JOIN, não por um filtro no
fim** — partindo da entrega vigente, uma versão superada não tem chave para casar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.modules.dictionary.verbetes import REVISAO


@dataclass(frozen=True)
class Juncao:
    """Um caminho de junção sancionado entre duas tabelas consultáveis."""

    origem_tabela: str
    origem_colunas: tuple[str, ...]
    destino_tabela: str
    destino_colunas: tuple[str, ...]
    cardinalidade: str
    nota: str
    condicao: str | None = None
    fonte_definicao: str = "curadoria Sprint IA-2 sobre as chaves reais da gold"
    atualizado_em: date = REVISAO


_VIGENCIA = (
    "É por esta junção que a bitemporalidade se resolve (§6.5). Sem 'versao_entrega' na "
    "chave, uma retificação faz o mesmo ente aparecer duas vezes — o COUNT dobra e a média "
    "muda, sem erro nenhum na consulta."
)

JUNCOES: tuple[Juncao, ...] = (
    Juncao(
        origem_tabela="gold.mart_indicador",
        origem_colunas=("cod_ibge",),
        destino_tabela="gold.dim_ente",
        destino_colunas=("cod_ibge",),
        cardinalidade="n:1",
        nota=(
            "Caminho normal para filtrar por esfera, UF, região ou faixa populacional. "
            "dim_ente tem uma linha por ente, então não multiplica."
        ),
    ),
    Juncao(
        origem_tabela="gold.mart_indicador",
        origem_colunas=("cod_ibge", "periodo", "versao_entrega"),
        destino_tabela="gold.dim_entrega",
        destino_colunas=("cod_ibge", "periodo", "versao_entrega"),
        cardinalidade="n:1",
        condicao="gold.dim_entrega.relatorio = 'RREO' AND gold.dim_entrega.vigente IS TRUE",
        nota=_VIGENCIA,
    ),
    Juncao(
        origem_tabela="gold.mart_indicador",
        origem_colunas=("periodo",),
        destino_tabela="gold.dim_periodo",
        destino_colunas=("codigo",),
        cardinalidade="n:1",
        nota=(
            "Traz ano/bimestre para agregação temporal. O mart fala sempre em bimestre do "
            "RREO, então o nível 2 da hierarquia é o que casa."
        ),
    ),
    Juncao(
        origem_tabela="gold.mart_indicador",
        origem_colunas=("indicador",),
        destino_tabela="gold.dicionario_indicador",
        destino_colunas=("codigo",),
        cardinalidade="n:1",
        nota=(
            "O dicionário se autodescreve: esta junção traz fórmula, denominador correto, "
            "sentido e base legal do indicador para dentro da própria consulta."
        ),
    ),
    Juncao(
        origem_tabela="gold.mart_indicador",
        origem_colunas=("indicador",),
        destino_tabela="gold.dim_limite_legal",
        destino_colunas=("indicador",),
        cardinalidade="n:1",
        condicao=(
            "gold.dim_limite_legal.esfera = gold.dim_ente.esfera AND "
            "gold.dim_limite_legal.poder = CASE WHEN gold.mart_indicador.indicador = "
            "'pessoal_executivo' THEN 'Executivo' ELSE '' END"
        ),
        nota=(
            "Junção por 'indicador' apenas é o erro clássico: casa a linha municipal E a "
            "estadual, duplica o resultado e traz dois tetos diferentes para o mesmo ente. "
            "A esfera vem de dim_ente, e o poder é parte da chave (string vazia, não NULL)."
        ),
    ),
    Juncao(
        origem_tabela="gold.mart_indicador",
        origem_colunas=("cod_ibge", "periodo", "versao_entrega"),
        destino_tabela="gold.fato_rcl",
        destino_colunas=("cod_ibge", "periodo_ref", "versao_entrega"),
        cardinalidade="n:1",
        nota=(
            "Só vale quando mart_indicador.denominador = 'rcl'. Para os limites da LRF o "
            "denominador é a RCL AJUSTADA, que não está aqui: ela viaja em "
            "mart_indicador.base_valor e nos fatos de pessoal/dívida. Note que a coluna do "
            "período chama-se 'periodo_ref' deste lado."
        ),
    ),
    Juncao(
        origem_tabela="gold.fato_pessoal",
        origem_colunas=("cod_ibge", "periodo", "versao_entrega"),
        destino_tabela="gold.dim_entrega",
        destino_colunas=("cod_ibge", "periodo", "versao_entrega"),
        cardinalidade="n:1",
        condicao="gold.dim_entrega.relatorio = 'RGF' AND gold.dim_entrega.vigente IS TRUE",
        nota=(
            _VIGENCIA
            + " Atenção à cadência: o período aqui é do RGF (quadrimestre/semestre), não o "
            "bimestre do mart — não existe junção direta fato_pessoal↔mart_indicador por "
            "'periodo'."
        ),
    ),
    Juncao(
        origem_tabela="gold.fato_divida",
        origem_colunas=("cod_ibge", "periodo", "versao_entrega"),
        destino_tabela="gold.dim_entrega",
        destino_colunas=("cod_ibge", "periodo", "versao_entrega"),
        cardinalidade="n:1",
        condicao="gold.dim_entrega.relatorio = 'RGF' AND gold.dim_entrega.vigente IS TRUE",
        nota=_VIGENCIA,
    ),
    Juncao(
        origem_tabela="gold.fato_rcl",
        origem_colunas=("cod_ibge", "periodo_ref", "versao_entrega"),
        destino_tabela="gold.dim_entrega",
        destino_colunas=("cod_ibge", "periodo", "versao_entrega"),
        cardinalidade="n:1",
        condicao="gold.dim_entrega.relatorio = 'RREO' AND gold.dim_entrega.vigente IS TRUE",
        nota=_VIGENCIA + " A coluna de período do fato chama-se 'periodo_ref'.",
    ),
    Juncao(
        origem_tabela="gold.fato_pessoal",
        origem_colunas=("cod_ibge",),
        destino_tabela="gold.dim_ente",
        destino_colunas=("cod_ibge",),
        cardinalidade="n:1",
        nota=(
            "Necessária para saber a esfera antes de aplicar o teto do art. 20 — 54% no "
            "município, 49% no estado."
        ),
    ),
    Juncao(
        origem_tabela="gold.mart_carteira",
        origem_colunas=("cod_ibge",),
        destino_tabela="gold.dim_ente",
        destino_colunas=("cod_ibge",),
        cardinalidade="n:1",
        nota="Filtro territorial/porte sobre o snapshot de conformidade.",
    ),
    Juncao(
        origem_tabela="gold.mart_cobertura_fonte",
        origem_colunas=("cod_ibge",),
        destino_tabela="gold.dim_ente",
        destino_colunas=("cod_ibge",),
        cardinalidade="n:1",
        nota=(
            "Responde 'quem não entregou': o ente sem linha nesta tabela é ausência da "
            "fonte, e só um LEFT JOIN a partir de dim_ente a revela — INNER JOIN esconde "
            "exatamente quem se quer encontrar."
        ),
    ),
    Juncao(
        origem_tabela="gold.mart_consolidado_uf",
        origem_colunas=("uf",),
        destino_tabela="gold.dim_ente",
        destino_colunas=("uf",),
        cardinalidade="1:n",
        nota=(
            "A única 1:n da lista, e a mais perigosa: a linha da UF JÁ é o agregado. "
            "Juntá-la aos entes para 'conferir' repete o consolidado uma vez por município. "
            "Use para listar quem compõe, nunca para somar. E lembre: o consolidado dos "
            "municípios não é o governo do estado."
        ),
    ),
    Juncao(
        origem_tabela="gold.dim_periodo",
        origem_colunas=("parent_codigo",),
        destino_tabela="gold.dim_periodo",
        destino_colunas=("codigo",),
        cardinalidade="n:1",
        nota=(
            "Auto-junção da hierarquia temporal (drill UP: mês → bimestre → exercício). "
            "Para subárvores, o operador de ltree sobre 'path' é mais barato que a "
            "recursão."
        ),
    ),
)
