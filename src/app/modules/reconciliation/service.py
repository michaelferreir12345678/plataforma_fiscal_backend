"""Reconciliação com o valor oficial: onde a plataforma discorda de quem publicou.

Um número calculado corretamente e um número **conferido** não são a mesma coisa. A
plataforma computa a RCL a partir dos componentes do RREO Anexo 03; o ente, no RGF Anexo
02, publica a RCL que ele próprio apurou. São dois caminhos independentes para o mesmo
fato — e é justamente por isso que compará-los prova alguma coisa.

Medido no acervo em 2026-08-04: **2.054 pares comparados, 1.939 batem à centavo (94,4%),
115 divergem**. Nenhuma dessas divergências aparecia em lugar nenhum da plataforma.

## Por que os valores são calculados na hora e não guardados

O que muda com o tempo é o **dado** (uma retificação altera os dois lados) e o que não
muda é a **conclusão do analista** sobre a causa. Guardar o valor congelaria uma
divergência já resolvida por retificação; guardar a triagem preserva o trabalho humano.
Por isso esta camada calcula e a triagem (causa, decisão) fica para quem investiga.

## O que já se sabe sobre as causas

Duas foram investigadas até a raiz:

* **RCL zero.** 32 das 4.141 linhas de ``gold.fato_rcl`` têm ``rcl_12m = 0``, em 8 entes.
  Em ``2300150/2022-B4`` o ente simplesmente não entregou o Anexo 03 daquele bimestre — a
  lista pula de B3 para B5 — e a plataforma materializou **zero** em vez de não
  materializar. Ausência virando zero, gravada na gold, no denominador de quase todo
  limite da LRF. É defeito nosso, não do ente.
* **Divergência de escopo.** Casos como ``2307650/2023-Q1`` (+578%) têm valor estável na
  plataforma ao longo de quatro bimestres e valor pequeno no RGF, o que sugere publicação
  parcial do ente. Aqui a plataforma pode estar certa — e é por isso que a divergência é
  **mostrada**, não corrigida automaticamente.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.reconciliation.schemas import (
    DivergenciaItem,
    ReconciliacaoResultado,
    ReconciliacaoResumo,
)

#: Tolerância. Um centavo cobre o arredondamento do Decimal; acima disso é divergência de
#: verdade. Alargar isto seria esconder diferença por arredondamento — exatamente o que a
#: auditoria proíbe.
TOLERANCIA = Decimal("0.01")


@dataclass(frozen=True)
class Comparacao:
    """Uma reconciliação declarada: o que compara, contra o quê, e por qual metodologia."""

    codigo: str
    titulo: str
    indicador: str
    fonte_oficial: str
    metodologia: str
    sql: str


#: A RCL é o caso mais valioso: é o denominador de quase todo limite da LRF, e o ente
#: publica a dele. Divergir aqui contamina pessoal, dívida, investimento — tudo.
_RCL = Comparacao(
    codigo="rcl_rgf",
    titulo="RCL calculada × RCL publicada pelo ente",
    indicador="rcl_12m",
    fonte_oficial="RGF, Anexo 02 — linha 'RECEITA CORRENTE LÍQUIDA - RCL (IV)'",
    metodologia=(
        "A plataforma soma as receitas correntes dos 12 meses móveis do RREO Anexo 03 e "
        "subtrai as deduções. O ente publica, no RGF Anexo 02, a RCL que ele próprio "
        "apurou para o mesmo fecho. São dois caminhos independentes para o mesmo fato: a "
        "coincidência é evidência, a divergência é pergunta."
    ),
    sql="""
        with oficial as (
          select r.cod_ibge, r.periodo,
                 max(case when r.coluna like 'Até o 1%' then r.valor end) q1,
                 max(case when r.coluna like 'Até o 2%' then r.valor end) q2,
                 max(case when r.coluna like 'Até o 3%' then r.valor end) q3
          from silver.siconfi_rgf r
          where r.anexo like '%%02%%' and r.cod_conta = 'RGF2ReceitaCorrenteLiquida'
          group by 1, 2
        )
        select o.cod_ibge,
               o.periodo,
               case right(o.periodo, 1)
                 when '1' then o.q1 when '2' then o.q2 else o.q3
               end as valor_oficial,
               f.rcl_12m as valor_plataforma
        from oficial o
        join gold.fato_rcl f
          on f.cod_ibge = o.cod_ibge
         and f.periodo_ref = left(o.periodo, 5) || 'B'
             || (2 * cast(right(o.periodo, 1) as int))::text
        where o.periodo like '%%-Q%%'
          and o.cod_ibge = any(:entes)
    """,
)

COMPARACOES: dict[str, Comparacao] = {_RCL.codigo: _RCL}


def _causa_provavel(oficial: Decimal, plataforma: Decimal) -> str:
    """Classificação inicial, explícita quanto ao que ainda não sabe.

    Não é diagnóstico fechado: é a triagem que orienta a investigação. Fingir certeza aqui
    faria o analista pular justamente o caso que merece olhar.
    """
    if plataforma == 0:
        return (
            "A plataforma apurou zero. Indício de bimestre sem Anexo 03 entregue, em que a "
            "materialização gravou zero em vez de não gravar — defeito nosso, não do ente."
        )
    if oficial == 0:
        return "O ente publicou zero e a plataforma apurou valor. Verificar a entrega no RGF."
    razao = plataforma / oficial
    if razao > Decimal("1.5"):
        return (
            "A plataforma apurou bem mais que o publicado. Indício de publicação parcial no "
            "RGF (o ente pode ter informado só parte do período) — investigar antes de tratar "
            "como erro nosso."
        )
    if razao < Decimal("0.7"):
        return (
            "A plataforma apurou bem menos que o publicado. Indício de janela de 12 meses "
            "incompleta: faltam bimestres do RREO no acervo."
        )
    return "Divergência pequena. Verificar versão da entrega usada em cada lado (retificação)."


def build_reconciliacao(
    session: Session,
    *,
    codigo: str,
    entes: set[str],
    limite: int = 200,
) -> ReconciliacaoResultado:
    """Executa a comparação declarada dentro do escopo informado."""
    comp = COMPARACOES[codigo]
    if not entes:
        return ReconciliacaoResultado(
            codigo=comp.codigo,
            titulo=comp.titulo,
            fonte_oficial=comp.fonte_oficial,
            metodologia=comp.metodologia,
            resumo=ReconciliacaoResumo(pares=0, conferem=0, divergem=0, sem_par=0),
            divergencias=[],
        )

    linhas = session.execute(text(comp.sql), {"entes": sorted(entes)}).all()
    conferem = divergem = sem_par = 0
    itens: list[DivergenciaItem] = []
    for cod_ibge, periodo, oficial, plataforma in linhas:
        if oficial is None or plataforma is None:
            sem_par += 1
            continue
        of = Decimal(oficial)
        pl = Decimal(plataforma)
        diferenca = pl - of
        if abs(diferenca) <= TOLERANCIA:
            conferem += 1
            continue
        divergem += 1
        itens.append(
            DivergenciaItem(
                cod_ibge=cod_ibge,
                periodo=periodo,
                valor_plataforma=pl,
                valor_oficial=of,
                diferenca=diferenca,
                # Sem denominador não há percentual; devolver zero sugeriria "sem diferença".
                diferenca_pct=(diferenca / of * 100) if of else None,
                causa_provavel=_causa_provavel(of, pl),
            )
        )

    # As maiores primeiro: é onde a investigação rende mais por hora gasta.
    itens.sort(key=lambda i: abs(i.diferenca), reverse=True)
    return ReconciliacaoResultado(
        codigo=comp.codigo,
        titulo=comp.titulo,
        fonte_oficial=comp.fonte_oficial,
        metodologia=comp.metodologia,
        resumo=ReconciliacaoResumo(
            pares=len(linhas), conferem=conferem, divergem=divergem, sem_par=sem_par
        ),
        divergencias=itens[:limite],
        truncado=len(itens) > limite,
    )
