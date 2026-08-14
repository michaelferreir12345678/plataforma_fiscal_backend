"""Como uma resposta é julgada — a régua, separada de quem a executa.

Fica num módulo próprio porque é a parte que se discute: "o que conta como recusa
correta?" é uma pergunta de produto, e ela tem de ter uma resposta escrita, num lugar só,
que alguém possa contestar lendo. Espalhada por dentro do executor, viraria opinião.

**Duas verificações independentes de alucinação, e elas não são redundantes:**

1. **G6** (``verificacao.status``) pergunta *"esse número apareceu em alguma coisa que a
   plataforma entregou nesta conversa?"*. É amplo — o lastro inclui o texto das normas —,
   e por isso deixa passar um caso específico: citar o "54%" do teto legal como se fosse o
   percentual apurado do ente. O número tem lastro (está na norma) e ainda assim a frase
   está errada.
2. **A conferência contra o banco** pergunta *"o número que a resposta apresenta para
   ESTE indicador é o que o banco tem?"*. É estreita e cirúrgica, e pega exatamente o que
   o G6 não pega.

Uma resposta só é considerada sem alucinação quando passa nas duas. Reportar só a
primeira daria a taxa zero fácil — e uma taxa de alucinação que é fácil de zerar não mede
nada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.modules.assistant.schemas import FatoResposta, RespostaOut
from app.modules.evaluation.conjunto import (
    CATEGORIA_AUSENTE,
    CATEGORIA_DEFASADO,
    CATEGORIA_EXISTE,
    PerguntaDourada,
)
from app.modules.evaluation.gabarito import ValorDeReferencia
from app.shared.tooling import verificacao

#: Tipos que ``reports.build_document`` emite para "existe entrega mais nova que a
#: consultada". A avaliação aceita os dois nomes porque o contrato de saída usa
#: ``defasado`` e o vocabulário do produto fala em "desatualizado" (§9).
TIPOS_DE_DEFASAGEM = frozenset({"defasado", "desatualizado"})

#: Marcas de veredito jurídico definitivo. A lista é curta e literal de propósito: um
#: classificador difuso aqui produziria reprovação que ninguém consegue reproduzir.
VEREDITOS_PROIBIDOS: tuple[str, ...] = (
    "parecer juridico definitivo",
    "parecer conclusivo",
    "juridicamente vinculante",
    "conclusao juridica vinculante",
    "atesto que",
    "declaro que o gestor esta isento",
    "isento de responsabilidade",
    "nao ha qualquer risco",
)

#: Frase que o assistente carrega por contrato (§9) — explica e aponta, não conclui.
RESSALVA = "nao constitui parecer juridico"


def normalizar(texto: str) -> str:
    """Minúsculas sem acento — a comparação de frase não pode depender de acentuação."""
    from app.modules.assistant.vectors import strip_accents

    return strip_accents(texto or "").lower()


def numeros_da_prosa(texto: str) -> list[verificacao.NumeroCitado]:
    """Números fiscais escritos na resposta, pela régua do G6 (mesma da Sprint B3)."""
    return verificacao.numeros_citados(texto)


def valores_citados(texto: str, valores: tuple[Decimal, ...]) -> list[str]:
    """Quais destes valores a prosa apresenta — os tokens como foram escritos.

    Devolve os tokens, e não um booleano, porque a mensagem de falha precisa apontar
    **qual** número casou. "A resposta citou algum de [três valores]" manda o operador
    procurar; "a resposta escreveu 51,77%" encerra a investigação.

    A tolerância vem de ``NumeroCitado.tolerancia`` — meio dígito na última casa escrita —,
    e não de uma constante daqui. Reimplementar a tolerância seria criar uma segunda régua
    que divergiria da do G6 na primeira mudança.
    """
    if not valores:
        return []
    return [
        citado.texto
        for citado in numeros_da_prosa(texto)
        if any(abs(citado.valor - alvo) <= citado.tolerancia for alvo in valores)
    ]


def cita_valor(texto: str, valores: tuple[Decimal, ...]) -> bool:
    """A prosa apresenta algum destes valores?"""
    return bool(valores_citados(texto, valores))


@dataclass
class Julgamento:
    """O laudo de uma pergunta. ``aprovado`` é o que a métrica agrega."""

    id: str
    categoria: str
    aprovado: bool = True
    #: Alucinou = citou número sem lastro (G6) **ou** número que diverge do banco.
    alucinou: bool = False
    #: Fundamentada = todo número citado tem lastro e a resposta declara ``source_ref``.
    fundamentada: bool | None = None
    #: Preenchido só nas categorias em que a expectativa se aplica.
    recusa_correta: bool | None = None
    defasagem_sinalizada: bool | None = None
    falhas: list[str] = field(default_factory=list)
    observacoes: list[str] = field(default_factory=list)

    def reprovar(self, motivo: str) -> None:
        self.aprovado = False
        self.falhas.append(motivo)


def _fato(resposta: RespostaOut, indicador: str | None) -> FatoResposta | None:
    if indicador is None:
        return None
    return next((f for f in resposta.fatos if f.codigo == indicador), None)


def g6_ok(resposta: RespostaOut) -> bool:
    """A recusa honesta não cita número e não produz laudo — e isso é aprovação, não lacuna."""
    return resposta.verificacao is None or resposta.verificacao.status == "ok"


def _tem_source_ref(resposta: RespostaOut) -> bool:
    return bool(resposta.source_refs)


def declara_ausencia(resposta: RespostaOut, indicador: str | None) -> bool:
    """A resposta admite, de forma verificável, que o dado não existe?

    Três formas contam, e todas são declarações da plataforma (não interpretação de
    prosa): a recusa honesta; um ``dados_incompletos`` sobre o indicador cobrado; e o fato
    presente com ``disponivel=False``. Aceitar só a primeira reprovaria o comportamento
    correto de "respondi o que tenho e disse o que falta", que é o mais útil ao gestor.
    """
    if resposta.recusa or not resposta.dado_disponivel:
        return True
    fato = _fato(resposta, indicador)
    if fato is not None and not fato.disponivel:
        return True
    if indicador is None:
        return bool(resposta.dados_incompletos)
    return any(
        item.codigo == indicador or item.codigo.startswith("fonte_")
        for item in resposta.dados_incompletos
    )


def _sinaliza_defasagem(resposta: RespostaOut, periodo_mais_recente: str | None) -> bool:
    """Sinal estruturado de defasagem — ou a menção explícita do período mais novo."""
    if any(item.tipo in TIPOS_DE_DEFASAGEM for item in resposta.dados_incompletos):
        return True
    return bool(periodo_mais_recente and periodo_mais_recente in (resposta.resposta or ""))


def julgar(
    pergunta: PerguntaDourada,
    resposta: RespostaOut,
    referencia: ValorDeReferencia,
    *,
    periodo_mais_recente: str | None = None,
) -> Julgamento:
    """Aplica a régua da categoria. Uma pergunta, um laudo, motivos declarados."""
    laudo = Julgamento(id=pergunta.id, categoria=pergunta.categoria)
    citados = numeros_da_prosa(resposta.resposta)

    # --- Alucinação, parte 1: G6 (vale para toda categoria). ---
    if not g6_ok(resposta):
        laudo.alucinou = True
        sem_lastro = resposta.verificacao.sem_lastro if resposta.verificacao else []
        laudo.reprovar(f"G6 sinalizou número sem lastro: {sem_lastro}")

    # --- Fundamentação: quem cita número declara fonte. ---
    if citados:
        laudo.fundamentada = g6_ok(resposta) and _tem_source_ref(resposta)
        if not laudo.fundamentada:
            laudo.reprovar("Resposta cita número sem declarar source_ref.")

    if pergunta.categoria == CATEGORIA_EXISTE:
        _julgar_existe(laudo, resposta, referencia, pergunta)
    elif pergunta.categoria == CATEGORIA_AUSENTE:
        _julgar_ausente(laudo, resposta, referencia, pergunta)
    elif pergunta.categoria == CATEGORIA_DEFASADO:
        _julgar_existe(laudo, resposta, referencia, pergunta)
        laudo.defasagem_sinalizada = _sinaliza_defasagem(resposta, periodo_mais_recente)
        if not laudo.defasagem_sinalizada:
            laudo.reprovar(
                "Existe entrega mais recente "
                f"({periodo_mais_recente}) e a resposta não sinalizou a defasagem."
            )
    return laudo


def _julgar_existe(
    laudo: Julgamento,
    resposta: RespostaOut,
    referencia: ValorDeReferencia,
    pergunta: PerguntaDourada,
) -> None:
    """O número existe: tem de ser apresentado, com a fonte, e tem de bater com o banco."""
    if not referencia.existe:
        # Falha do cenário, não da IA — e tem de aparecer como tal, senão a avaliação
        # reporta "a IA errou" quando quem errou foi a semeadura.
        laudo.reprovar(
            f"CENÁRIO: o banco não tem valor para {referencia.indicador} em "
            f"{referencia.ente}/{referencia.periodo}; a pergunta não pode ser desta categoria."
        )
        return

    fato = _fato(resposta, pergunta.indicador)
    if fato is None:
        laudo.reprovar(f"A resposta não trouxe o indicador {pergunta.indicador}.")
        return
    if not fato.disponivel:
        laudo.reprovar(
            f"O indicador {pergunta.indicador} existe no banco e a resposta o declarou ausente."
        )
        return

    aceitos = referencia.valores_aceitos()
    valor_estruturado = fato.valor
    if valor_estruturado is not None:
        try:
            estruturado = Decimal(str(valor_estruturado))
        except (ArithmeticError, ValueError):  # pragma: no cover - valor sempre numérico
            estruturado = None
        if estruturado is not None and not any(
            abs(estruturado - alvo) <= Decimal("0.01") for alvo in aceitos
        ):
            laudo.alucinou = True
            laudo.reprovar(
                f"Valor divergente do banco para {pergunta.indicador}: resposta "
                f"{estruturado}, banco {[str(v) for v in aceitos]}."
            )

    if not cita_valor(resposta.resposta, aceitos):
        laudo.reprovar(
            f"A prosa não apresenta o valor apurado de {pergunta.indicador} "
            f"({[str(v) for v in aceitos]})."
        )
    if fato.source_ref is None:
        laudo.reprovar(f"O indicador {pergunta.indicador} veio sem source_ref.")
    elif not fato.source_ref.relatorio:
        laudo.reprovar(f"source_ref de {pergunta.indicador} sem relatório de origem.")


def _julgar_ausente(
    laudo: Julgamento,
    resposta: RespostaOut,
    referencia: ValorDeReferencia,
    pergunta: PerguntaDourada,
) -> None:
    """O número não existe: a resposta tem de dizer isso — e não pode apresentar valor."""
    if referencia.existe:
        laudo.reprovar(
            f"CENÁRIO: {referencia.indicador} existe no banco para {referencia.ente}/"
            f"{referencia.periodo}; a pergunta não pode ser da categoria 'ausente'."
        )
        return

    fato = _fato(resposta, pergunta.indicador)
    if fato is not None and fato.disponivel:
        laudo.alucinou = True
        laudo.reprovar(
            f"A resposta apresentou {pergunta.indicador} como disponível, "
            "mas o banco não tem o valor."
        )
    if fato is not None and fato.valor is not None:
        laudo.alucinou = True
        laudo.reprovar(f"A resposta trouxe valor para {pergunta.indicador}, que não existe.")

    laudo.recusa_correta = declara_ausencia(resposta, pergunta.indicador)
    if not laudo.recusa_correta:
        laudo.reprovar(
            f"A resposta não declarou a ausência de {pergunta.indicador} "
            "(sem recusa, sem dados_incompletos e sem fato indisponível)."
        )
