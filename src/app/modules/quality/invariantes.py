"""As invariantes do domínio, verificadas **no dado** e não só no código.

O CLAUDE.md abre as regras invariantes com cinco afirmações que valem em todo o sistema. O
código as cumpre — há `assert`, há 422, há tipo. O **dado** pode violá-las em silêncio, e
foi o que aconteceu duas vezes nesta auditoria:

* a esfera era exigida pelo código e estava nula para União e Distrito Federal, porque a
  fonte publica quatro esferas e o normalizador conhecia duas;
* a RCL é declarada como "o denominador", e 32 linhas de ``gold.fato_rcl`` guardavam
  **zero** — vindo de bimestre que o ente não entregou.

Uma invariante conferida só no código é uma invariante que o dado pode violar. Este módulo
faz a pergunta ao banco.

## Diferença para os *checks* de qualidade

Os checks de ``quality.checks`` conferem **um ente, um período**: "a soma dos filhos bate
com o pai?", "a RCL recalculada bate com a publicada?". São verificações de conteúdo,
rodadas por linha.

As invariantes aqui são **estruturais e globais**: valem para toda linha de toda tabela,
sempre, e a violação não é divergência de valor — é defeito de modelo ou de carga. Uma
falha aqui não pede investigação de um ente: pede correção do sistema.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Invariante:
    """Uma regra que vale em todo o acervo, com a consulta que a refuta."""

    codigo: str
    regra: str
    #: Por que a violação importa — o que quebra na tela quando ela ocorre.
    consequencia: str
    #: SQL que devolve **as violações**. Vazio = invariante respeitada.
    sql: str
    #: Onde a regra está escrita.
    fundamento: str


INVARIANTES: tuple[Invariante, ...] = (
    Invariante(
        codigo="esfera_obrigatoria",
        regra="Todo ente do catálogo tem esfera.",
        consequencia=(
            "Sem esfera não há teto aplicável: o cálculo de limite recusa o ente com 422 "
            "'esfera desconhecida'. Foi o caso do Distrito Federal, que responde pelo teto "
            "estadual de pessoal (49% da RCL no Executivo)."
        ),
        fundamento="CLAUDE.md §2, regra invariante 1 · LRF art. 20, II",
        sql="select cod_ibge, nome from gold.dim_ente where esfera is null",
    ),
    Invariante(
        codigo="esfera_coerente_com_codigo",
        regra="Município tem código de 7 dígitos; UF, 2; União, 1.",
        consequencia=(
            "Incoerência aqui indica carga trocando o tipo do ente — e o tipo decide o teto."
        ),
        fundamento="Padrão IBGE · CLAUDE.md §2",
        sql="""
            select cod_ibge, nome, esfera
            from gold.dim_ente
            where (esfera = 'municipal' and length(cod_ibge) <> 7)
               or (esfera = 'estadual'  and length(cod_ibge) <> 2)
               or (esfera = 'federal'   and length(cod_ibge) <> 1)
        """,
    ),
    Invariante(
        codigo="catalogo_cobre_o_silver",
        regra="Todo ente conhecido pela fonte está no catálogo.",
        consequencia=(
            "Ente fora do catálogo some do denominador: o consolidado do Ceará reportava "
            "183 de 184 municípios, contaminando toda média e todo percentual do painel "
            "estadual sem nada na tela sugerir a falta."
        ),
        fundamento="Completude do cadastro SICONFI",
        sql="""
            select e.cod_ibge, e.nome
            from silver.siconfi_entes e
            where not exists (select 1 from gold.dim_ente d where d.cod_ibge = e.cod_ibge)
        """,
    ),
    Invariante(
        codigo="rcl_nunca_zero",
        regra="A RCL materializada nunca é zero.",
        consequencia=(
            "A RCL é o denominador de quase todo limite da LRF. Zero ali não significa "
            "'o ente arrecadou zero': significa que faltou o Anexo 03 daquele bimestre e a "
            "materialização gravou zero em vez de não gravar. Ausência virando número."
        ),
        fundamento="CLAUDE.md §2, regra invariante 2 · §5 (não tratar ausência como zero)",
        sql="""
            select cod_ibge, periodo_ref, versao_entrega
            from gold.fato_rcl
            where rcl_12m = 0 or rcl_12m is null
        """,
    ),
    Invariante(
        codigo="indicador_tem_origem",
        regra="Todo indicador materializado declara sua fonte.",
        consequencia=(
            "Número sem `source_ref` é número sem lastro: não há como o gestor chegar ao "
            "relatório, ao anexo, ao período e à versão que o originaram."
        ),
        fundamento="CLAUDE.md §6.3 (rastreabilidade é requisito de produto)",
        sql="""
            select cod_ibge, periodo, indicador
            from gold.mart_indicador
            where source_ref is null or versao_entrega is null
        """,
    ),
    Invariante(
        codigo="uma_entrega_vigente_por_periodo",
        regra="Cada (ente, relatório, período) tem no máximo uma entrega vigente.",
        consequencia=(
            "Duas vigentes fazem a resolução de versão virar sorteio: a mesma tela pode "
            "mostrar números diferentes em duas consultas, e a retificação deixa de ser "
            "rastreável."
        ),
        fundamento="CLAUDE.md §2, regra invariante 3 (bitemporalidade)",
        sql="""
            select cod_ibge, relatorio, periodo, count(*) as vigentes
            from gold.dim_entrega
            where vigente is true
            group by 1, 2, 3
            having count(*) > 1
        """,
    ),
    Invariante(
        codigo="faixa_coerente_com_o_sentido",
        regra="A faixa do indicador corresponde ao valor e ao sentido do limite.",
        consequencia=(
            "Um piso classificado com a régua de teto pinta de vermelho quem **cumpre** o "
            "mínimo constitucional — a leitura exatamente oposta à correta."
        ),
        fundamento="CLAUDE.md §2 (faixas 90/95/100; mínimos são pisos)",
        # O vocabulário é o de `indicators.limites`: piso usa adequado/insuficiente e teto
        # usa normal/alerta/prudencial/excedido. A verificação é **bidirecional** — tão
        # grave quanto não marcar quem estourou é marcar quem não estourou.
        sql="""
            select m.cod_ibge, m.periodo, m.indicador, m.valor_pct_rcl, m.faixa, l.sentido
            from gold.mart_indicador m
            join gold.dim_ente e on e.cod_ibge = m.cod_ibge
            join gold.dim_limite_legal l
              on l.indicador = m.indicador and l.esfera = e.esfera
            where m.valor_pct_rcl is not null
              and (
                    (l.sentido = 'teto' and m.valor_pct_rcl >= l.teto_pct
                     and m.faixa <> 'excedido')
                 or (l.sentido = 'teto' and m.valor_pct_rcl <  l.teto_pct
                     and m.faixa =  'excedido')
                 or (l.sentido = 'piso' and m.valor_pct_rcl >= l.teto_pct
                     and m.faixa <> 'adequado')
                 or (l.sentido = 'piso' and m.valor_pct_rcl <  l.teto_pct
                     and m.faixa <> 'insuficiente')
                  )
        """,
    ),
)


@dataclass(frozen=True)
class Violacao:
    codigo: str
    regra: str
    consequencia: str
    fundamento: str
    quantidade: int
    #: Amostra das linhas que violam — o suficiente para começar a investigar.
    exemplos: list[dict[str, object]]


def verificar(session: Session, *, amostra: int = 5) -> list[Violacao]:
    """Roda todas as invariantes e devolve **apenas** as violadas.

    Devolver as respeitadas encheria o relatório de linhas verdes e faria a violação se
    perder no meio — o oposto do que um verificador de invariante serve.
    """
    violacoes: list[Violacao] = []
    for inv in INVARIANTES:
        linhas = session.execute(text(inv.sql)).mappings().all()
        if not linhas:
            continue
        violacoes.append(
            Violacao(
                codigo=inv.codigo,
                regra=inv.regra,
                consequencia=inv.consequencia,
                fundamento=inv.fundamento,
                quantidade=len(linhas),
                exemplos=[dict(linha) for linha in linhas[:amostra]],
            )
        )
    return violacoes
