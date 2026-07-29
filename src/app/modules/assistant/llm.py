"""Porta ``LLMProvider`` e adaptadores (Sprint 17).

O domínio (RAG, guardrails, ``source_ref``) fala com esta porta e **nunca** importa o
SDK do provedor. O contrato transporta o contexto **estruturado** (fatos já calculados +
dispositivos normativos), de modo que:

- o adaptador Gemini renderiza esse contexto num *prompt* e chama o modelo;
- o adaptador local determinístico compõe a resposta diretamente do contexto (offline,
  extrativo — **nunca inventa número**);
- os testes injetam um provedor falso que inspeciona o contexto ou simula falha.

Falha/timeout do provedor vira :class:`LLMProviderError` (RFC 7807) — nunca uma resposta
inventada. Trocar de provedor é trocar o adaptador registrado em :func:`build_provider`.
"""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol, runtime_checkable

from app.core.config import Settings, get_settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)


def gemini_sdk_available() -> bool:
    """True se o SDK ``google-genai`` está instalável no ambiente.

    Permite degradar para o provedor local quando a chave está configurada mas o SDK
    não está presente (dev/CI sem a dependência) — em vez de falhar toda requisição.
    """
    try:
        # find_spec lança ModuleNotFoundError quando o pacote pai ('google') não existe.
        return importlib.util.find_spec("google.genai") is not None
    except (ImportError, ValueError):
        return False


def use_gemini(settings: Settings) -> bool:
    """Decisão única de provedor: Gemini só quando pedido, com chave **e** SDK presente."""
    return (
        settings.assistant_provider.lower() != "local"
        and bool(settings.gemini_api_key)
        and gemini_sdk_available()
    )


class LLMProviderError(AppError):
    """Indisponibilidade/erro do provedor de IA — degrada com erro claro (§9)."""

    def __init__(self, *, detail: str, status: int = 502) -> None:
        super().__init__(
            status=status,
            title="Assistente de IA indisponível",
            detail=detail,
            type_="urn:plataforma-fiscal:error:llm-provider-unavailable",
        )


@dataclass(frozen=True)
class FatoContexto:
    """Um indicador **já calculado** do ente (gold) com rastreabilidade completa."""

    codigo: str
    rotulo: str
    valor_formatado: str
    unidade: str
    status: str
    disponivel: bool
    periodo: str
    source_ref: dict
    as_of: str | None = None
    faixa: str | None = None
    valor: str | None = None
    memoria: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NormaContexto:
    """Um dispositivo normativo recuperado do *vector store* (explicação geral da norma)."""

    fonte: str
    dispositivo: str
    texto: str
    titulo: str | None = None
    score: float = 0.0


@dataclass(frozen=True)
class LLMRequest:
    """Requisição fundamentada enviada à porta — contexto estruturado, não texto solto."""

    system: str
    pergunta: str
    ente_label: str | None = None
    periodo: str | None = None
    fatos: tuple[FatoContexto, ...] = ()
    normas: tuple[NormaContexto, ...] = ()
    modelo: str | None = None
    temperatura: float = 0.2
    #: Teto de saída. Precisa acomodar o **raciocínio** dos modelos 3.x, que sai do mesmo
    #: orçamento: o ``gemini-3.5-flash`` gasta ~250 tokens pensando antes da primeira
    #: palavra, e um teto apertado devolveria resposta vazia com o modelo saudável. É um
    #: limite, não um gasto — elevar não encarece quem responde curto.
    max_tokens: int | None = 2048


@dataclass(frozen=True)
class LLMResult:
    """Resposta do provedor + telemetria (alimenta ``op.conversa_uso``)."""

    texto: str
    modelo: str
    tokens_entrada: int
    tokens_saida: int


@runtime_checkable
class LLMProvider(Protocol):
    """Porta do provedor de IA. Adaptadores implementam ``chat``."""

    name: str

    def chat(self, request: LLMRequest) -> LLMResult:
        """Responde de forma fundamentada no contexto. Erros ⇒ :class:`LLMProviderError`."""
        ...


