"""Premissas de cenário ancoradas na **observação real**, não em número de fábrica.

A tela de cenários abria com IPCA 4,5% e Selic 10,5% escritos no código do frontend. Não
eram premissas do gestor nem projeções de mercado: eram dois números que alguém digitou
uma vez e que a tela apresentava com a mesma aparência de qualquer valor informado. Quem
aceitasse o padrão — que é o que quase todo mundo faz — estaria simulando sobre uma
suposição alheia sem saber que era uma suposição.

O acervo já tem as séries reais (``silver.bcb_indice``: IPCA 433, Selic 4390) e o FPM do
próprio ente. Este módulo as lê e devolve **o que foi observado**, com a data e a fonte, de
modo que a tela possa abrir dizendo de onde parte. O gestor continua livre para alterar —
a diferença é que agora ele sabe o que está alterando.

## Ausência não vira número

Se a série não existe para o período, a premissa volta com ``observado=None`` e um motivo.
Nenhum valor de fábrica ocupa o lugar: uma premissa inventada é indistinguível de uma
premissa medida depois que entra no formulário, e o cenário inteiro herda essa mentira.

## A conversão anual → mensal é composta

Um cenário com Selic de 12% ao ano **não** é 1% ao mês: é ``(1,12)^(1/12) − 1 = 0,9489%``.
A divisão linear por 12 superestima a taxa e, ao longo do horizonte, o erro compõe. O
código anterior dividia por 12 — para a Selic de 12% a.a., 5,4% de erro na premissa antes
mesmo de o modelo rodar. :func:`anual_para_mensal` faz a conversão correta, e
:func:`mensal_para_anual` a volta, que é o que a tela precisa para exibir o observado no
vocabulário em que o gestor pensa (taxa anual).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.ingestion.models import BcbIndice, TesouroFpm

#: Séries SGS/BCB usadas como premissa macro. Os valores são **variações mensais em %**.
SERIE_IPCA = 433
SERIE_SELIC = 4390

#: Janela de acumulação. Doze meses é o vocabulário em que IPCA e Selic são discutidos —
#: "IPCA de 4,5%" quer dizer acumulado em 12 meses, nunca a variação de um mês.
MESES_ACUMULACAO = 12

MOTIVO_SEM_SERIE = "A série não está no acervo para este período."
MOTIVO_SERIE_CURTA = (
    f"A série tem menos de {MESES_ACUMULACAO} meses — não dá para acumular 12 meses "
    "sem completar o que falta com suposição."
)
MOTIVO_SEM_FPM = "O ente não tem histórico de FPM no acervo."


def anual_para_mensal(taxa_anual_pct: float | Decimal) -> Decimal:
    """``12`` (% a.a.) → ``0.9489`` (% a.m.), por capitalização composta.

    Dividir por 12 é a aproximação que parece inofensiva e não é: para 12% a.a. devolve
    1,0% em vez de 0,9489% — 5,4% a mais na premissa, composto a cada passo do horizonte.
    """
    fator = Decimal(1) + Decimal(str(taxa_anual_pct)) / Decimal(100)
    if fator <= 0:
        # Deflação de 100% ou mais não tem raiz real com significado econômico; devolver a
        # divisão linear aqui seria inventar. O chamador recebe zero e a memória registra.
        return Decimal(0)
    try:
        mensal = fator ** (Decimal(1) / Decimal(MESES_ACUMULACAO)) - Decimal(1)
    except (InvalidOperation, OverflowError):
        return Decimal(0)
    return mensal * Decimal(100)


def mensal_para_anual(taxas_mensais_pct: list[Decimal]) -> Decimal | None:
    """Acumula variações mensais em % pelo produto — não pela soma.

    Somar 12 variações mensais subestima o acumulado (ignora o juro sobre juro do próprio
    índice). Com IPCA de 0,5% ao mês, a soma dá 6,0% e o acumulado real é 6,17%.
    """
    if not taxas_mensais_pct:
        return None
    acumulado = Decimal(1)
    for v in taxas_mensais_pct:
        acumulado *= Decimal(1) + v / Decimal(100)
    return (acumulado - Decimal(1)) * Decimal(100)


@dataclass(frozen=True)
class Premissa:
    """Uma variável de cenário, com o que se observou e de onde veio.

    ``observado`` é ``None`` quando a série não sustenta o cálculo. Nesse caso ``motivo``
    diz o porquê e a tela pede o valor em vez de sugerir um.
    """

    chave: str
    rotulo: str
    unidade: str
    observado: Decimal | None
    motivo: str | None = None
    #: Último período com observação — é o que autoriza o gestor a confiar (ou não).
    referencia: str | None = None
    fonte: str | None = None
    #: Quantas observações entraram no acumulado, para que "12 meses" seja verificável.
    n_observacoes: int | None = None


def _serie_bcb(session: Session, codigo: int, limite: int) -> list[tuple[date, Decimal]]:
    linhas = session.execute(
        select(BcbIndice.data_ref, BcbIndice.valor)
        .where(BcbIndice.codigo_serie == codigo, BcbIndice.valor.is_not(None))
        .order_by(BcbIndice.data_ref.desc())
        .limit(limite)
    ).all()
    return [(d, Decimal(v)) for d, v in linhas if v is not None]


def _premissa_bcb(
    session: Session, *, chave: str, rotulo: str, codigo: int, fonte: str
) -> Premissa:
    pontos = _serie_bcb(session, codigo, MESES_ACUMULACAO)
    if not pontos:
        return Premissa(chave, rotulo, "%_aa", None, MOTIVO_SEM_SERIE, fonte=fonte)
    if len(pontos) < MESES_ACUMULACAO:
        # Acumular 7 meses e chamar de "12 meses" seria o erro mais fácil de cometer aqui,
        # e o mais difícil de perceber depois — o número sai plausível.
        return Premissa(
            chave,
            rotulo,
            "%_aa",
            None,
            MOTIVO_SERIE_CURTA,
            referencia=pontos[0][0].isoformat(),
            fonte=fonte,
            n_observacoes=len(pontos),
        )
    acumulado = mensal_para_anual([v for _, v in reversed(pontos)])
    return Premissa(
        chave,
        rotulo,
        "%_aa",
        acumulado,
        referencia=pontos[0][0].isoformat(),
        fonte=fonte,
        n_observacoes=len(pontos),
    )


def _premissa_fpm(session: Session, cod_ibge: str) -> Premissa:
    """Variação do FPM do ente: último exercício fechado contra o anterior.

    A premissa oferecida ao gestor é uma **variação**, não um nível — é assim que a
    pergunta se formula ("e se o FPM cair 5%?"). Comparar exercícios fechados evita
    contrapor um ano completo a um ano em curso, que produziria uma queda inexistente.
    """
    def _sem_fpm(motivo: str) -> Premissa:
        return Premissa(
            "fpm_variacao_pct",
            "Variação do FPM",
            "%",
            None,
            motivo,
            fonte="Tesouro Nacional · FPM",
        )

    # **Só entram exercícios com os 12 meses.** Comparar um ano completo com um ano em
    # curso produziria uma queda que não existe — e ela apareceria na tela como premissa
    # observada, que é a pior espécie de número errado: um que se apresenta como medido.
    completos = [
        ano
        for (ano,) in session.execute(
            select(TesouroFpm.ano)
            .where(TesouroFpm.cod_ibge == cod_ibge)
            .group_by(TesouroFpm.ano)
            .having(func.count(func.distinct(TesouroFpm.mes)) == MESES_ACUMULACAO)
        ).all()
        if ano is not None
    ]
    completos.sort()
    if len(completos) < 2:
        return _sem_fpm(
            "O ente não tem dois exercícios completos de FPM no acervo para comparar."
        )
    ano_recente, ano_anterior = completos[-1], completos[-2]

    def total(ano: int) -> Decimal | None:
        # **Uma versão de entrega por vez.** Somar todas dobraria o total dos entes com
        # carga repetida (achado A14) e a premissa nasceria com o dobro do FPM real.
        versao = session.scalar(
            select(TesouroFpm.versao_entrega)
            .where(TesouroFpm.cod_ibge == cod_ibge, TesouroFpm.ano == ano)
            .order_by(TesouroFpm.versao_entrega.desc())
            .limit(1)
        )
        if versao is None:
            return None
        linhas = session.scalars(
            select(TesouroFpm.valor_liquido).where(
                TesouroFpm.cod_ibge == cod_ibge,
                TesouroFpm.ano == ano,
                TesouroFpm.versao_entrega == versao,
            )
        ).all()
        valores = [Decimal(v) for v in linhas if v is not None]
        return sum(valores, Decimal(0)) if valores else None

    recente, anterior = total(ano_recente), total(ano_anterior)
    if recente is None or anterior is None or anterior == 0:
        return _sem_fpm(MOTIVO_SEM_FPM)
    variacao = (recente - anterior) / anterior * Decimal(100)
    return Premissa(
        "fpm_variacao_pct",
        "Variação do FPM",
        "%",
        variacao,
        referencia=f"{ano_anterior}→{ano_recente}",
        fonte="Tesouro Nacional · FPM",
    )


def observadas(session: Session, cod_ibge: str) -> list[Premissa]:
    """As premissas de cenário como o mundo as reporta, para a tela abrir ancorada."""
    return [
        _premissa_bcb(
            session,
            chave="ipca_aa_pct",
            rotulo="IPCA acumulado em 12 meses",
            codigo=SERIE_IPCA,
            fonte="BCB/SGS série 433",
        ),
        _premissa_bcb(
            session,
            chave="selic_aa_pct",
            rotulo="Selic acumulada em 12 meses",
            codigo=SERIE_SELIC,
            fonte="BCB/SGS série 4390",
        ),
        _premissa_fpm(session, cod_ibge),
    ]
