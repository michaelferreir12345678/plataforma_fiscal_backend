"""Recuperador RAG: indicadores JÁ CALCULADOS (gold) + dispositivos normativos.

Os números **nunca** são recalculados aqui. Os indicadores do ente são obtidos
reutilizando ``reports.service.build_document`` (mesma fotografia, ``as_of``, resolução de
versão e sinalização de incompletude do relatório executivo) — DRY e coerente com a
Sprint 16. As normas vêm do *vector store* (``gold.norma_chunk``) por busca híbrida
(embedding + sobreposição lexical), abstraindo o backend (portável hoje, pgvector em prod).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError
from app.modules.assistant import repository, vectors
from app.modules.assistant.embeddings import Embedder
from app.modules.assistant.llm import FatoContexto, NormaContexto
from app.modules.indicators.rotulos import ROTULOS
from app.modules.ingestion import repository as ingestion_repo
from app.modules.reports import service as reports_service
from app.modules.reports.models import Relatorio
from app.shared import tooling

# Seções do modelo executivo → códigos de métrica emitidos por build_document.
_SECOES_EXECUTIVO = ("rcl", "pessoal", "divida", "resultado", "saude", "educacao")

# Palavras da pergunta → indicadores relevantes (para filtrar fatos e ponderar normas).
_KEYWORD_INDICADOR: dict[str, str] = {
    "pessoal": "pessoal_executivo",
    "folha": "pessoal_executivo",
    "servidor": "pessoal_executivo",
    "salario": "pessoal_executivo",
    "prudencial": "pessoal_executivo",
    "divida": "divida_consolidada_liquida",
    "endividamento": "divida_consolidada_liquida",
    "dcl": "divida_consolidada_liquida",
    "credito": "divida_consolidada_liquida",
    "rcl": "rcl",
    "corrente": "rcl",
    "resultado": "resultado_primario",
    "primario": "resultado_primario",
    "nominal": "resultado_primario",
    "superavit": "resultado_primario",
    "deficit": "resultado_primario",
    "saude": "saude_asps",
    "asps": "saude_asps",
    "educacao": "educacao_mde",
    "ensino": "educacao_mde",
    "mde": "educacao_mde",
    "fundeb": "educacao_mde",
}


@dataclass
class GroundedContext:
    ente: str
    ente_nome: str | None
    esfera: str | None
    periodo: str | None
    as_of: datetime
    fatos: list[FatoContexto] = field(default_factory=list)
    normas: list[NormaContexto] = field(default_factory=list)
    dados_incompletos: list[dict] = field(default_factory=list)
    source_refs: list[dict] = field(default_factory=list)


# Página do produto → indicadores que ela mostra (Sprint 25E). "Pergunte sobre esta
# tela" só é honesto se a tela realmente restringir o contexto recuperado.
_PAGINA_INDICADORES: dict[str, set[str]] = {
    "/pessoal": {"pessoal_executivo", "rcl"},
    "/divida": {"divida_consolidada_liquida", "rcl"},
    "/resultado": {"resultado_primario", "rcl"},
    "/saude-educacao": {"saude_asps", "educacao_mde"},
    "/receita": {"rcl"},
    "/despesa": {"pessoal_executivo", "rcl"},
    "/limites": {"pessoal_executivo", "divida_consolidada_liquida", "rcl"},
    "/caixa": {"rcl"},
}


def indicadores_da_pagina(pagina: str | None) -> set[str]:
    """Indicadores da tela de onde veio a pergunta (rota do frontend)."""
    if not pagina:
        return set()
    return set(_PAGINA_INDICADORES.get(pagina.strip().rstrip("/").lower() or "/", set()))


def indicadores_relevantes(pergunta: str, pagina: str | None = None) -> set[str]:
    """Indicadores citados na pergunta — ou os da tela atual. Vazio ⇒ visão geral.

    A pergunta manda: quem está na tela de dívida e pergunta sobre pessoal quer falar de
    pessoal. A página só entra quando a pergunta não nomeia nenhum indicador ("e isto
    aqui, está bom?"), que é justamente o caso em que o contexto da tela é a informação
    que falta.
    """
    tokens = set(vectors.tokenize(pergunta))
    alvos = {codigo for termo, codigo in _KEYWORD_INDICADOR.items() if termo in tokens}
    if not alvos:
        alvos = indicadores_da_pagina(pagina)
    # Tetos usam a RCL como denominador — traga a RCL como contexto.
    if alvos & {"pessoal_executivo", "divida_consolidada_liquida"}:
        alvos.add("rcl")
    return alvos


# Teto de chamadas de ferramenta por pergunta. Uma pergunta que nomeia sete indicadores é
# um pedido de resumo executivo, e para isso existe o outro endpoint.
MAX_FERRAMENTAS_POR_PERGUNTA = 3

#: Ferramenta usada para alcançar indicador fora do pacote fixo do relatório executivo.
FERRAMENTA_INDICADOR = "indicador_do_ente"


def indicadores_nomeados(pergunta: str) -> set[str]:
    """Indicadores que a pergunta nomeia, resolvidos pelo **registro canônico de rótulos**.

    O dicionário de palavras-chave (``_KEYWORD_INDICADOR``) cobre as seis famílias do
    relatório executivo e continua valendo — ele traduz linguagem de gestor ("folha",
    "endividamento") para código. O que falta nele é tudo o mais que a plataforma apura:
    ``garantias``, ``operacoes_credito``, ``fundeb_profissionais``, ``investimento_rcl``,
    ``rcl_per_capita`` existem em ``gold.mart_indicador``, aparecem em tela e **nunca**
    chegavam ao assistente, porque nenhuma palavra-chave os alcançava (§1 do plano).

    Aqui o alcance deixa de ser uma lista escrita à mão e passa a ser derivado de
    ``indicators.rotulos.ROTULOS``, que já é a fonte única do nome de cada indicador: um
    indicador novo entra no assistente no dia em que ganha rótulo, sem ninguém lembrar de
    editar um dicionário.

    A exigência é de **todos** os termos do código (ou do rótulo) na pergunta — "operações
    de crédito" casa ``operacoes_credito``, mas "crédito" sozinho não, para não sequestrar
    uma pergunta sobre crédito tributário.
    """
    tokens = set(vectors.tokenize(pergunta))
    if not tokens:
        return set()
    alvos: set[str] = set()
    for codigo, rotulo_legivel in ROTULOS.items():
        termos_codigo = set(vectors.tokenize(codigo.replace("_", " ")))
        termos_rotulo = set(vectors.tokenize(rotulo_legivel))
        if (termos_codigo and termos_codigo <= tokens) or (
            termos_rotulo and termos_rotulo <= tokens
        ):
            alvos.add(codigo)
    return alvos


def fato_de_ferramenta(payload: dict) -> FatoContexto:
    """Converte a saída de ``indicador_do_ente`` no contexto estruturado do provedor."""
    disponivel = bool(payload.get("disponivel"))
    valor = payload.get("valor_pct")
    if valor is None:
        valor = payload.get("valor_rs")
    return FatoContexto(
        codigo=str(payload.get("indicador") or ""),
        rotulo=str(payload.get("rotulo") or ""),
        valor_formatado=str(payload.get("valor_formatado") or "Dado não disponível"),
        unidade=str(payload.get("unidade") or ""),
        status="calculado" if disponivel else "dado_incompleto",
        disponivel=disponivel,
        periodo=str(payload.get("periodo") or ""),
        # Sem número não há fonte a exibir — e um chip de fonte para um valor que não
        # existe seria rastreabilidade de mentira.
        source_ref=(payload.get("source_ref") or {}) if disponivel else {},
        as_of=payload.get("as_of"),
        faixa=payload.get("faixa"),
        valor=str(valor) if disponivel and valor is not None else None,
        memoria=payload.get("memoria") or {},
    )


def fatos_por_ferramenta(
    session: Session,
    principal: Principal,
    *,
    cod_ibge: str,
    codigos: Sequence[str],
    periodo: str | None,
    as_of: datetime | None,
    conversa_id: uuid.UUID | None = None,
) -> tuple[list[FatoContexto], list[dict]]:
    """Busca indicadores **pela camada de ferramentas** — com escopo, fonte e auditoria.

    Chamar o envelope (e não o repositório direto) é o ponto: mesmo neste caminho
    determinístico, a leitura passa por ``assert_ente_in_scope``, exige ``source_ref`` na
    saída e vira linha em ``op.ia_tool_call``. Quando o Gemini decidir sozinho o que
    chamar, será exatamente o mesmo código executando as mesmas verificações.
    """
    fatos: list[FatoContexto] = []
    incompletos: list[dict] = []
    ctx = tooling.ToolContext(
        session=session, principal=principal, origem="assistente", conversa_id=conversa_id
    )
    for codigo in list(codigos)[:MAX_FERRAMENTAS_POR_PERGUNTA]:
        try:
            resultado = tooling.invoke(
                ctx,
                tooling.registro(),
                FERRAMENTA_INDICADOR,
                {"ente": cod_ibge, "indicador": codigo, "periodo": periodo, "as_of": as_of},
            )
        except AppError as exc:
            # A recusa da ferramenta não derruba a pergunta: ela vira ausência declarada.
            # (O 403 de escopo/licença já foi aplicado antes, na borda; aqui sobram os
            # casos de dado — esfera desconhecida, por exemplo.)
            incompletos.append(
                {
                    "tipo": "indisponivel",
                    "codigo": codigo,
                    "mensagem": exc.detail or exc.title,
                    "periodo_esperado": periodo,
                    "periodo_encontrado": None,
                }
            )
            continue
        fato = fato_de_ferramenta(resultado.payload)
        fatos.append(fato)
        if not fato.disponivel:
            incompletos.append(
                {
                    "tipo": "ausente",
                    "codigo": codigo,
                    "mensagem": str(
                        resultado.payload.get("observacao")
                        or f"{fato.rotulo} não está materializado."
                    ),
                    "periodo_esperado": periodo or fato.periodo or None,
                    "periodo_encontrado": None,
                }
            )
    return fatos, incompletos


def _metric_to_fato(metric: dict) -> FatoContexto:
    sources = metric.get("source_refs") or []
    primary = sources[0] if sources else {}
    disponivel = metric.get("valor") is not None and metric.get("status") != "dado_incompleto"
    return FatoContexto(
        codigo=metric["codigo"],
        rotulo=metric["rotulo"],
        valor_formatado=metric.get("valor_formatado") or "Dado não disponível",
        unidade=metric.get("unidade") or "",
        status=metric.get("status") or "",
        disponivel=disponivel,
        periodo=metric.get("periodo") or "",
        source_ref=primary,
        as_of=metric.get("as_of"),
        faixa=metric.get("faixa"),
        valor=metric.get("valor"),
        memoria=metric.get("memoria") or {},
    )


def _resolve_periodo(
    session: Session, *, cod_ibge: str, periodo: str | None, as_of: datetime | None
) -> str | None:
    if periodo:
        return periodo
    latest = ingestion_repo.resolve_latest_entrega(
        session, cod_ibge=cod_ibge, relatorio="RREO", as_of=as_of
    )
    return latest.periodo if latest else None


def retrieve_indicadores(
    session: Session,
    principal: Principal,
    *,
    cod_ibge: str,
    periodo: str,
    as_of: datetime,
) -> tuple[list[FatoContexto], list[dict], dict]:
    """Fatos calculados do ente via build_document (transitório, não persistido)."""
    assert principal.org_id is not None
    row = Relatorio(
        org_id=principal.org_id,
        lote_id=uuid.uuid4(),
        modelo="executivo",
        modelo_versao="v1",
        formato="pdf",
        escopo="ente",
        cod_ibge=cod_ibge,
        periodo=periodo,
        as_of=as_of,
        status="processando",
        progresso=0,
        parametros={"secoes": list(_SECOES_EXECUTIVO)},
        cabecalho={},
        source_refs=[],
        memoria={},
        dados_incompletos=[],
        criado_por=principal.usuario_id,
    )
    document = reports_service.build_document(session, row, datetime.now(UTC))
    fatos = [_metric_to_fato(m) for m in document.get("metricas", [])]
    cabecalho = document.get("cabecalho") or {}
    header = {
        "ente_nome": cabecalho.get("ente"),
        "esfera": cabecalho.get("esfera"),
        "source_refs": document.get("source_refs") or [],
        "dados_incompletos": document.get("dados_incompletos") or [],
    }
    return fatos, document.get("source_refs") or [], header


def retrieve_normas(
    session: Session,
    embedder: Embedder,
    *,
    pergunta: str,
    indicadores: set[str],
    top_k: int,
) -> list[NormaContexto]:
    """Busca híbrida no *vector store*: cosseno do embedding + sobreposição lexical."""
    chunks = repository.list_norma_chunks(session)
    if not chunks:
        return []
    query_vec = embedder.embed([pergunta])[0]
    ranked: list[tuple[float, NormaContexto]] = []
    for chunk in chunks:
        emb_score = 0.0
        if (
            chunk.embedding
            and chunk.modelo_embedding == embedder.model_id
            and chunk.dim == embedder.dim
        ):
            emb_score = vectors.cosine(query_vec, list(chunk.embedding))
        corpo = " ".join(
            [chunk.dispositivo, chunk.titulo or "", chunk.texto, " ".join(chunk.tags or [])]
        )
        lex = vectors.lexical_overlap(pergunta, corpo)
        boost = 0.2 if indicadores & set(chunk.indicadores or []) else 0.0
        score = 0.6 * emb_score + 0.4 * lex + boost
        if score <= 0.02:
            continue
        ranked.append(
            (
                score,
                NormaContexto(
                    fonte=chunk.fonte,
                    dispositivo=chunk.dispositivo,
                    texto=chunk.texto,
                    titulo=chunk.titulo,
                    score=round(score, 4),
                ),
            )
        )
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [norma for _, norma in ranked[:top_k]]


def build_context(
    session: Session,
    principal: Principal,
    embedder: Embedder,
    *,
    cod_ibge: str,
    pergunta: str,
    periodo: str | None,
    as_of: datetime | None,
    top_k: int,
    todos_indicadores: bool = False,
    pagina: str | None = None,
) -> GroundedContext:
    """Monta o contexto fundamentado (fatos + normas + incompletudes) para o serviço."""
    effective_as_of = as_of or datetime.now(UTC)
    resolved_periodo = _resolve_periodo(
        session, cod_ibge=cod_ibge, periodo=periodo, as_of=as_of
    )
    ctx = GroundedContext(
        ente=cod_ibge,
        ente_nome=None,
        esfera=None,
        periodo=resolved_periodo,
        as_of=effective_as_of,
    )

    alvos = indicadores_relevantes(pergunta, pagina)
    if resolved_periodo is not None:
        fatos, source_refs, header = retrieve_indicadores(
            session, principal, cod_ibge=cod_ibge, periodo=resolved_periodo, as_of=effective_as_of
        )
        ctx.ente_nome = header["ente_nome"]
        ctx.esfera = header["esfera"]
        ctx.source_refs = source_refs
        ctx.dados_incompletos = header["dados_incompletos"]
        if todos_indicadores or not alvos:
            ctx.fatos = fatos
        else:
            ctx.fatos = [f for f in fatos if f.codigo in alvos]
            if not ctx.fatos:  # nenhum casou o filtro — não deixe o gestor sem contexto
                ctx.fatos = fatos

        # Sprint IA-1a: o que a pergunta nomeia e o relatório executivo não emite é
        # buscado **pela camada de ferramentas**. É o que tira `garantias` e companhia da
        # invisibilidade — sem alargar o pacote fixo, que continua sendo o mesmo.
        if not todos_indicadores:
            ja_no_contexto = {f.codigo for f in ctx.fatos}
            pedidos = [c for c in sorted(indicadores_nomeados(pergunta)) if c not in ja_no_contexto]
            if pedidos:
                extras, incompletos = fatos_por_ferramenta(
                    session,
                    principal,
                    cod_ibge=cod_ibge,
                    codigos=pedidos,
                    periodo=resolved_periodo,
                    as_of=as_of,
                )
                ctx.fatos.extend(extras)
                ctx.dados_incompletos = list(ctx.dados_incompletos) + incompletos
                ctx.source_refs = list(ctx.source_refs) + [
                    f.source_ref for f in extras if f.disponivel and f.source_ref
                ]

    ctx.normas = retrieve_normas(
        session, embedder, pergunta=pergunta, indicadores=alvos, top_k=top_k
    )
    return ctx