# --------------------------------------------------------------------------- #
# Renderização compartilhada do contexto para provedores textuais (LLMs).
# --------------------------------------------------------------------------- #
def _fmt_source(ref: dict) -> str:
    partes = [ref.get("relatorio") or "fonte"]
    if ref.get("anexo"):
        partes.append(str(ref["anexo"]))
    if ref.get("periodo"):
        partes.append(str(ref["periodo"]))
    if ref.get("versao_entrega"):
        partes.append(f"versão {ref['versao_entrega']}")
    return ", ".join(partes)


def render_grounding(request: LLMRequest) -> str:
    """Serializa o contexto estruturado num bloco textual para o LLM."""
    linhas: list[str] = []
    if request.ente_label:
        alvo = request.ente_label
        if request.periodo:
            alvo += f" — período {request.periodo}"
        linhas.append(f"ENTE/PERÍODO: {alvo}")
    disponiveis = [f for f in request.fatos if f.disponivel]
    indisponiveis = [f for f in request.fatos if not f.disponivel]
    if disponiveis:
        linhas.append("\nINDICADORES CALCULADOS DOS DADOS DO ENTE (use apenas estes números):")
        for fato in disponiveis:
            faixa = f" [{fato.faixa}]" if fato.faixa else ""
            linhas.append(
                f"- {fato.rotulo}: {fato.valor_formatado}{faixa} "
                f"(fonte: {_fmt_source(fato.source_ref)})"
            )
    if indisponiveis:
        linhas.append("\nSEM DADO MATERIALIZADO (NÃO afirme valores — sinalize a ausência):")
        for fato in indisponiveis:
            linhas.append(f"- {fato.rotulo} ({fato.periodo})")
    if request.normas:
        linhas.append("\nDISPOSITIVOS NORMATIVOS (explicação geral da norma):")
        for norma in request.normas:
            linhas.append(f"- {norma.dispositivo} ({norma.fonte}): {norma.texto}")
    if not disponiveis and not request.normas:
        linhas.append("\n(NENHUM dado nem dispositivo relevante foi recuperado.)")
    return "\n".join(linhas)


# --------------------------------------------------------------------------- #
# Adaptador local determinístico (offline, sem rede, nunca inventa número).
# --------------------------------------------------------------------------- #
class LocalGroundedProvider:
    """Compositor extrativo do contexto — default sem ``GEMINI_API_KEY``.

    Não é um LLM: costura os fatos calculados e os dispositivos normativos numa resposta
    legível, citando a fonte de cada número. Como só reutiliza valores já presentes no
    contexto, respeita por construção o guardrail "nenhum número sem fonte".
    """

    name = "local-grounded"

    def chat(self, request: LLMRequest) -> LLMResult:
        from app.modules.assistant import vectors

        disponiveis = [f for f in request.fatos if f.disponivel]
        indisponiveis = [f for f in request.fatos if not f.disponivel]
        blocos: list[str] = []

        if disponiveis:
            alvo = request.ente_label or "o ente"
            periodo = f" no período {request.periodo}" if request.periodo else ""
            blocos.append(
                f"Com base nos indicadores já calculados dos dados de {alvo}{periodo}:"
            )
            for fato in disponiveis:
                nota = f" — situação: {fato.faixa}" if fato.faixa else ""
                blocos.append(
                    f"• {fato.rotulo}: {fato.valor_formatado}{nota} "
                    f"(fonte: {_fmt_source(fato.source_ref)})."
                )

        if indisponiveis:
            rotulos = "; ".join(f.rotulo for f in indisponiveis)
            blocos.append(
                "Não há dado fiscal materializado para: "
                f"{rotulos}. Não é possível afirmar esses valores para o período informado — "
                "recomenda-se verificar a entrega/retificação no SICONFI."
            )

        if request.normas:
            blocos.append("Fundamentação normativa:")
            for norma in request.normas:
                texto = norma.texto.strip()
                if len(texto) > 320:
                    texto = texto[:317].rstrip() + "…"
                blocos.append(f"• {norma.dispositivo} ({norma.fonte}): {texto}")

        if not disponiveis and not request.normas:
            blocos.append(
                "Não localizei indicadores calculados nem dispositivos normativos "
                "aplicáveis à sua pergunta. Não vou inferir números sem fonte."
            )

        blocos.append(
            "Esta resposta é informativa e fundamentada apenas nas fontes citadas; "
            "não constitui parecer jurídico ou contábil definitivo."
        )
        texto = "\n".join(blocos)
        entrada = vectors.approx_tokens(
            request.system + request.pergunta + render_grounding(request)
        )
        saida = vectors.approx_tokens(texto)
        return LLMResult(
            texto=texto, modelo=self.name, tokens_entrada=entrada, tokens_saida=saida
        )


