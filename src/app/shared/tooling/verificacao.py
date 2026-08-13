"""G6 — verificação de saída: número na prosa tem de ter lastro (Sprint IA-3).

Os guardrails anteriores agem **antes** da prosa existir: a ferramenta devolve valor
tipado com ``source_ref`` (G1/G4), o escopo é conferido dentro dela (G2), a recusa honesta
evita chamar o modelo sem fundamento (G3). Nenhum deles impede o passo final — o modelo
escrever um número que nenhuma ferramenta devolveu. É o modo de falha mais caro num
sistema fiscal, porque a prosa é justamente a parte que o gestor lê.

**Por que verificação e não instrução.** O ``SYSTEM_PROMPT`` já manda não inventar número,
e essa é a defesa certa para orientar o modelo — mas é um pedido. Este módulo é a garantia:
casa cada número citado contra os valores que as ferramentas e o contexto daquela conversa
realmente devolveram, e o que não tiver lastro é **sinalizado**. Um pedido não sobrevive à
troca de modelo; uma verificação sim (§5, G6 do plano de MCP).

**A régua é a mesma da Sprint B3** (``RespostaMarkdown.tsx``), de propósito. A tela já
ancorava número em fato com casamento tolerante — porque o modelo parafraseia o mesmo valor
(menos casas, sem separador de milhar). Divergir aqui produziria o pior dos mundos: a tela
ancorando um número que o servidor considera órfão, ou o contrário. Duas regras iguais
escritas duas vezes divergem; esta foi copiada com a semântica declarada campo a campo:

- **o que conta como número fiscal**: agrupamento de milhar (``12.345.678``), decimal com
  vírgula (``54,2``) ou percentual (``54%``). Inteiro solto (``42``, ``2024``) **não** conta
  — artigo de lei, ano e número de anexo não são valores apurados, e casá-los encheria a
  resposta de falsos positivos até o sinal virar ruído;
- **tolerância**: meio dígito na última casa escrita (``0,5 × 10⁻ⁿ``), ou 0,5 quando o token
  é inteiro. Cobre arredondamento e supressão de separador sem abrir para um número de
  magnitude parecida que seja realmente outro.

**O que é lastro.** Tudo que a plataforma pôs no contexto daquela conversa: os payloads das
ferramentas chamadas, os fatos já calculados, o texto dos dispositivos normativos (é de lá
que sai o "54%" do teto) e os verbetes do dicionário. Restringir lastro só a payload de
ferramenta reprovaria toda citação de limite legal — o guardrail existe para pegar número
inventado, não para proibir o modelo de repetir a norma que recebeu.

**Sinalizar, não censurar.** A saída deste módulo é um laudo estruturado, e o serviço
decide o que fazer com ele. Apagar o número seria pior: some a evidência de que o modelo
errou, e o gestor lê uma frase mutilada sem saber por quê.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

#: Número fiscal solto no texto. **Idêntica** a ``NUMERO_FISCAL_SOURCE`` da Sprint B3
#: (``RespostaMarkdown.tsx``) — ver a docstring do módulo sobre por que um inteiro solto
#: fica deliberadamente de fora.
NUMERO_FISCAL = re.compile(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?%?|-?\d+,\d+%?|-?\d+%")

#: Número em qualquer notação, para **colher lastro** (não para achar citação). Aqui o
#: inteiro solto entra: ``periodo: 2024`` e ``teto_pct: 54`` são lastro legítimo do que o
#: modelo pode citar, mesmo que sozinhos não fossem considerados citação.
_NUMERO_QUALQUER = re.compile(r"-?\d[\d.,]*")

#: Teto de valores colhidos por conversa. Um payload de série histórica traz centenas de
#: números; sem teto, uma conversa longa transformaria a verificação num conjunto grande o
#: bastante para casar quase qualquer coisa por coincidência — o guardrail se anularia.
_MAX_LASTRO = 5000


@dataclass(frozen=True)
class NumeroCitado:
    """Um número encontrado na prosa, já normalizado."""

    texto: str
    valor: Decimal
    #: Tolerância derivada das casas decimais escritas (regra da B3).
    tolerancia: Decimal


@dataclass(frozen=True)
class Verificacao:
    """Laudo de G6 para uma resposta. ``ok`` é o que o serviço consulta."""

    total_citados: int
    com_lastro: int
    sem_lastro: tuple[NumeroCitado, ...]

    @property
    def ok(self) -> bool:
        """True quando todo número citado casou com algum valor entregue pela plataforma."""
        return not self.sem_lastro

    @property
    def status(self) -> str:
        return "ok" if self.ok else "sinalizado"

    def tokens_sem_lastro(self) -> list[str]:
        return [n.texto for n in self.sem_lastro]

    def aviso(self) -> str | None:
        """Frase declarada para acompanhar a resposta. ``None`` quando não há o que avisar.

        O texto é dirigido ao gestor, não ao operador: ele precisa saber **quais** números
        não se sustentam nas fontes citadas, para não agir sobre eles.
        """
        if self.ok:
            return None
        tokens = ", ".join(dict.fromkeys(self.tokens_sem_lastro()))
        return (
            "⚠️ Verificação automática (G6): os valores a seguir aparecem no texto acima "
            f"mas não correspondem a nenhum número devolvido pelas fontes desta consulta: "
            f"{tokens}. Não os utilize para decidir — confira o indicador na tela "
            "correspondente, onde cada número traz a sua fonte."
        )


def _para_decimal(bruto: str) -> Decimal | None:
    """Converte texto numérico em ``Decimal``, aceitando pt-BR e notação de máquina.

    Os dois convivem no mesmo payload: ``valor_formatado`` chega em pt-BR (``"54,32%"``) e
    ``valor_rs`` chega como string de ``Decimal`` serializado (``"1234.56"``), porque o
    envelope serializa com ``mode='json'``. Ler só um dos formatos deixaria metade do
    lastro invisível — e metade do lastro invisível é falso positivo garantido.
    """
    limpo = re.sub(r"[^\d,.\-]", "", bruto)
    if not limpo or limpo in {"-", ".", ","}:
        return None
    if "," in limpo:
        # pt-BR: ponto é milhar, vírgula é decimal.
        normalizado = limpo.replace(".", "").replace(",", ".")
    else:
        # Ponto seguido de exatamente 3 dígitos até o fim/próximo separador é milhar
        # (``1.234``); qualquer outro ponto é decimal de máquina (``1234.56``).
        normalizado = re.sub(r"\.(?=\d{3}(?:[.,]|$))", "", limpo)
    try:
        return Decimal(normalizado)
    except (InvalidOperation, ValueError):
        return None


def _tolerancia(token: str) -> Decimal:
    """Meio dígito na última casa escrita — a regra da B3, sem reinterpretação."""
    sem_pct = token.replace("%", "")
    casas = len(sem_pct.split(",")[1]) if "," in sem_pct else 0
    return Decimal("0.5") * (Decimal(10) ** -casas)


def numeros_citados(texto: str) -> list[NumeroCitado]:
    """Números fiscais escritos na prosa, na ordem em que aparecem."""
    achados: list[NumeroCitado] = []
    for bruto in NUMERO_FISCAL.findall(texto or ""):
        valor = _para_decimal(bruto)
        if valor is None:
            continue
        achados.append(
            NumeroCitado(texto=bruto, valor=valor, tolerancia=_tolerancia(bruto))
        )
    return achados


def _colher(valor: Any, destino: set[Decimal], profundidade: int = 0) -> None:
    """Percorre um payload recursivamente acumulando todo número alcançável."""
    if len(destino) >= _MAX_LASTRO or profundidade > 12:
        return
    if isinstance(valor, bool):  # bool é int em Python; não é número fiscal.
        return
    if isinstance(valor, int | float | Decimal):
        destino.add(Decimal(str(valor)))
        return
    if isinstance(valor, str):
        for bruto in _NUMERO_QUALQUER.findall(valor):
            convertido = _para_decimal(bruto)
            if convertido is not None:
                destino.add(convertido)
            if len(destino) >= _MAX_LASTRO:
                return
        return
    if isinstance(valor, dict):
        for filho in valor.values():
            _colher(filho, destino, profundidade + 1)
        return
    if isinstance(valor, list | tuple):
        for filho in valor:
            _colher(filho, destino, profundidade + 1)


def lastro(*fontes: Any) -> set[Decimal]:
    """Conjunto de valores que a plataforma entregou naquela conversa.

    Recebe qualquer combinação de payloads de ferramenta, dicionários de fato, textos de
    norma e verbetes — tudo que **veio da plataforma**. O que o modelo escreveu nunca entra
    aqui: seria o guardrail conferindo o modelo contra ele mesmo.
    """
    valores: set[Decimal] = set()
    for fonte in fontes:
        _colher(fonte, valores)
    return valores


def _tem_lastro(citado: NumeroCitado, valores: Iterable[Decimal]) -> bool:
    alvo = citado.valor
    tolerancia = citado.tolerancia
    return any(abs(candidato - alvo) <= tolerancia for candidato in valores)


def verificar(texto: str, *fontes: Any) -> Verificacao:
    """Casa cada número da prosa contra o lastro daquela conversa (G6).

    ``fontes`` são os payloads das ferramentas chamadas e o contexto entregue ao modelo.
    Uma resposta sem número nenhum passa trivialmente — e isso é correto: a recusa honesta
    e a explicação puramente normativa não citam valor apurado.
    """
    citados = numeros_citados(texto)
    if not citados:
        return Verificacao(total_citados=0, com_lastro=0, sem_lastro=())
    valores = lastro(*fontes)
    orfaos = tuple(c for c in citados if not _tem_lastro(c, valores))
    return Verificacao(
        total_citados=len(citados),
        com_lastro=len(citados) - len(orfaos),
        sem_lastro=orfaos,
    )


def anexar_aviso(texto: str, verificacao: Verificacao) -> str:
    """Junta o aviso de G6 à resposta — "sinalizado, não publicado em silêncio".

    O aviso vai no corpo da resposta, e não só num campo estruturado ao lado, porque um
    campo que a tela pode ignorar é exatamente "publicar em silêncio". O texto original
    fica **intacto** acima dele: quem audita precisa ver o que o modelo escreveu.
    """
    aviso = verificacao.aviso()
    return f"{texto}\n\n{aviso}" if aviso else texto
