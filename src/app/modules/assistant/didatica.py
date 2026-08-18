"""Redação da resposta (Sprint IA-7): o que torna um texto fiscal legível por gente.

A tese da sprint é que **fidedignidade é sobre o número, não sobre o tom**. Este módulo é
o lado mensurável dessa tese: enquanto os guardrails G1–G7 garantem que o número está
certo, aqui se verifica — objetivamente, sem julgar estilo — se a resposta faz o mínimo
para ser entendida por auditor, gestor ou servidor que não é da área:

1. **Sigla expandida na primeira ocorrência.** "RCL" sozinho é opaco para metade de quem
   lê; "RCL (Receita Corrente Líquida)" custa seis palavras e resolve.
2. **Número acompanhado do que significa.** Um valor solto numa linha é um dado; um valor
   precedido do que ele mede é informação.
3. **Significado antes do primeiro número.** A queixa que originou a sprint ("respostas
   travadas") é exatamente esta: a resposta antiga abria no valor.

**Por que checar e não só pedir.** É a mesma lição do G6: instrução de prompt é pedido,
verificação é garantia — e sobrevive à troca de modelo. A diferença é o que se faz com o
laudo: número sem lastro é risco e vai sinalizado ao gestor; texto pouco didático é
qualidade, entra como métrica da avaliação (IA-6) e não mutila resposta nenhuma.

O expansor de siglas é usado pelo provedor local determinístico, que compõe texto a partir
do contexto e por isso pode expandir sem inventar nada: a expansão é uma tabela do
domínio, não conhecimento do modelo.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.shared.tooling.verificacao import NUMERO_FISCAL

#: Siglas do domínio fiscal e a forma por extenso. É a tabela do glossário do CLAUDE.md
#: (§2) mais as que aparecem em ``source_ref``. Deliberadamente pequena: sigla que não
#: está aqui não é cobrada, porque cobrar sem saber expandir viraria ruído.
SIGLAS: dict[str, str] = {
    "RCL": "Receita Corrente Líquida",
    "RREO": "Relatório Resumido da Execução Orçamentária",
    "RGF": "Relatório de Gestão Fiscal",
    "DCA": "Declaração de Contas Anuais",
    "MSC": "Matriz de Saldos Contábeis",
    "LRF": "Lei de Responsabilidade Fiscal",
    "PCASP": "Plano de Contas Aplicado ao Setor Público",
    "RPPS": "Regime Próprio de Previdência Social",
    "CAPAG": "Capacidade de Pagamento",
    "DCL": "Dívida Consolidada Líquida",
    "ARO": "Antecipação de Receita Orçamentária",
    "ASPS": "Ações e Serviços Públicos de Saúde",
    "MDE": "Manutenção e Desenvolvimento do Ensino",
    "FUNDEB": (
        "Fundo de Manutenção e Desenvolvimento da Educação Básica e de Valorização dos "
        "Profissionais da Educação"
    ),
    "RPNP": "Restos a Pagar Não Processados",
    "SICONFI": "Sistema de Informações Contábeis e Fiscais do Setor Público Brasileiro",
    "STN": "Secretaria do Tesouro Nacional",
    "MDF": "Manual de Demonstrativos Fiscais",
    "CF": "Constituição Federal",
}

#: Quantas palavras de conteúdo bastam para dizer que houve explicação antes do número.
#: Não é uma régua de estilo: é o piso abaixo do qual a resposta abriu no valor.
MIN_PALAVRAS_ANTES_DO_NUMERO = 10

#: Janela, em caracteres, na qual se procura o rótulo que dá sentido a um número.
_JANELA_ROTULO = 90

_PALAVRA = re.compile(r"[A-Za-zÀ-ÿ]{3,}")

# A contagem de palavras impede a abertura abrupta no valor, mas não prova que as
# palavras o expliquem. Estes marcadores deliberadamente pequenos reconhecem uma relação
# semântica explícita entre o indicador e o valor. A normalização abaixo evita duplicar
# cada variante acentuada.
_SIGNIFICADO = re.compile(
    r"\b(?:mede(?:m)?|indica(?:m)?|representa(?:m)?|corresponde(?:m)?|"
    r"expressa(?:m)?|mostra(?:m)?|reflete(?:m)?|serve(?:m)?|refere-se|"
    r"trata-se)\b"
)

# Depois de explicar e quantificar, a resposta precisa dizer por que aquilo importa ou
# qual é o próximo passo. A lista cobre consequência fiscal e ação operacional; não tenta
# classificar estilo nem sentimento.
_IMPLICACAO_OU_ACAO = re.compile(
    r"\b(?:implica|significa|situacao|faixa|limite|teto|piso|minimo|maximo|"
    r"denominador|atencao|risco|cumpri\w*|ultrapass\w*|dentro|acima|abaixo|"
    r"recomend\w*|providencia|acao|deve|pode|permite|exige|acompanhar|"
    r"monitorar|verificar|conferir|retificar|corrigir|regularizar|entregar|"
    r"inferir)\b"
)


def _normalizar(texto: str) -> str:
    decomposicao = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in decomposicao if not unicodedata.combining(c)).lower()


def _ocorrencias(texto: str, sigla: str) -> list[int]:
    """Posições da sigla como palavra inteira (``RCL`` não casa dentro de ``RCLX``)."""
    return [m.start() for m in re.finditer(rf"\b{re.escape(sigla)}\b", texto)]


def _ja_expandida(texto: str, sigla: str, posicao: int) -> bool:
    """A expansão acompanha a sigla — em qualquer das duas ordens usuais.

    ``RCL (Receita Corrente Líquida)`` e ``Receita Corrente Líquida (RCL)`` são a mesma
    cortesia com o leitor. Aceitar só a primeira reprovaria o texto mais natural.
    """
    extenso = SIGLAS[sigla]
    # A expressão pode trazer um qualificador entre o nome e a sigla, como
    # ``Receita Corrente Líquida por habitante (RCL por habitante)``. O limite antigo de
    # oito caracteres reprovava essa expansão correta. A margem continua local o bastante
    # para não aceitar uma definição perdida em outro parágrafo.
    margem_qualificador = 64
    depois = texto[posicao : posicao + len(sigla) + len(extenso) + margem_qualificador]
    if extenso.lower() in depois.lower():
        return True
    antes = texto[max(0, posicao - len(extenso) - margem_qualificador) : posicao]
    return extenso.lower() in antes.lower()


def siglas_sem_expansao(texto: str) -> list[str]:
    """Siglas usadas sem a forma por extenso na primeira ocorrência."""
    achadas: list[str] = []
    for sigla in SIGLAS:
        posicoes = _ocorrencias(texto or "", sigla)
        if posicoes and not _ja_expandida(texto, sigla, posicoes[0]):
            achadas.append(sigla)
    return achadas


def expandir_siglas(texto: str) -> str:
    """Expande a **primeira** ocorrência de cada sigla conhecida. Idempotente.

    Usado pelo provedor local determinístico: expandir é reescrever com a tabela do
    domínio, não acrescentar informação — nenhum número muda, nenhuma afirmação nova
    aparece. As ocorrências seguintes ficam como estão, que é como gente escreve.
    """
    saida = texto or ""
    for sigla, extenso in SIGLAS.items():
        posicoes = _ocorrencias(saida, sigla)
        if not posicoes or _ja_expandida(saida, sigla, posicoes[0]):
            continue
        inicio = posicoes[0]
        fim = inicio + len(sigla)
        saida = f"{saida[:inicio]}{sigla} ({extenso}){saida[fim:]}"
    return saida


#: A frase que o §9 exige em toda resposta. O avaliador a procura normalizada
#: ("nao constitui parecer juridico"), então a forma exata do acento não é contrato —
#: mas a presença é.
RESSALVA_NAO_PARECER = (
    "Esta resposta é informativa e fundamentada apenas nas fontes citadas; "
    "não constitui parecer jurídico ou contábil definitivo."
)

_MARCA_RESSALVA = "constitui parecer"


def fechar_resposta(texto: str) -> str:
    """Aplica, a QUALQUER provedor, as duas garantias que o §9 e a IA-7 exigem.

    Existe porque uma regra de prompt **pede** e um passo de pipeline **garante**. Estas
    duas viviam dentro do provedor local — e, como a avaliação só rodava nele, a lacuna do
    caminho Gemini ficou invisível até a primeira corrida paga: seis respostas escrevendo
    "RREO" sem expandir e três sem a ressalva do §9.

    Nenhuma das duas acrescenta informação nova: expandir sigla é reescrever com a tabela
    do domínio e a ressalva é frase fixa. Nenhuma toca em número — por isso podem rodar
    antes do G6 sem alterar o lastro que ele confere.

    Idempotente nas duas pontas: ``expandir_siglas`` já o é, e a ressalva só entra se o
    texto ainda não a carregar (o provedor local continua produzindo a sua).
    """
    fechado = expandir_siglas(texto)
    if _MARCA_RESSALVA not in _normalizar(fechado):
        separador = "\n\n" if fechado.strip() else ""
        fechado = f"{fechado.rstrip()}{separador}{RESSALVA_NAO_PARECER}"
    return fechado


def numeros_sem_rotulo(texto: str) -> list[str]:
    """Números fiscais que aparecem sem nada que diga o que eles são.

    A régua do que **é** número fiscal é a do G6 (``NUMERO_FISCAL``), de propósito: usar
    duas definições de número na mesma plataforma é garantir que elas divirjam. O que se
    exige aqui é contexto textual imediatamente antes do valor, na mesma linha — duas
    palavras de conteúdo bastam ("Pessoal do Executivo: 51,77%").
    """
    orfaos: list[str] = []
    for achado in NUMERO_FISCAL.finditer(texto or ""):
        inicio_linha = (texto or "").rfind("\n", 0, achado.start()) + 1
        janela = (texto or "")[max(inicio_linha, achado.start() - _JANELA_ROTULO) : achado.start()]
        if len(_PALAVRA.findall(janela)) < 2:
            orfaos.append(achado.group())
    return orfaos


def palavras_antes_do_primeiro_numero(texto: str) -> int:
    """Palavras de conteúdo antes do primeiro número fiscal. Sem número ⇒ tudo conta."""
    achado = NUMERO_FISCAL.search(texto or "")
    trecho = (texto or "")[: achado.start()] if achado else (texto or "")
    return len(_PALAVRA.findall(trecho))


@dataclass(frozen=True)
class Legibilidade:
    """Laudo objetivo de uma resposta. ``ok`` é o que a métrica agrega."""

    explica_antes_do_numero: bool
    tem_significado_antes_do_numero: bool
    tem_implicacao_ou_acao: bool
    palavras_antes: int
    siglas_sem_expansao: tuple[str, ...] = ()
    numeros_sem_rotulo: tuple[str, ...] = ()
    falhas: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.falhas


def avaliar(texto: str) -> Legibilidade:
    """Aplica as três verificações. Um texto sem número passa nas duas últimas por vacuidade.

    Isso é correto e não é frouxidão: a recusa honesta e a explicação puramente normativa
    não citam valor apurado, e cobrar-lhes rótulo de número seria reprovar exatamente o
    comportamento que a §9 exige.
    """
    conteudo = texto or ""
    siglas = tuple(siglas_sem_expansao(conteudo))
    orfaos = tuple(numeros_sem_rotulo(conteudo))
    palavras = palavras_antes_do_primeiro_numero(conteudo)
    primeiro_numero = NUMERO_FISCAL.search(conteudo)
    tem_numero = primeiro_numero is not None
    trecho_antes = conteudo[: primeiro_numero.start()] if primeiro_numero else conteudo
    tem_significado = (not tem_numero) or bool(_SIGNIFICADO.search(_normalizar(trecho_antes)))
    explica_antes = (not tem_numero) or (
        palavras >= MIN_PALAVRAS_ANTES_DO_NUMERO and tem_significado
    )
    tem_implicacao_ou_acao = bool(_IMPLICACAO_OU_ACAO.search(_normalizar(conteudo)))

    falhas: list[str] = []
    if not explica_antes:
        if palavras < MIN_PALAVRAS_ANTES_DO_NUMERO:
            falhas.append(
                f"A resposta abre no número: só {palavras} palavras antes do primeiro valor "
                f"(mínimo {MIN_PALAVRAS_ANTES_DO_NUMERO})."
            )
        if not tem_significado:
            falhas.append(
                "O texto antes do primeiro valor não explica o que o indicador mede ou "
                "representa."
            )
    if not tem_implicacao_ou_acao:
        falhas.append(
            "A resposta não explicita implicação prática nem ação ou providência possível."
        )
    if siglas:
        falhas.append(f"Siglas usadas sem expansão na primeira ocorrência: {', '.join(siglas)}.")
    if orfaos:
        falhas.append(f"Números sem rótulo que diga o que são: {', '.join(orfaos)}.")
    return Legibilidade(
        explica_antes_do_numero=explica_antes,
        tem_significado_antes_do_numero=tem_significado,
        tem_implicacao_ou_acao=tem_implicacao_ou_acao,
        palavras_antes=palavras,
        siglas_sem_expansao=siglas,
        numeros_sem_rotulo=orfaos,
        falhas=tuple(falhas),
    )