# --------------------------------------------------------------------------- #
# Adaptador Google Gemini (SDK google-genai importado preguiçosamente).
# --------------------------------------------------------------------------- #
def _motivo_resposta_vazia(response: object, usage: object, modelo: str) -> str:
    """Explica **por que** veio vazio, em vez de só constatar que veio.

    Os modelos com raciocínio (Gemini 3.x) gastam tokens pensando **antes** de escrever, e
    esses tokens saem do mesmo ``max_output_tokens``. Um teto curto produz resposta vazia
    com o modelo funcionando perfeitamente — sem esta mensagem, o operador procuraria falha
    de rede ou de credencial durante horas.
    """
    pensamento = int(getattr(usage, "thoughts_token_count", 0) or 0)
    candidatos = getattr(response, "candidates", None) or []
    razao = str(getattr(candidatos[0], "finish_reason", "") or "") if candidatos else ""
    if "MAX_TOKENS" in razao.upper() and pensamento:
        return (
            f"O modelo {modelo} consumiu o limite de saída raciocinando "
            f"({pensamento} tokens de raciocínio) e não sobrou espaço para a resposta. "
            "Aumente ``max_tokens`` da requisição."
        )
    if razao and "STOP" not in razao.upper():
        return f"O modelo {modelo} interrompeu a geração ({razao}) sem produzir texto."
    return f"O modelo {modelo} retornou resposta vazia."


