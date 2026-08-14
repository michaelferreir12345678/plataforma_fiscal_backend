"""Regras do assistente de IA (Módulo 15): RAG fundamentado + guardrails da §9.

Fluxo (perguntar / resumo-executivo):
1. valida organização e **escopo/esfera** do ente (§6.4);
2. recupera indicadores JÁ CALCULADOS do ente + dispositivos normativos (RAG);
3. se não há dado nem norma aplicável ⇒ **recusa honesta** (sem chamar o LLM, sem
   inventar número);
4. caso contrário chama o provedor pela porta ``LLMProvider``. Falha/timeout ⇒
   ``LLMProviderError`` (RFC 7807) — nunca uma resposta sem fonte;
5. registra ``op.conversa`` + ``op.conversa_uso`` (telemetria/cota) + trilha de auditoria.

Toda resposta carrega ``source_ref`` por número (calculado × norma) e distingue o que é
"calculado dos seus dados" da "explicação geral da norma".
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import Principal
from app.core.errors import AppError
from app.modules.assistant import repository, retriever
from app.modules.assistant.embeddings import Embedder, get_embedder
from app.modules.assistant.llm import (
    FatoContexto,
    LLMProvider,
    LLMRequest,
    LLMResult,
    NormaContexto,
    ToolCallingProvider,
    ToolSpec,
    schema_para_provedor,
)
from app.modules.assistant.schemas import (
    ConversaResumo,
    ConversasOut,
    DadoIncompleto,
    FatoResposta,
    FonteChip,
    NormaResposta,
    PerguntaRequest,
    RespostaOut,
    ResumoExecutivoRequest,
    UsoInfo,
    UsoResumoOut,
    VerificacaoOut,
)
from app.modules.tenancy import repository as tenancy_repo
from app.shared import tooling
from app.shared.scope import assert_ente_in_scope
from app.shared.source_ref import SourceRef
from app.shared.tooling import verificacao

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Você é o assistente fiscal da Plataforma de Inteligência Fiscal, que atende gestores "
    "públicos técnicos (contador, controlador, secretário de finanças). Regras invioláveis:\n"
    "1. Responda SOMENTE com base no contexto fornecido: indicadores já calculados dos "
    "dados do ente e dispositivos normativos (LRF, CF, MDF). NUNCA use números de memória "
    "do modelo nem invente valores.\n"
    "2. Cite a fonte de cada número (relatório, anexo, período, versão da entrega).\n"
    "3. Distinga claramente o que é 'calculado dos dados do ente' do que é 'explicação "
    "geral da norma'.\n"
    "4. Se um dado necessário está ausente ou desatualizado, diga isso explicitamente e "
    "não estime — sinalize a lacuna.\n"
    "5. Considere a esfera (municipal/estadual) do ente ao explicar limites.\n"
    "5.1. Quando o contexto trouxer o DICIONÁRIO DA PLATAFORMA, ele PREVALECE sobre o seu "
    "conhecimento geral para fórmula, denominador, unidade e sentido de um indicador — "
    "inclusive quando divergir do que você sabe (ex.: o denominador dos limites de pessoal "
    "e dívida é a RCL Ajustada, não a RCL cheia). Sem verbete no contexto, diga que a "
    "definição não foi fornecida em vez de recorrer à memória.\n"
    "6. Não emita parecer jurídico ou contábil definitivo; explique e aponte o dispositivo "
    "aplicável.\n"
    "Escreva em português, de forma objetiva e útil para a decisão do gestor."
)

_FONTE_LONGA = {
    "LRF": "Lei de Responsabilidade Fiscal (LC 101/2000)",
    "CF": "Constituição Federal de 1988",
    "MDF": "Manual de Demonstrativos Fiscais (STN)",
}


def _org(principal: Principal) -> uuid.UUID:
    if principal.org_id is None:
        raise AppError(status=403, title="Sem organização", detail="Requer organização ativa.")
    return principal.org_id


def _to_source_ref(ref: dict | None) -> SourceRef | None:
    if not ref:
        return None
    return SourceRef(
        relatorio=ref.get("relatorio") or "FONTE-NÃO-INFORMADA",
        anexo=ref.get("anexo"),
        periodo=ref.get("periodo"),
        versao_entrega=ref.get("versao_entrega"),
    )


def _parse_as_of(valor: str | None) -> datetime | None:
    if not valor:
        return None
    try:
        parsed = datetime.fromisoformat(valor)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def fato_para_resposta(fato: FatoContexto) -> FatoResposta:
    """Fato do contexto → fato da resposta. Pública desde a IA-5.

    A IA nas telas devolve os mesmos fatos com a mesma forma — é o que permite ao
    frontend renderizar as quatro superfícies novas com o componente que já ancora número
    em fonte (``RespostaMarkdown``, Sprint B3). Duas conversões produziriam duas formas.
    """
    return FatoResposta(
        codigo=fato.codigo,
        rotulo=fato.rotulo,
        valor_formatado=fato.valor_formatado,
        valor=fato.valor,
        unidade=fato.unidade,
        status=fato.status,
        faixa=fato.faixa,
        disponivel=fato.disponivel,
        periodo=fato.periodo,
        as_of=_parse_as_of(fato.as_of),
        source_ref=_to_source_ref(fato.source_ref),
        memoria=fato.memoria,
    )


def _norma_to_resposta(norma: NormaContexto) -> NormaResposta:
    return NormaResposta(
        fonte=norma.fonte,
        dispositivo=norma.dispositivo,
        titulo=norma.titulo,
        trecho=norma.texto,
        score=norma.score,
    )


def _chips(disponiveis: list[FatoContexto], normas: list[NormaContexto]) -> list[FonteChip]:
    chips: list[FonteChip] = []
    for fato in disponiveis:
        chips.append(
            FonteChip(
                tipo="indicador",
                rotulo=fato.rotulo,
                detalhe=f"{fato.periodo} · {fato.valor_formatado}",
                source_ref=_to_source_ref(fato.source_ref),
            )
        )
    for norma in normas:
        chips.append(
            FonteChip(
                tipo="norma",
                rotulo=norma.dispositivo,
                detalhe=_FONTE_LONGA.get(norma.fonte, norma.fonte),
                source_ref=SourceRef(
                    relatorio=_FONTE_LONGA.get(norma.fonte, norma.fonte),
                    anexo=norma.dispositivo,
                ),
            )
        )
    return chips


def _source_refs(
    indicador_refs: list[dict], normas: list[NormaContexto]
) -> list[SourceRef]:
    seen: dict[tuple, SourceRef] = {}
    for ref in indicador_refs:
        sr = _to_source_ref(ref)
        if sr is not None:
            seen[(sr.relatorio, sr.anexo, sr.periodo, sr.versao_entrega)] = sr
    for norma in normas:
        sr = SourceRef(
            relatorio=_FONTE_LONGA.get(norma.fonte, norma.fonte), anexo=norma.dispositivo
        )
        seen[(sr.relatorio, sr.anexo, sr.periodo, sr.versao_entrega)] = sr
    return list(seen.values())


def _dados_incompletos(
    issues: list[dict], *, codigos_visiveis: set[str] | None
) -> list[DadoIncompleto]:
    resultado: list[DadoIncompleto] = []
    for issue in issues:
        codigo = issue.get("codigo", "")
        if (
            codigos_visiveis is not None
            and codigo not in codigos_visiveis
            and not codigo.startswith("fonte_")
        ):
            continue
        resultado.append(
            DadoIncompleto(
                tipo=issue.get("tipo", "ausente"),
                codigo=codigo,
                mensagem=issue.get("mensagem", ""),
                periodo_esperado=issue.get("periodo_esperado"),
                periodo_encontrado=issue.get("periodo_encontrado"),
            )
        )
    return resultado


def ente_label(nome: str | None, cod_ibge: str, esfera: str | None) -> str:
    """Rótulo do ente para o prompt (nome, código e esfera). Pública desde a IA-5."""
    base = nome or f"ente {cod_ibge}"
    detalhe = f" ({cod_ibge}" + (f", esfera {esfera}" if esfera else "") + ")"
    return base + detalhe


def _refusal_text(cod_ibge: str, periodo: str | None) -> str:
    alvo = f" para {cod_ibge}" + (f" no período {periodo}" if periodo else "")
    return (
        f"Não localizei indicadores fiscais materializados{alvo} nem dispositivos "
        "normativos aplicáveis à sua pergunta. Para não induzir a erro, não vou estimar "
        "números sem fonte. Verifique se a entrega correspondente já foi ingerida do "
        "SICONFI ou refine a pergunta."
    )


@dataclass
class _Derivado:
    """Tudo que a resposta expõe sobre o fundamento — recalculável depois das ferramentas."""

    fatos: list[FatoResposta]
    normas: list[NormaResposta]
    chips: list[FonteChip]
    source_refs: list[SourceRef]
    incompletos: list[DadoIncompleto]
    dado_disponivel: bool


def _derivar(
    ctx: retriever.GroundedContext, *, codigos_visiveis: set[str] | None
) -> _Derivado:
    """Deriva fatos/chips/fontes do contexto corrente.

    É uma função, e não um trecho no meio do fluxo, porque o contexto pode **crescer
    durante** a conversa: quando o provedor faz *function calling*, os fatos chegam depois
    da chamada, e os chips e ``source_ref`` da resposta têm de incluí-los. Derivar duas
    vezes do mesmo lugar garante que a resposta e a sua rastreabilidade não divirjam.
    """
    disponiveis = [f for f in ctx.fatos if f.disponivel]
    return _Derivado(
        fatos=[fato_para_resposta(f) for f in ctx.fatos],
        normas=[_norma_to_resposta(n) for n in ctx.normas],
        chips=_chips(disponiveis, ctx.normas),
        source_refs=_source_refs(ctx.source_refs, ctx.normas),
        incompletos=_dados_incompletos(
            ctx.dados_incompletos, codigos_visiveis=codigos_visiveis
        ),
        dado_disponivel=bool(disponiveis),
    )


def _run(
    session: Session,
    principal: Principal,
    provider: LLMProvider,
    *,
    tipo: str,
    cod_ibge: str,
    pergunta: str,
    ctx: retriever.GroundedContext,
    modelo_desejado: str | None,
    titulo: str | None,
    codigos_visiveis: set[str] | None,
) -> RespostaOut:
    """Aplica os guardrails, chama (ou não) o provedor e persiste conversa + uso + auditoria."""
    org_id = _org(principal)
    disponiveis = [f for f in ctx.fatos if f.disponivel]
    tem_norma = bool(ctx.normas)

    derivado = _derivar(ctx, codigos_visiveis=codigos_visiveis)
    fatos_resp = derivado.fatos
    normas_resp = derivado.normas
    chips = derivado.chips
    source_refs = derivado.source_refs
    incompletos = derivado.incompletos
    dado_disponivel = derivado.dado_disponivel
    gerado_em = datetime.now(UTC)

    if not disponiveis and not tem_norma:
        # Recusa honesta: nenhum fundamento ⇒ não chamamos o LLM nem inventamos número.
        resposta_texto = _refusal_text(cod_ibge, ctx.periodo)
        conversa = repository.insert_conversa(
            session,
            org_id=org_id,
            usuario_id=principal.usuario_id,
            tipo=tipo,
            cod_ibge=cod_ibge,
            periodo=ctx.periodo,
            pergunta=pergunta,
            resposta=resposta_texto,
            recusa=True,
            dado_disponivel=False,
            modelo=None,
            fontes=[],
            fatos=[f.model_dump(mode="json") for f in fatos_resp],
            source_refs=[s.model_dump(mode="json") for s in source_refs],
            dados_incompletos=[d.model_dump(mode="json") for d in incompletos],
            as_of=ctx.as_of,
        )
        _audit(
            session,
            principal,
            tipo=tipo,
            cod_ibge=cod_ibge,
            conversa_id=conversa.id,
            modelo="recusa",
        )
        return RespostaOut(
            conversa_id=conversa.id,
            tipo=tipo,
            ente=cod_ibge,
            ente_nome=ctx.ente_nome,
            periodo=ctx.periodo,
            as_of=ctx.as_of,
            titulo=titulo,
            pergunta=pergunta,
            resposta=resposta_texto,
            recusa=True,
            dado_disponivel=False,
            fatos=fatos_resp,
            normas=normas_resp,
            fontes=chips,
            dados_incompletos=incompletos,
            uso=UsoInfo(modelo="n/a", tokens_entrada=0, tokens_saida=0, latencia_ms=0),
            source_refs=source_refs,
            gerado_em=gerado_em,
        )

    request = LLMRequest(
        system=SYSTEM_PROMPT,
        pergunta=pergunta,
        ente_label=ente_label(ctx.ente_nome, cod_ibge, ctx.esfera),
        periodo=ctx.periodo,
        fatos=tuple(ctx.fatos),
        normas=tuple(ctx.normas),
        # Sprint IA-2: o significado viaja junto do número. Note que os verbetes entram
        # **depois** da decisão de recusa acima — definição não é fundamento para
        # responder, e um dicionário sempre presente jamais poderia virar "dado
        # disponível" sem esvaziar o guardrail G3.
        verbetes=tuple(ctx.verbetes),
        modelo=modelo_desejado,
    )
    inicio = time.perf_counter()
    # LLMProviderError propaga (RFC 7807). Quando o provedor sabe pedir ferramentas, quem
    # as executa é o envelope — as garantias não mudam por o modelo ter escolhido.
    result, payloads = _chamar_provedor(
        session, principal, provider, request, cod_ibge=cod_ibge, ctx=ctx
    )
    latencia_ms = int((time.perf_counter() - inicio) * 1000)

    # O contexto pode ter crescido durante a chamada (fatos pedidos pelo modelo): a
    # resposta declara os fatos e as fontes que realmente sustentaram a prosa.
    derivado = _derivar(ctx, codigos_visiveis=None if codigos_visiveis is None else
                        codigos_visiveis | {f.codigo for f in ctx.fatos})
    fatos_resp = derivado.fatos
    normas_resp = derivado.normas
    chips = derivado.chips
    source_refs = derivado.source_refs
    incompletos = derivado.incompletos
    dado_disponivel = derivado.dado_disponivel

    # G6 — verificação de saída. Roda **depois** da rederivação, de propósito: o lastro tem
    # de incluir os fatos que o modelo buscou por ferramenta durante a conversa, senão o
    # guardrail sinalizaria justamente os números mais bem fundamentados da resposta.
    # E roda **antes** de persistir: o que vai para ``op.conversa`` é o texto já com o
    # aviso, para que o histórico e a auditoria mostrem o mesmo que o gestor leu.
    laudo = verificacao.verificar(
        result.texto,
        payloads,
        [f.model_dump(mode="json") for f in fatos_resp],
        [n.texto for n in ctx.normas],
        [v.__dict__ for v in ctx.verbetes],
    )
    texto_final = verificacao.anexar_aviso(result.texto, laudo)
    if not laudo.ok:
        logger.warning(
            "G6 sinalizou %s número(s) sem lastro na conversa do ente %s: %s",
            len(laudo.sem_lastro),
            cod_ibge,
            laudo.tokens_sem_lastro(),
        )

    conversa = repository.insert_conversa(
        session,
        org_id=org_id,
        usuario_id=principal.usuario_id,
        tipo=tipo,
        cod_ibge=cod_ibge,
        periodo=ctx.periodo,
        pergunta=pergunta,
        resposta=texto_final,
        recusa=False,
        dado_disponivel=dado_disponivel,
        modelo=result.modelo,
        fontes=[c.model_dump(mode="json") for c in chips],
        fatos=[f.model_dump(mode="json") for f in fatos_resp],
        source_refs=[s.model_dump(mode="json") for s in source_refs],
        dados_incompletos=[d.model_dump(mode="json") for d in incompletos],
        as_of=ctx.as_of,
    )
    repository.insert_conversa_uso(
        session,
        org_id=org_id,
        conversa_id=conversa.id,
        modelo=result.modelo,
        tokens_entrada=result.tokens_entrada,
        tokens_saida=result.tokens_saida,
        latencia_ms=latencia_ms,
    )
    _audit(
        session,
        principal,
        tipo=tipo,
        cod_ibge=cod_ibge,
        conversa_id=conversa.id,
        modelo=result.modelo,
    )

    return RespostaOut(
        conversa_id=conversa.id,
        tipo=tipo,
        ente=cod_ibge,
        ente_nome=ctx.ente_nome,
        periodo=ctx.periodo,
        as_of=ctx.as_of,
        titulo=titulo,
        pergunta=pergunta,
        resposta=texto_final,
        recusa=False,
        dado_disponivel=dado_disponivel,
        fatos=fatos_resp,
        normas=normas_resp,
        fontes=chips,
        dados_incompletos=incompletos,
        uso=UsoInfo(
            modelo=result.modelo,
            tokens_entrada=result.tokens_entrada,
            tokens_saida=result.tokens_saida,
            latencia_ms=latencia_ms,
        ),
        source_refs=source_refs,
        verificacao=VerificacaoOut(
            status=laudo.status,
            total_citados=laudo.total_citados,
            com_lastro=laudo.com_lastro,
            sem_lastro=laudo.tokens_sem_lastro(),
        ),
        gerado_em=gerado_em,
    )


def especificacoes_de_ferramenta() -> list[ToolSpec]:
    """Traduz o registro de ferramentas para a porta do provedor (sem SDK, sem regra)."""
    return [
        ToolSpec(
            nome=tool.nome,
            descricao=tool.descricao,
            parametros=schema_para_provedor(tool.schema_entrada()),
        )
        for tool in tooling.registro().todas()
    ]


def _chamar_provedor(
    session: Session,
    principal: Principal,
    provider: LLMProvider,
    request: LLMRequest,
    *,
    cod_ibge: str,
    ctx: retriever.GroundedContext,
) -> tuple[LLMResult, list[dict]]:
    """Chama o provedor — com ferramentas quando ele sabe pedi-las.

    Devolve também os **payloads** das ferramentas executadas, que são o lastro do G6:
    sem guardá-los, a verificação de saída teria de reconstituir depois o que o modelo
    recebeu — e reconstituição é exatamente o que o G7 existe para evitar.

    O executor entregue ao provedor é o **envelope**: escopo, licença, ``as_of``,
    ``source_ref`` e auditoria são aplicados dentro dele, não aqui. Um modelo que peça um
    ente fora do escopo recebe a recusa como conteúdo e segue a conversa dizendo que não
    pode acessar aquele ente — em vez de derrubar a requisição inteira ou, pior, receber
    o dado.
    """
    if not isinstance(provider, ToolCallingProvider):
        return provider.chat(request), []
    especificacoes = especificacoes_de_ferramenta()
    if not especificacoes:  # pragma: no cover - registro sempre tem ferramentas
        return provider.chat(request), []

    registro = tooling.registro()
    tool_ctx = tooling.ToolContext(session=session, principal=principal, origem="assistente")
    vistos = {f.codigo for f in ctx.fatos}
    payloads: list[dict] = []

    def executar(nome: str, argumentos: dict) -> dict:
        args = dict(argumentos or {})
        ferramenta = registro.get(nome)
        if ferramenta is not None and ferramenta.recebe_ente:
            # O ente da conversa é o default; o modelo pode nomear outro, e aí o gate de
            # escopo decide — não este código.
            args.setdefault("ente", cod_ibge)
        try:
            resultado = tooling.invoke(tool_ctx, registro, nome, args)
        except AppError as exc:
            # A recusa vira conteúdo para o modelo, mas **não** vira lastro: um 403 não
            # contém número que possa fundamentar prosa nenhuma.
            return tooling.erro_para_payload(exc)
        payloads.append(resultado.payload)
        if nome == retriever.FERRAMENTA_INDICADOR:
            fato = retriever.fato_de_ferramenta(resultado.payload)
            if fato.codigo and fato.codigo not in vistos:
                vistos.add(fato.codigo)
                ctx.fatos.append(fato)
                if fato.disponivel and fato.source_ref:
                    ctx.source_refs.append(fato.source_ref)
        return resultado.payload

    return provider.chat_com_ferramentas(request, especificacoes, executar), payloads


def _audit(
    session: Session,
    principal: Principal,
    *,
    tipo: str,
    cod_ibge: str,
    conversa_id: uuid.UUID,
    modelo: str,
) -> None:
    tenancy_repo.insert_audit_log(
        session,
        org_id=principal.org_id,
        usuario_id=principal.usuario_id,
        acao="CONSULTA_IA",
        recurso=f"assistant:{tipo};ente={cod_ibge};modelo={modelo};conversa={conversa_id}",
    )


def perguntar(
    session: Session,
    principal: Principal,
    provider: LLMProvider,
    body: PerguntaRequest,
    *,
    embedder: Embedder | None = None,
) -> RespostaOut:
    """POST /assistant/perguntar — pergunta livre fundamentada no ente + normas."""
    _org(principal)
    assert_ente_in_scope(session, principal, body.ente)
    settings = get_settings()
    emb = embedder or get_embedder()
    ctx = retriever.build_context(
        session,
        principal,
        emb,
        cod_ibge=body.ente,
        pergunta=body.pergunta,
        periodo=body.periodo,
        as_of=body.as_of,
        top_k=settings.assistant_norma_top_k,
        pagina=body.pagina,
    )
    codigos_visiveis = {f.codigo for f in ctx.fatos}
    return _run(
        session,
        principal,
        provider,
        tipo="pergunta",
        cod_ibge=body.ente,
        pergunta=body.pergunta,
        ctx=ctx,
        modelo_desejado=None,
        titulo=None,
        codigos_visiveis=codigos_visiveis,
    )


def resumo_executivo(
    session: Session,
    principal: Principal,
    provider: LLMProvider,
    body: ResumoExecutivoRequest,
    *,
    embedder: Embedder | None = None,
) -> RespostaOut:
    """POST /assistant/resumo-executivo — narrativa executiva (handoff Sprint 16)."""
    _org(principal)
    assert_ente_in_scope(session, principal, body.ente)
    settings = get_settings()
    emb = embedder or get_embedder()
    foco = body.foco or "situação fiscal geral, riscos de limites e mínimos constitucionais"
    pergunta = (
        f"Gere um resumo executivo da {foco} do ente, destacando pessoal, dívida, "
        "resultado primário e os mínimos de saúde e educação, com a fonte de cada número."
    )
    ctx = retriever.build_context(
        session,
        principal,
        emb,
        cod_ibge=body.ente,
        pergunta=pergunta,
        periodo=body.periodo,
        as_of=body.as_of,
        top_k=max(settings.assistant_norma_top_k, 4),
        todos_indicadores=True,
    )
    return _run(
        session,
        principal,
        provider,
        tipo="resumo_executivo",
        cod_ibge=body.ente,
        pergunta=pergunta,
        ctx=ctx,
        modelo_desejado=settings.assistant_summary_model,
        titulo="Resumo Executivo",
        codigos_visiveis=None,
    )


def historico(session: Session, principal: Principal, *, limit: int = 20) -> ConversasOut:
    """Histórico recente de conversas da organização (RLS por org_id)."""
    org_id = _org(principal)
    rows = repository.list_conversas(session, org_id=org_id, limit=limit)
    return ConversasOut(
        itens=[
            ConversaResumo(
                id=row.id,
                tipo=row.tipo,
                cod_ibge=row.cod_ibge,
                periodo=row.periodo,
                pergunta=row.pergunta,
                resposta=row.resposta,
                recusa=row.recusa,
                modelo=row.modelo,
                criado_em=row.criado_em,
            )
            for row in rows
        ]
    )


def uso_mensal(session: Session, principal: Principal) -> UsoResumoOut:
    """Consumo de IA da organização no mês corrente (cota 'Consultas IA/mês')."""
    org_id = _org(principal)
    agora = datetime.now(UTC)
    inicio_mes = agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    resumo = repository.usage_summary(session, org_id=org_id, desde=inicio_mes)
    return UsoResumoOut(
        mes=agora.strftime("%Y-%m"),
        consultas=resumo.consultas,
        tokens_entrada=resumo.tokens_entrada,
        tokens_saida=resumo.tokens_saida,
        gerado_em=agora,
    )
