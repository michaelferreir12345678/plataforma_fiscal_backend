"""Laço de agente com teto de passos e orçamento de tokens (Sprint IA-3).

O laço vivia dentro do adaptador Gemini (``llm.py``), e por isso não podia ser testado sem
rede nem credencial — justamente a parte que decide **quando parar** e **o que devolver
quando para**. Aqui ele vira código de domínio: o adaptador implementa só a tradução de um
turno para o SDK (:class:`MotorDeTurno`), e a política de parada é exercitada pela suíte
com um motor falso.

**A degradação é a razão de existir do módulo.** A versão anterior estourava
``LLMProviderError`` quando o modelo pedia ferramentas em todas as rodadas sem redigir —
o gestor recebia um erro depois de a plataforma ter consultado e pago por cinco
indicadores. Errado nos dois sentidos: descarta trabalho já feito e trata como falha de
provedor o que é comportamento previsto de agente.

Agora o estouro produz uma **resposta parcial declarada**, composta exclusivamente dos
valores que as ferramentas devolveram, com a fonte de cada um e uma frase dizendo que
está incompleta e por quê. Nunca uma resposta inventada — a composição é extrativa, no
mesmo espírito do ``LocalGroundedProvider``: o texto não pode conter número que não esteja
no payload, porque não há de onde tirar um.

**Por que não uma última chamada ao modelo "para fechar".** Seria o caminho natural e é
uma armadilha: a volta extra ocorre exatamente quando o orçamento acabou, custa mais
tokens e, sem ferramentas, é o cenário em que o modelo mais tende a completar de memória o
que não conseguiu buscar. Preferimos prosa pobre e verificável a prosa fluente e sem
lastro.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.modules.assistant.llm import LLMProviderError, LLMResult

logger = logging.getLogger(__name__)

MOTIVO_PASSOS = "teto de passos"
MOTIVO_TOKENS = "orçamento de tokens"


@dataclass(frozen=True)
class OrcamentoAgente:
    """Os dois limites do laço. Ambos param; nenhum dos dois derruba a resposta."""

    max_passos: int = 4
    max_tokens: int = 60000

    def __post_init__(self) -> None:
        if self.max_passos < 1:
            raise ValueError("O laço de agente precisa de ao menos um passo.")


@dataclass(frozen=True)
class ChamadaPedida:
    """Uma ferramenta que o modelo pediu, já traduzida para fora do SDK."""

    nome: str
    argumentos: dict[str, Any]


@dataclass(frozen=True)
class Turno:
    """O que o modelo devolveu numa volta: texto **ou** pedidos de ferramenta."""

    texto: str | None = None
    chamadas: tuple[ChamadaPedida, ...] = ()
    tokens_entrada: int = 0
    tokens_saida: int = 0
    #: Explicação de por que veio vazio, quando veio (o adaptador sabe olhar o
    #: ``finish_reason``; o laço não deve aprender a fazer isso).
    motivo_vazio: str | None = None


@runtime_checkable
class MotorDeTurno(Protocol):
    """Uma volta de conversa com o modelo. O histórico é do adaptador (SDK-específico).

    Fica com o adaptador de propósito: os modelos com raciocínio exigem que a
    ``Part.thought_signature`` volte junto no round-trip do *function calling*, e isso é
    detalhe do ``google-genai`` que o domínio não deve conhecer nem conseguir corromper.
    """

    modelo: str

    def gerar(self, respostas: Sequence[tuple[str, dict[str, Any]]] | None) -> Turno:
        """Envia o pedido inicial (``None``) ou os resultados das ferramentas pedidas."""
        ...


#: Executa uma ferramenta e devolve o payload. É o envelope de ``shared/tooling`` — com
#: escopo, licença, ``source_ref`` e auditoria — nunca uma chamada direta ao banco.
Executor = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass
class ResultadoLaco:
    """Saída do laço + o que ele consumiu (alimenta ``op.conversa_uso`` e o G6)."""

    texto: str
    modelo: str
    tokens_entrada: int = 0
    tokens_saida: int = 0
    #: Payloads devolvidos pelas ferramentas — o **lastro** que o G6 vai conferir.
    payloads: list[dict[str, Any]] = field(default_factory=list)
    passos: int = 0
    parcial: bool = False
    motivo_parcial: str | None = None

    def para_llm_result(self) -> LLMResult:
        return LLMResult(
            texto=self.texto,
            modelo=self.modelo,
            tokens_entrada=self.tokens_entrada,
            tokens_saida=self.tokens_saida,
        )


def executar_laco(
    motor: MotorDeTurno, executar: Executor, *, orcamento: OrcamentoAgente | None = None
) -> ResultadoLaco:
    """Roda o laço até o modelo redigir, o teto estourar ou o orçamento acabar."""
    limites = orcamento or OrcamentoAgente()
    estado = ResultadoLaco(texto="", modelo=motor.modelo)
    respostas: list[tuple[str, dict[str, Any]]] | None = None

    for passo in range(1, limites.max_passos + 1):
        turno = motor.gerar(respostas)
        estado.passos = passo
        estado.tokens_entrada += max(turno.tokens_entrada, 0)
        estado.tokens_saida += max(turno.tokens_saida, 0)

        if not turno.chamadas:
            texto = (turno.texto or "").strip()
            if not texto:
                # Sem texto e sem pedido não é degradação: é o provedor não tendo
                # respondido. Isso continua sendo LLMProviderError (§9), nunca uma
                # resposta montada por nós fingindo ser dele.
                raise LLMProviderError(
                    detail=turno.motivo_vazio
                    or f"O modelo {motor.modelo} retornou resposta vazia."
                )
            estado.texto = texto
            return estado

        respostas = []
        for chamada in turno.chamadas:
            payload = executar(chamada.nome, dict(chamada.argumentos))
            respostas.append((chamada.nome, payload))
            estado.payloads.append(payload)

        gastos = estado.tokens_entrada + estado.tokens_saida
        if gastos >= limites.max_tokens:
            return _degradar(estado, MOTIVO_TOKENS, limites)

    return _degradar(estado, MOTIVO_PASSOS, limites)


def _degradar(
    estado: ResultadoLaco, motivo: str, limites: OrcamentoAgente
) -> ResultadoLaco:
    """Compõe a resposta parcial **declarada** a partir do que já foi apurado."""
    logger.warning(
        "Laço de agente interrompido por %s (passos=%s, tokens=%s); "
        "degradando para resposta parcial com %s resultado(s) de ferramenta.",
        motivo,
        estado.passos,
        estado.tokens_entrada + estado.tokens_saida,
        len(estado.payloads),
    )
    estado.parcial = True
    estado.motivo_parcial = motivo
    estado.texto = compor_parcial(estado.payloads, motivo=motivo, limites=limites)
    return estado


def _fonte_legivel(ref: Any) -> str | None:
    if not isinstance(ref, dict):
        return None
    partes = [str(ref.get("relatorio") or "").strip()]
    for chave in ("anexo", "periodo"):
        if valor := ref.get(chave):
            partes.append(str(valor))
    if versao := ref.get("versao_entrega"):
        partes.append(f"versão {versao}")
    texto = ", ".join(p for p in partes if p)
    return texto or None


def _linha_do_payload(payload: dict[str, Any]) -> str | None:
    """Uma linha extrativa por payload. Sem chave conhecida, não inventa formatação.

    Os campos lidos (``rotulo``, ``valor_formatado``, ``periodo``, ``source_ref``) são os
    do contrato de saída das ferramentas de indicador. Para as demais — linhagem,
    cobertura, drill — a linha diz o que foi consultado sem tentar resumir número que não
    sabe interpretar. Resumir errado seria pior que não resumir.
    """
    rotulo = payload.get("rotulo") or payload.get("indicador")
    if not isinstance(rotulo, str):
        return None
    if payload.get("disponivel") is False:
        motivo = payload.get("observacao")
        return f"• {rotulo}: dado não apurado" + (f" — {motivo}" if motivo else "") + "."
    valor = payload.get("valor_formatado")
    if not isinstance(valor, str) or not valor.strip():
        return None
    periodo = payload.get("periodo")
    fonte = _fonte_legivel(payload.get("source_ref"))
    linha = f"• {rotulo}"
    if periodo:
        linha += f" ({periodo})"
    linha += f": {valor}"
    if fonte:
        linha += f" (fonte: {fonte})"
    return linha + "."


def compor_parcial(
    payloads: Sequence[dict[str, Any]], *, motivo: str, limites: OrcamentoAgente
) -> str:
    """Texto da resposta parcial: só valores já devolvidos, com a fonte de cada um.

    Pública para que o teste componha o mesmo texto que a produção compõe — e para que
    fique explícito que **nenhum** trecho dela vem do modelo.
    """
    teto = (
        f"{limites.max_passos} passos"
        if motivo == MOTIVO_PASSOS
        else f"{limites.max_tokens} tokens"
    )
    blocos = [
        "**Resposta parcial.** A consulta atingiu o limite de "
        f"{teto} antes de o assistente concluir a redação, então o texto abaixo traz "
        "apenas o que já havia sido apurado — não é a resposta completa à sua pergunta."
    ]
    linhas = [linha for p in payloads if (linha := _linha_do_payload(p)) is not None]
    if linhas:
        blocos.append("Dados já consultados nesta conversa:")
        blocos.extend(linhas)
    elif payloads:
        blocos.append(
            f"Foram consultadas {len(payloads)} ferramentas, mas nenhuma devolveu um "
            "indicador em forma de valor apresentável aqui. Consulte a tela do "
            "indicador para ver o resultado com a fonte."
        )
    else:
        blocos.append(
            "Nenhuma ferramenta chegou a devolver dado antes do limite. Não há número a "
            "apresentar — e a plataforma não estima o que não apurou."
        )
    blocos.append(
        "Refaça a pergunta de forma mais específica (um indicador e um período por vez) "
        "para obter a análise completa."
    )
    return "\n".join(blocos)