class GeminiProvider:
    """Adaptador do Google Gemini via SDK oficial ``google-genai``.

    O SDK é importado **dentro** dos métodos: o módulo carrega sem a dependência e os
    testes rodam sem rede. ``modelo`` da requisição permite o ``gemini-2.5-pro`` no
    resumo executivo; o default é o ``assistant_chat_model``.

    **Fallback de modelo.** Se o principal está indisponível (modelo inexistente para a
    chave, cota estourada, serviço fora), tenta os de reserva **em ordem**. Só isso: erro
    de credencial ou de conteúdo **não** cai para outro modelo — um degrade silencioso
    esconderia configuração errada, e o resultado sempre declara qual modelo respondeu,
    para que a tela possa dizê-lo ao gestor.
    """

    name = "gemini"

    #: Sinais de indisponibilidade do modelo (e não de erro do pedido).
    _MARCAS_INDISPONIVEL = (
        "not found",
        "not_found",
        "is not supported",
        "does not exist",
        "unavailable",
        "overloaded",
        "resource_exhausted",
        "resource exhausted",
        "quota",
        "429",
        "503",
        "404",
    )

    def __init__(
        self,
        *,
        api_key: str,
        chat_model: str,
        summary_model: str,
        timeout_s: float,
        fallback_models: Sequence[str] = (),
    ) -> None:
        self._api_key = api_key
        self._chat_model = chat_model
        self._summary_model = summary_model
        self._fallback_models = tuple(fallback_models)
        self._timeout_ms = int(timeout_s * 1000)
        self._client: object | None = None

    def _modelos_a_tentar(self, pedido: str | None) -> list[str]:
        """Principal + reservas, sem repetir. Modelo pedido explicitamente não tem reserva:
        quem escolheu o ``gemini-2.5-pro`` para o resumo quer aquele modelo, não outro."""
        if pedido:
            return [pedido]
        ordem = [self._chat_model, *self._fallback_models]
        return list(dict.fromkeys(m for m in ordem if m))

    @classmethod
    def _indisponivel(cls, exc: Exception) -> bool:
        texto = f"{exc.__class__.__name__}: {exc}".lower()
        return any(marca in texto for marca in cls._MARCAS_INDISPONIVEL)

    def _ensure_client(self) -> object:
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover - depende do ambiente
                raise LLMProviderError(
                    detail="SDK google-genai não instalado; configure ASSISTANT_PROVIDER=local."
                ) from exc
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def chat(self, request: LLMRequest) -> LLMResult:  # pragma: no cover - requer rede/credencial
        client = self._ensure_client()
        candidatos = self._modelos_a_tentar(request.modelo)
        prompt = (
            f"{render_grounding(request)}\n\n"
            f"PERGUNTA DO GESTOR:\n{request.pergunta}\n\n"
            "Responda em português, de forma objetiva e fundamentada. Cite a fonte de "
            "cada número (relatório/anexo/período/versão). Distinga o que é 'calculado "
            "dos dados do ente' do que é 'explicação geral da norma'. Se um dado não foi "
            "fornecido acima, diga que não está disponível — não estime nem invente."
        )
        try:
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise LLMProviderError(detail=f"SDK google-genai indisponível: {exc}") from exc

        config = types.GenerateContentConfig(
            system_instruction=request.system,
            temperature=request.temperatura,
            max_output_tokens=request.max_tokens,
            http_options=types.HttpOptions(timeout=self._timeout_ms),
        )
        ultimo: Exception | None = None
        for indice, modelo in enumerate(candidatos):
            try:
                response = client.models.generate_content(  # type: ignore[attr-defined]
                    model=modelo, contents=prompt, config=config
                )
            except Exception as exc:
                ultimo = exc
                if indice + 1 < len(candidatos) and self._indisponivel(exc):
                    logger.warning(
                        "Gemini %s indisponível (%s); tentando %s",
                        modelo,
                        exc.__class__.__name__,
                        candidatos[indice + 1],
                    )
                    continue
                raise LLMProviderError(detail=f"Falha na chamada ao Gemini: {exc}") from exc

            usage = getattr(response, "usage_metadata", None)
            texto = (getattr(response, "text", None) or "").strip()
            if not texto:
                raise LLMProviderError(detail=_motivo_resposta_vazia(response, usage, modelo))
            return LLMResult(
                texto=texto,
                modelo=modelo,
                tokens_entrada=int(getattr(usage, "prompt_token_count", 0) or 0),
                tokens_saida=int(getattr(usage, "candidates_token_count", 0) or 0),
            )
        raise LLMProviderError(detail=f"Falha na chamada ao Gemini: {ultimo}")


def build_provider(settings: Settings) -> LLMProvider:
    """Fábrica do provedor de chat (mesma regra do *embedder*).

    ``ASSISTANT_PROVIDER=local`` força o offline; ``auto`` usa Gemini quando há chave e
    o SDK está instalado (senão degrada para o local determinístico).
    """
    if use_gemini(settings):
        return GeminiProvider(
            api_key=settings.gemini_api_key or "",
            chat_model=settings.assistant_chat_model,
            summary_model=settings.assistant_summary_model,
            timeout_s=settings.assistant_request_timeout_s,
            fallback_models=settings.assistant_chat_fallback_models,
        )
    return LocalGroundedProvider()


@lru_cache
def _cached_provider() -> LLMProvider:
    return build_provider(get_settings())


def get_llm_provider() -> LLMProvider:
    """Dependência FastAPI do provedor de chat (sobrescrevível em testes)."""
    return _cached_provider()
