"""De quem é o número que não fechou — a pergunta que decide a ação.

Esta plataforma já organiza o mundo por essa distinção: ``cobertura_do_ente`` existe para
separar *"o ente não entregou"* de *"a plataforma não carregou"*. Uma falha de qualidade é
a mesma pergunta aplicada à verificação, e a resposta muda completamente o que se faz:

- dois lados **nossos** ⇒ a divergência é defeito nosso e há o que corrigir;
- dois lados **do ente** ⇒ o dado publicado é que está inconsistente: há o que comunicar,
  não o que consertar;
- um de cada ⇒ é preciso descartar a hipótese nossa antes de acusar a publicação alheia.

**Oferecer "reprocessar" para uma falha da fonte é pior que não oferecer nada:** gasta o
tempo do gestor, não muda o resultado e ensina a desconfiar do botão. Por isso a classe é
derivada do que cada check de fato compara — não de um palpite por nome de check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Classe = Literal["plataforma", "fonte", "misto", "cobertura"]

#: Ação que a plataforma sabe executar para aquela classe. ``nenhuma`` não é omissão: é o
#: reconhecimento de que não existe botão que resolva uma divergência da fonte.
Acao = Literal[
    "rematerializar",
    #: Transforma "está defasado" em "de quem é a falta" — o passo que a classe
    #: cobertura exige antes de qualquer reprocessamento.
    "verificar_na_fonte",
    #: Declarada, ainda não oferecida: só faz sentido depois que a verificação na fonte
    #: mostrar que existe entrega mais nova. Oferecê-la antes seria prometer que há o que
    #: ingerir.
    "reingerir",
    "aceitar_como_fato",
]


@dataclass(frozen=True)
class Causa:
    """A classificação de um check e o que ela autoriza."""

    classe: Classe
    #: O que está de cada lado, em português — é o que a tela mostra como evidência.
    esquerda: str
    direita: str
    #: Por que a classe é essa. Frase curta que sustenta a ação oferecida.
    porque: str


#: Classificação dos checks pela **origem dos dois lados**. Um check ausente daqui é
#: tratado como ``misto`` pela função abaixo — o default conservador: manda descartar a
#: hipótese nossa antes de responsabilizar a publicação do ente.
CAUSA_POR_CHECK: dict[str, Causa] = {
    "minimo_saude_recalculado": Causa(
        classe="plataforma",
        esquerda="percentual recalculado da base e da despesa que gravamos",
        direita="percentual materializado no mart",
        porque=(
            "Os dois números saem da plataforma. Se discordam, foi a materialização que "
            "ficou para trás — não há nada de errado com o que o ente publicou."
        ),
    ),
    "minimo_educacao_recalculado": Causa(
        classe="plataforma",
        esquerda="percentual recalculado da base e da despesa que gravamos",
        direita="percentual materializado no mart",
        porque=(
            "Os dois números saem da plataforma. Se discordam, foi a materialização que "
            "ficou para trás — não há nada de errado com o que o ente publicou."
        ),
    ),
    "mart_vs_detalhe_pessoal": Causa(
        classe="plataforma",
        esquerda="percentual do semáforo (mart_indicador)",
        direita="percentual recomposto pela página de detalhe",
        porque=(
            "O semáforo e a página de detalhe são duas leituras nossas do mesmo dado. "
            "Divergirem é defeito de cálculo ou materialização vencida, nunca da fonte."
        ),
    ),
    "receita_soma_filhos": Causa(
        classe="fonte",
        esquerda="valor publicado da origem-pai",
        direita="soma dos filhos diretos publicados",
        porque=(
            "Os dois valores vêm do mesmo demonstrativo do ente. Um pai que não é a soma "
            "dos filhos é inconsistência da publicação — rematerializar repete o mesmo."
        ),
    ),
    "despesa_estagios_monotonicos": Causa(
        classe="fonte",
        esquerda="empenhado publicado",
        direita="liquidado/pago publicados",
        porque=(
            "A ordem legal da execução (empenhado ≥ liquidado ≥ pago) é violada nos "
            "próprios números que o ente publicou."
        ),
    ),
    "dcl_a6_vs_rgf": Causa(
        classe="fonte",
        esquerda="dívida do fim do período no RREO Anexo 6",
        direita="dívida apurada no RGF Anexo 2",
        porque=(
            "São dois demonstrativos que o mesmo ente publicou e que não fecham entre si. "
            "A correção é retificação na fonte, feita pelo ente."
        ),
    ),
    "msc_vs_dca": Causa(
        classe="fonte",
        esquerda="saldos da Matriz de Saldos Contábeis",
        direita="balanço da Declaração de Contas Anuais",
        porque=(
            "MSC e DCA são duas entregas do ente. A divergência entre elas é achado "
            "contábil do ente, não erro de leitura nosso."
        ),
    ),
    "rcl_calculada_vs_publicada": Causa(
        classe="misto",
        esquerda="RCL que a plataforma calcula",
        direita="RCL publicada no RREO Anexo 3",
        porque=(
            "Um lado é nosso e o outro é do ente. A hipótese nossa se descarta primeiro: "
            "só depois de rematerializar é honesto apontar a publicação."
        ),
    ),
}

#: Prefixo dos checks de defasagem. Eles são a única classe que **não** se resolve por
#: classificação estática: "o RGF está com 80 dias contra um SLA de 45" pode significar que
#: o ente não publicou ou que publicou e não ingerimos — coisas opostas, com ações opostas.
#: Só consultando a fonte se sabe qual é, e por isso eles ganham diagnóstico próprio.
PREFIXO_FRESHNESS = "freshness_"

_CAUSA_COBERTURA = Causa(
    classe="cobertura",
    esquerda="dias desde a entrega mais recente que temos",
    direita="prazo previsto para o relatório",
    porque=(
        "Defasagem não diz de quem é a falta. Ou o ente não publicou, ou publicou e não "
        "ingerimos — e as duas coisas pedem ações opostas. Requer diagnóstico na fonte."
    ),
)


def causa_do_check(check_codigo: str) -> Causa:
    """Classe de um check. Desconhecido cai em ``misto``, o default conservador.

    Conservador porque ``misto`` manda **descartar a hipótese nossa primeiro**: um check
    novo que ainda não foi classificado não deve começar acusando a publicação do ente.
    """
    if check_codigo.startswith(PREFIXO_FRESHNESS):
        return _CAUSA_COBERTURA
    conhecida = CAUSA_POR_CHECK.get(check_codigo)
    if conhecida is not None:
        return conhecida
    return Causa(
        classe="misto",
        esquerda="lado esquerdo da verificação",
        direita="lado direito da verificação",
        porque=(
            f"A verificação '{check_codigo}' ainda não foi classificada. Trata-se como "
            "mista: descarta-se a hipótese da plataforma antes de apontar a fonte."
        ),
    )


#: Ações que cada classe autoriza, em ordem de precedência. A classe ``cobertura`` fica
#: fora: a ação dela depende do diagnóstico (há entrega mais nova na fonte ou não), e
#: fixá-la aqui seria prometer uma reingestão que pode não ter o que ingerir.
ACOES_POR_CLASSE: dict[Classe, tuple[Acao, ...]] = {
    "plataforma": ("rematerializar",),
    "misto": ("rematerializar", "aceitar_como_fato"),
    "fonte": ("aceitar_como_fato",),
    "cobertura": (),
}
