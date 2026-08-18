"""IA nas telas (Sprint IA-5): quatro capacidades, uma orquestração, zero caminho novo.

Cada capacidade responde a **uma pergunta declarada** (a da ficha), e responde sempre pelo
mesmo caminho:

1. chama as ferramentas do registro pelo envelope de ``shared/tooling`` — é lá que moram
   escopo, licença, ``as_of``, ``source_ref`` e auditoria (G2/G4/G5/G7, lição A22/E1);
2. se as ferramentas não devolveram dado, a resposta é a **ausência declarada** e o modelo
   **não é chamado** (G3 — a recusa honesta da Sprint 17 é herdada, não reescrita);
3. com dado, monta ``LLMRequest`` (fatos com fonte + notas apuradas + verbetes do
   dicionário) e pede a prosa pela porta ``LLMProvider``;
4. verifica a saída contra o lastro daquela chamada (G6) e anexa o aviso quando algum
   número não se sustenta;
5. registra consumo (``op.conversa_uso``) e trilha (``op.audit_log``).

**Por que a orquestração é determinística e não um laço de agente.** O laço existe e é o
certo para pergunta aberta (``assistant/agente.py``). Aqui a pergunta é fixa — "explique
*este* número", "narre *este* relatório" —, então deixar o modelo escolher as ferramentas
custaria mais tokens para chegar à mesma cadeia, com a diferença de que a cadeia deixaria
de ser previsível. Uma superfície de tela precisa ter custo e latência estáveis, e o
gestor precisa poder conferir que a explicação olhou exatamente o que a tela mostra.

**Por que não grava ``op.conversa``.** Isto não é conversa: não tem histórico, não tem
turno seguinte e não pertence ao Assistente. O consumo entra em ``op.conversa_uso`` (que é
o que a cota e a cobrança contam) e a cadeia de ferramentas em ``op.ia_tool_call`` — que é,
pela própria ficha, o instrumento com que se mede se a superfície é usada.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError
from app.modules.alerts import rules as alert_rules
from app.modules.assistant import didatica, retriever
from app.modules.assistant import repository as assistant_repo
from app.modules.assistant.llm import (
    FatoContexto,
    LLMProvider,
    LLMRequest,
    NotaContexto,
    VerbeteContexto,
)
from app.modules.assistant.schemas import (
    DadoIncompleto,
    FatoResposta,
    FonteChip,
    UsoInfo,
    VerificacaoOut,
)
from app.modules.assistant.service import SYSTEM_PROMPT, ente_label, fato_para_resposta
from app.modules.coverage.service import INDICADORES_POR_PAGINA, fontes_por_pagina
from app.modules.indicators import rotulos
from app.modules.insights.schemas import (
    CentralDadosRequest,
    ExplicarAlertasRequest,
    ExplicarNumeroRequest,
    InsightOut,
    NarrarRelatorioRequest,
    NotaInsight,
)
from app.modules.reports.service import formatar_valor
from app.modules.tenancy import repository as tenancy_repo
from app.shared import tooling
from app.shared.source_ref import SourceRef
from app.shared.tooling import verificacao
from app.shared.tooling.telas import STATUS_NAO_ENTREGUE

logger = logging.getLogger(__name__)

#: Origem gravada em ``op.ia_tool_call`` — distingue a tela do assistente e do MCP.
ORIGEM = "tela"

ACAO_AUDITORIA = "INSIGHT_IA"

#: Teto de obrigações listadas na nota do calendário — a nota orienta, não é o calendário.
_MAX_OBRIGACOES = 8

#: Instrução específica de cada superfície, somada às seis regras invioláveis do §9.
_INSTRUCAO: dict[str, str] = {
    "explicar_numero": (
        "Sua tarefa é explicar UM indicador que o gestor está vendo na tela: o que ele "
        "mede, como foi apurado, de onde vêm os dados, o que a norma exige e o que "
        "mudaria a faixa. Use apenas os números já apurados acima — não recalcule, não "
        "projete e não some nada."
    ),
    "explicar_alertas": (
        "Sua tarefa é explicar uma fila de alertas JÁ ORDENADA pela plataforma. A ordem é "
        "regra determinística e auditável: NÃO a reordene, não sugira outra prioridade e "
        "não invente severidade. Explique por que o primeiro item é o primeiro, qual é a "
        "providência legal aplicável e o que muda se ele não for tratado."
    ),
    "narrar_relatorio": (
        "Sua tarefa é escrever a narrativa executiva de um relatório JÁ MONTADO. Use "
        "exatamente os mesmos números e as mesmas fontes listadas acima — nenhum número "
        "novo, nenhuma soma, nenhuma comparação com dado que não esteja aqui. Itens "
        "ausentes devem aparecer como ausentes."
    ),
    "central_dados": (
        "Sua tarefa é explicar, em linguagem de operação, por que uma página tem ou não "
        "tem dado para este ente: o que a cobertura mostra, o que a qualidade acusou e o "
        "que o calendário de obrigações diz sobre o prazo. Diga de quem é a lacuna (do "
        "ente que não entregou, da nossa carga, ou de prazo que ainda não venceu) apenas "
        "quando o contexto acima permitir concluir."
    ),
}

_TITULO: dict[str, str] = {
    "explicar_numero": "Explique este número",
    "explicar_alertas": "Por que este alerta é o primeiro",
    "narrar_relatorio": "Narrativa do relatório",
    "central_dados": "Busca na Central de Dados",
}

#: Vocabulário de negócio → página da Central de Dados. Só roteia a pergunta; a resposta
#: continua vindo de cobertura/qualidade/calendário.
_PALAVRA_PAGINA: dict[str, str] = {
    "saude": "saude-educacao",
    "asps": "saude-educacao",
    "educacao": "saude-educacao",
    "ensino": "saude-educacao",
    "mde": "saude-educacao",
    "fundeb": "saude-educacao",
    "divida": "divida",
    "endividamento": "divida",
    "dcl": "divida",
    "limite": "limites",
    "limites": "limites",
    "pessoal": "limites",
    "folha": "limites",
    "prudencial": "limites",
    "resultado": "resultado",
    "primario": "resultado",
    "benchmark": "benchmarking",
    "benchmarking": "benchmarking",
    "coorte": "benchmarking",
    "comparacao": "benchmarking",
    "carteira": "carteira",
    "dashboard": "dashboard",
    "painel": "dashboard",
    "cockpit": "dashboard",
}


def _org(principal: Principal) -> uuid.UUID:
    if principal.org_id is None:
        raise AppError(status=403, title="Sem organização", detail="Requer organização ativa.")
    return principal.org_id


def _decimal(valor: Any) -> Decimal | None:
    """O payload viaja em JSON: ``Decimal`` chega como string. Converte sem inventar."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


def _pct(valor: Any) -> str | None:
    numero = _decimal(valor)
    return None if numero is None else formatar_valor(numero, "PERCENTUAL")


def _source_ref(bruto: Any) -> SourceRef | None:
    if not isinstance(bruto, dict) or not bruto.get("relatorio"):
        return None
    return SourceRef(
        relatorio=str(bruto["relatorio"]),
        anexo=bruto.get("anexo"),
        periodo=bruto.get("periodo"),
        versao_entrega=bruto.get("versao_entrega"),
    )


@dataclass
class _Coleta:
    """O que as ferramentas devolveram nesta chamada — lastro, fonte e trilha."""

    payloads: list[dict[str, Any]] = field(default_factory=list)
    ferramentas: list[str] = field(default_factory=list)
    fatos: list[FatoContexto] = field(default_factory=list)
    notas: list[NotaContexto] = field(default_factory=list)
    incompletos: list[DadoIncompleto] = field(default_factory=list)

    def nota(self, titulo: str, linhas: list[str | None], *, origem: str) -> None:
        """Registra uma nota, descartando linhas vazias — nota vazia não vira ruído."""
        conteudo = tuple(linha for linha in linhas if linha)
        if conteudo:
            self.notas.append(NotaContexto(titulo=titulo, linhas=conteudo, origem=origem))

    def source_refs(self) -> list[SourceRef]:
        """Fontes de tudo que as ferramentas devolveram — a varredura do envelope."""
        vistos: dict[tuple, SourceRef] = {}
        for payload in self.payloads:
            for bruto in tooling.fontes_do_payload(payload):
                ref = _source_ref(bruto)
                if ref is not None:
                    vistos[(ref.relatorio, ref.anexo, ref.periodo, ref.versao_entrega)] = ref
        return list(vistos.values())


def _chamar(
    coleta: _Coleta, ctx: tooling.ToolContext, nome: str, argumentos: dict[str, Any]
) -> dict[str, Any]:
    """Executa uma ferramenta pelo envelope e guarda o payload como lastro.

    Sem ``try``: um 403 de escopo ou de licença **tem** de chegar à tela como 403 (RFC
    7807), não virar um parágrafo dizendo que não foi possível. Quem pediu a explicação
    pediu de um ente específico; responder outra coisa seria pior que recusar.
    """
    resultado = tooling.invoke(ctx, tooling.registro(), nome, argumentos)
    coleta.payloads.append(resultado.payload)
    coleta.ferramentas.append(nome)
    return resultado.payload


def _contexto(session: Session, principal: Principal, capacidade: str) -> tooling.ToolContext:
    return tooling.ToolContext(
        session=session, principal=principal, origem=ORIGEM, origem_ref=capacidade
    )


def _chips(fatos: list[FatoResposta], refs: list[SourceRef]) -> list[FonteChip]:
    """Chips de fonte: um por indicador usado, mais as fontes que só as notas trouxeram."""
    chips: list[FonteChip] = []
    usados: set[tuple] = set()
    for fato in fatos:
        if not fato.disponivel or fato.source_ref is None:
            continue
        chave = (
            fato.source_ref.relatorio,
            fato.source_ref.anexo,
            fato.source_ref.periodo,
            fato.source_ref.versao_entrega,
        )
        usados.add(chave)
        chips.append(
            FonteChip(
                tipo="indicador",
                rotulo=fato.rotulo,
                detalhe=f"{fato.periodo} · {fato.valor_formatado}".strip(" ·"),
                source_ref=fato.source_ref,
            )
        )
    for ref in refs:
        chave = (ref.relatorio, ref.anexo, ref.periodo, ref.versao_entrega)
        if chave in usados:
            continue
        usados.add(chave)
        chips.append(
            FonteChip(
                tipo="entrega",
                rotulo=ref.relatorio,
                detalhe=" · ".join(
                    parte
                    for parte in (ref.anexo, ref.periodo, ref.versao_entrega)
                    if parte
                )
                or None,
                source_ref=ref,
            )
        )
    return chips


def _ausencia(
    *,
    capacidade: str,
    ente: str,
    ente_nome: str | None,
    periodo: str | None,
    as_of: datetime | None,
    pergunta: str,
    motivo: str,
    coleta: _Coleta,
    session: Session,
    principal: Principal,
) -> InsightOut:
    """Ausência declarada: o texto é a explicação da falta, e o modelo não é acionado (G3)."""
    _auditar(session, principal, capacidade=capacidade, ente=ente, modelo="ausencia")
    return InsightOut(
        capacidade=capacidade,
        titulo=_TITULO[capacidade],
        ente=ente,
        ente_nome=ente_nome,
        periodo=periodo,
        as_of=as_of,
        pergunta=pergunta,
        resposta=motivo,
        disponivel=False,
        ausencia=motivo,
        fatos=[fato_para_resposta(f) for f in coleta.fatos],
        notas=[
            NotaInsight(titulo=n.titulo, linhas=list(n.linhas), origem=n.origem)
            for n in coleta.notas
        ],
        fontes=[],
        source_refs=coleta.source_refs(),
        dados_incompletos=coleta.incompletos,
        ferramentas=coleta.ferramentas,
        uso=UsoInfo(modelo="n/a", tokens_entrada=0, tokens_saida=0, latencia_ms=0),
        verificacao=None,
        gerado_em=datetime.now(UTC),
    )


def _responder(
    session: Session,
    principal: Principal,
    provider: LLMProvider,
    *,
    capacidade: str,
    ente: str,
    ente_nome: str | None,
    esfera: str | None,
    periodo: str | None,
    as_of: datetime | None,
    pergunta: str,
    coleta: _Coleta,
    verbetes: list[VerbeteContexto] | None = None,
) -> InsightOut:
    """Compõe a prosa pela porta, verifica (G6), registra consumo e devolve a resposta."""
    org_id = _org(principal)
    verbetes = verbetes or []
    request = LLMRequest(
        system=f"{SYSTEM_PROMPT}\n\n{_INSTRUCAO[capacidade]}",
        pergunta=pergunta,
        ente_label=ente_label(ente_nome, ente, esfera),
        periodo=periodo,
        fatos=tuple(coleta.fatos),
        notas=tuple(coleta.notas),
        verbetes=tuple(verbetes),
    )
    inicio = time.perf_counter()
    # LLMProviderError propaga como RFC 7807 (§9): falha de provedor nunca vira resposta
    # sem fonte.
    resultado = provider.chat(request)
    latencia_ms = int((time.perf_counter() - inicio) * 1000)

    fatos_resp = [fato_para_resposta(f) for f in coleta.fatos]
    # Mesmo fecho didático do assistente, e pela mesma razão: esta é a superfície que a
    # IA-7 levou a doze telas, com o mesmo prompt e o mesmo provedor. Uma garantia que
    # valesse só em ``/assistant`` consertaria a tela menos usada e não as mais usadas.
    # Roda antes do G6 para que o texto conferido seja o texto lido; nenhuma das duas
    # reescritas introduz número (verificado: nenhuma expansão da tabela de siglas contém
    # algarismo), então o lastro conferido é o mesmo.
    texto_fechado = didatica.fechar_resposta(resultado.texto)
    laudo = verificacao.verificar(
        texto_fechado,
        coleta.payloads,
        [f.model_dump(mode="json") for f in fatos_resp],
        [list(n.linhas) for n in coleta.notas],
        [v.__dict__ for v in verbetes],
    )
    texto = verificacao.anexar_aviso(texto_fechado, laudo)
    if not laudo.ok:
        logger.warning(
            "G6 sinalizou %s número(s) sem lastro em %s (ente %s): %s",
            len(laudo.sem_lastro),
            capacidade,
            ente,
            laudo.tokens_sem_lastro(),
        )

    assistant_repo.insert_conversa_uso(
        session,
        org_id=org_id,
        # Não há conversa: a IA de tela não tem histórico nem turno seguinte. O consumo,
        # porém, é consumo — e é por ``op.conversa_uso`` que a cota e a fatura o contam.
        conversa_id=None,
        modelo=resultado.modelo,
        tokens_entrada=resultado.tokens_entrada,
        tokens_saida=resultado.tokens_saida,
        latencia_ms=latencia_ms,
    )
    _auditar(session, principal, capacidade=capacidade, ente=ente, modelo=resultado.modelo)

    refs = coleta.source_refs()
    return InsightOut(
        capacidade=capacidade,
        titulo=_TITULO[capacidade],
        ente=ente,
        ente_nome=ente_nome,
        periodo=periodo,
        as_of=as_of,
        pergunta=pergunta,
        resposta=texto,
        disponivel=True,
        ausencia=None,
        fatos=fatos_resp,
        notas=[
            NotaInsight(titulo=n.titulo, linhas=list(n.linhas), origem=n.origem)
            for n in coleta.notas
        ],
        fontes=_chips(fatos_resp, refs),
        source_refs=refs,
        dados_incompletos=coleta.incompletos,
        ferramentas=coleta.ferramentas,
        uso=UsoInfo(
            modelo=resultado.modelo,
            tokens_entrada=resultado.tokens_entrada,
            tokens_saida=resultado.tokens_saida,
            latencia_ms=latencia_ms,
        ),
        verificacao=VerificacaoOut(
            status=laudo.status,
            total_citados=laudo.total_citados,
            com_lastro=laudo.com_lastro,
            sem_lastro=laudo.tokens_sem_lastro(),
        ),
        gerado_em=datetime.now(UTC),
    )


def _auditar(
    session: Session, principal: Principal, *, capacidade: str, ente: str, modelo: str
) -> None:
    tenancy_repo.insert_audit_log(
        session,
        org_id=principal.org_id,
        usuario_id=principal.usuario_id,
        acao=ACAO_AUDITORIA,
        recurso=f"insight:{capacidade};ente={ente};modelo={modelo}",
    )


# --------------------------------------------------------------------------- #
# 1. "Explique este número"
# --------------------------------------------------------------------------- #
def explicar_numero(
    session: Session,
    principal: Principal,
    provider: LLMProvider,
    body: ExplicarNumeroRequest,
) -> InsightOut:
    """Linhagem + memória de cálculo + base legal + o que mudaria a faixa (§4, item 1).

    Os quatro pedaços já existiam e nenhum deles era legível junto: a linhagem estava no
    grafo da Sprint 26, a memória no detalhe do Monitor de Limites, a base legal no
    dicionário da IA-2 e a distância até a faixa na lista de limites. A capacidade não
    apura nada — costura.
    """
    _org(principal)
    capacidade = "explicar_numero"
    ctx = _contexto(session, principal, capacidade)
    coleta = _Coleta()
    argumentos = {
        "ente": body.ente,
        "indicador": body.indicador,
        "periodo": body.periodo,
        "as_of": body.as_of,
    }
    indicador = _chamar(coleta, ctx, "indicador_do_ente", argumentos)
    rotulo = str(indicador.get("rotulo") or rotulos.rotulo(body.indicador))
    periodo = indicador.get("periodo") or body.periodo
    ente_nome = indicador.get("ente_nome")
    pergunta = (
        f"Explique o indicador {rotulo} do ente {body.ente}"
        + (f" no período {periodo}" if periodo else "")
        + ": o que ele mede, como foi apurado, de onde vêm os dados, qual a base legal e "
        "o que mudaria a faixa."
    )

    if not indicador.get("disponivel"):
        motivo = str(
            indicador.get("observacao")
            or f"{rotulo} não está materializado para {body.ente}."
        )
        coleta.incompletos.append(
            DadoIncompleto(
                tipo="ausente",
                codigo=body.indicador,
                mensagem=motivo,
                periodo_esperado=periodo,
                periodo_encontrado=None,
            )
        )
        return _ausencia(
            capacidade=capacidade,
            ente=body.ente,
            ente_nome=ente_nome,
            periodo=periodo,
            as_of=body.as_of,
            pergunta=pergunta,
            motivo=(
                f"{motivo} Sem valor apurado não há o que explicar: a plataforma não "
                "estima indicador. Verifique a entrega no SICONFI ou consulte a cobertura "
                "da fonte na Central de Dados."
            ),
            coleta=coleta,
            session=session,
            principal=principal,
        )

    coleta.fatos.append(retriever.fato_de_ferramenta(indicador))

    memoria = indicador.get("memoria") or {}
    coleta.nota(
        "Memória de cálculo",
        [
            f"Fórmula: {memoria['formula']}" if memoria.get("formula") else None,
            f"Valor apurado: {indicador.get('valor_formatado')}",
            (
                f"Denominador declarado: {indicador.get('denominador')}"
                if indicador.get("denominador")
                else None
            ),
            (
                "Base de cálculo (R$): "
                + formatar_valor(_decimal(indicador.get("base_valor")), "BRL")
                if indicador.get("base_valor") is not None
                else None
            ),
            (
                f"RCL de 12 meses usada: {formatar_valor(_decimal(memoria.get('rcl_12m')), 'BRL')}"
                if memoria.get("rcl_12m")
                else None
            ),
            f"Entrega que sustenta o número: versão {indicador.get('versao_entrega')}",
        ],
        origem="indicador_do_ente",
    )

    coleta.nota(
        f"Providência legal da faixa '{indicador.get('faixa') or 'sem faixa'}'",
        _linhas_de_providencia(indicador.get("providencias"))
        or [
            "Não há providência legal registrada para esta faixa — o indicador é "
            "gerencial ou está em faixa que a norma não exige agir."
        ],
        origem="gold.dim_providencia_legal",
    )

    limites = _chamar(
        coleta,
        ctx,
        "limites_do_ente",
        {"ente": body.ente, "periodo": periodo, "as_of": body.as_of},
    )
    item = next(
        (i for i in limites.get("itens") or [] if i.get("indicador") == body.indicador),
        None,
    )
    coleta.nota("O que mudaria a faixa", _linhas_da_faixa(item), origem="limites_do_ente")

    linhagem = _chamar(coleta, ctx, "linhagem_do_indicador", {"indicador": body.indicador})
    coleta.nota(
        "De onde vem este número (linhagem)",
        [
            (
                "Fontes de origem: " + ", ".join(linhagem.get("fontes_de_origem") or [])
                if linhagem.get("fontes_de_origem")
                else None
            ),
            (
                "Tabelas percorridas: " + ", ".join(linhagem.get("tabelas_montante") or [])
                if linhagem.get("tabelas_montante")
                else None
            ),
            f"Materializado em: {linhagem.get('tabela_gold')}",
            (
                "Páginas que dependem dele: "
                + ", ".join(linhagem.get("paginas_afetadas") or [])
                if linhagem.get("paginas_afetadas")
                else None
            ),
        ],
        origem="gold.lineage_edge",
    )

    verbetes = retriever.retrieve_verbetes(
        session, pergunta=f"{rotulo} {body.indicador}", codigos={body.indicador}
    )
    return _responder(
        session,
        principal,
        provider,
        capacidade=capacidade,
        ente=body.ente,
        ente_nome=ente_nome,
        esfera=indicador.get("esfera"),
        periodo=periodo,
        as_of=body.as_of,
        pergunta=pergunta,
        coleta=coleta,
        verbetes=verbetes,
    )


def _linhas_de_providencia(providencias: Any) -> list[str | None]:
    """A providência legal como está em ``gold.dim_providencia_legal`` — texto + base."""
    if not isinstance(providencias, list):
        return []
    linhas: list[str | None] = []
    for p in providencias:
        if not isinstance(p, dict):  # pragma: no cover - o contrato da ferramenta é tipado
            continue
        base = p.get("base_legal")
        linhas.append(f"{p.get('texto')}" + (f" (base legal: {base})" if base else ""))
    return linhas


def _linhas_da_faixa(item: dict[str, Any] | None) -> list[str | None]:
    """O que separa o indicador da próxima faixa — **sem** conta nova.

    Distância ao teto e ao gatilho de alerta já são calculadas por ``limits`` (que é a
    fonte única, §7); aqui elas só ganham frase. O sentido inverte para os mínimos: no
    piso a distância é folga acima do mínimo, e chamá-la de "distância até o teto" seria
    dizer ao gestor o contrário do que a norma exige.
    """
    if item is None:
        return [
            "Este indicador não tem limite legal em gold.dim_limite_legal — é gerencial, "
            "e por isso não tem faixa a mudar. Compare-o na coorte (Benchmarking) em vez "
            "de lê-lo contra um teto."
        ]
    sentido = str(item.get("sentido") or "teto")
    faixa = item.get("faixa") or "sem faixa"
    teto = _pct(item.get("teto_pct"))
    alerta = _pct(item.get("alerta_pct"))
    prudencial = _pct(item.get("prudencial_pct"))
    distancia_teto = _pct(item.get("distancia_teto"))
    distancia_alerta = _pct(item.get("distancia_alerta"))
    valor = item.get("valor_formatado")
    if sentido == "piso":
        return [
            f"Situação atual: {faixa}; aplicado {valor} sobre {item.get('denominador')}.",
            f"Mínimo exigido: {teto}." if teto else None,
            (
                f"Folga acima do mínimo: {distancia_teto} ponto(s) percentual(is) — "
                "abaixo do mínimo a faixa vira 'insuficiente'."
                if distancia_teto
                else None
            ),
            "Semântica invertida: aqui ficar abaixo é o problema, não ficar acima.",
        ]
    return [
        f"Situação atual: {faixa}; apurado {valor} sobre {item.get('denominador')}.",
        f"Teto legal: {teto}." if teto else None,
        (
            f"Gatilho de alerta: {alerta}"
            + (f" (faltam {distancia_alerta} p.p.)" if distancia_alerta else "")
            + "."
            if alerta
            else None
        ),
        f"Gatilho prudencial: {prudencial}." if prudencial else None,
        (
            f"Distância até o teto: {distancia_teto} ponto(s) percentual(is)."
            if distancia_teto
            else None
        ),
    ]


# --------------------------------------------------------------------------- #
# 2. Explicação da fila de alertas (a ordenação continua determinística)
# --------------------------------------------------------------------------- #
def explicar_alertas(
    session: Session,
    principal: Principal,
    provider: LLMProvider,
    body: ExplicarAlertasRequest,
) -> InsightOut:
    """Explica **a ordem que a regra produziu** — nunca produz ordem (§8 do plano).

    ``alerts/rules.py::prioridade`` continua sendo quem ordena, e ``criterio_ordenacao``
    devolve a regra em texto derivada dos próprios pesos. O modelo recebe a fila pronta e
    a regra que a produziu; o que ele acrescenta é a leitura para quem vai agir.
    """
    _org(principal)
    capacidade = "explicar_alertas"
    ctx = _contexto(session, principal, capacidade)
    coleta = _Coleta()
    fila = _chamar(
        coleta, ctx, "alertas_do_ente", {"ente": body.ente, "as_of": body.as_of}
    )
    alertas = fila.get("alertas") or []
    pergunta = (
        f"Explique a fila de alertas do ente {body.ente}: por que o primeiro item é o "
        "primeiro, qual a providência legal aplicável e o que muda se ele não for tratado."
    )
    if not alertas:
        return _ausencia(
            capacidade=capacidade,
            ente=body.ente,
            ente_nome=None,
            periodo=None,
            as_of=body.as_of,
            pergunta=pergunta,
            motivo=str(
                fila.get("observacao")
                or f"Nenhum alerta ativo para {body.ente}; não há fila a explicar."
            ),
            coleta=coleta,
            session=session,
            principal=principal,
        )

    primeiro = alertas[0]
    contadores = fila.get("contadores") or {}
    posicao = alert_rules.explicar_posicao(
        str(primeiro.get("severidade") or ""), str(primeiro.get("categoria") or "")
    )
    coleta.nota(
        "Como a fila foi ordenada",
        [*alert_rules.criterio_ordenacao()],
        origem="alerts/rules.py::prioridade",
    )
    coleta.nota(
        "O primeiro da fila",
        [
            f"Título: {primeiro.get('titulo')}",
            f"Posição justificada por: {posicao}",
            f"Motivo legal registrado: {primeiro.get('motivo_legal')}",
            f"Ação sugerida pelo motor: {primeiro.get('acao_sugerida')}",
            f"Prazo: {primeiro.get('prazo')}" if primeiro.get("prazo") else None,
            (
                f"Indicador associado: {rotulos.rotulo(str(primeiro.get('indicador')))} "
                f"({primeiro.get('periodo') or 'período não declarado'})"
                if primeiro.get("indicador")
                else None
            ),
        ],
        origem="alertas_do_ente",
    )
    coleta.nota(
        "Fila completa, na ordem da regra",
        [
            f"{indice}. [{a.get('severidade')}/{a.get('categoria')}] {a.get('titulo')}"
            for indice, a in enumerate(alertas, start=1)
        ],
        origem="alertas_do_ente",
    )
    coleta.nota(
        "Composição da fila",
        [
            f"Críticos: {contadores.get('critico', 0)}; atenção: "
            f"{contadores.get('atencao', 0)}; informativos: "
            f"{contadores.get('informativo', 0)}; total: {contadores.get('total', 0)}."
        ],
        origem="alertas_do_ente",
    )

    periodo = primeiro.get("periodo")
    ente_nome = None
    indicador_codigo = primeiro.get("indicador")
    if indicador_codigo:
        # A providência legal canônica é a de ``gold.dim_providencia_legal``, e quem a lê
        # é a ferramenta de indicador — não este módulo.
        indicador = _chamar(
            coleta,
            ctx,
            "indicador_do_ente",
            {
                "ente": body.ente,
                "indicador": str(indicador_codigo),
                "periodo": periodo,
                "as_of": body.as_of,
            },
        )
        ente_nome = indicador.get("ente_nome")
        if indicador.get("disponivel"):
            coleta.fatos.append(retriever.fato_de_ferramenta(indicador))
        coleta.nota(
            "Providência legal do indicador que disparou o alerta",
            [
                f"{p.get('texto')}"
                + (f" (base legal: {p.get('base_legal')})" if p.get("base_legal") else "")
                for p in indicador.get("providencias") or []
            ]
            or [
                "Sem providência registrada para a faixa atual deste indicador em "
                "gold.dim_providencia_legal."
            ],
            origem="gold.dim_providencia_legal",
        )

    return _responder(
        session,
        principal,
        provider,
        capacidade=capacidade,
        ente=body.ente,
        ente_nome=ente_nome,
        esfera=None,
        periodo=periodo,
        as_of=body.as_of,
        pergunta=pergunta,
        coleta=coleta,
    )


# --------------------------------------------------------------------------- #
# 3. Narrativa do relatório
# --------------------------------------------------------------------------- #
def narrar_relatorio(
    session: Session,
    principal: Principal,
    provider: LLMProvider,
    body: NarrarRelatorioRequest,
) -> InsightOut:
    """Prosa executiva sobre o documento que o relatório já monta — nenhum número novo."""
    _org(principal)
    capacidade = "narrar_relatorio"
    ctx = _contexto(session, principal, capacidade)
    coleta = _Coleta()
    documento = _chamar(
        coleta,
        ctx,
        "documento_do_relatorio",
        {
            "ente": body.ente,
            "periodo": body.periodo,
            "modelo": body.modelo,
            "as_of": body.as_of,
        },
    )
    periodo = documento.get("periodo") or body.periodo
    ente_nome = documento.get("ente_nome")
    titulo_doc = documento.get("titulo") or body.modelo
    pergunta = (
        f"Escreva a narrativa executiva do relatório '{titulo_doc}' do ente {body.ente}"
        + (f" no período {periodo}" if periodo else "")
        + ", usando exatamente os números e as fontes do documento."
    )

    coleta.incompletos = [
        DadoIncompleto(
            tipo=str(issue.get("tipo") or "ausente"),
            codigo=str(issue.get("codigo") or ""),
            mensagem=str(issue.get("mensagem") or ""),
            periodo_esperado=issue.get("periodo_esperado"),
            periodo_encontrado=issue.get("periodo_encontrado"),
        )
        for issue in documento.get("dados_incompletos") or []
    ]
    if not documento.get("disponivel"):
        return _ausencia(
            capacidade=capacidade,
            ente=body.ente,
            ente_nome=ente_nome,
            periodo=periodo,
            as_of=body.as_of,
            pergunta=pergunta,
            motivo=str(
                documento.get("observacao")
                or f"O relatório '{titulo_doc}' não tem métrica materializada para "
                f"{body.ente}."
            ),
            coleta=coleta,
            session=session,
            principal=principal,
        )

    for metrica in documento.get("metricas") or []:
        coleta.fatos.append(_fato_da_metrica(metrica, periodo))
    coleta.nota(
        "Ausências declaradas pelo relatório",
        [f"{i.codigo}: {i.mensagem}" for i in coleta.incompletos]
        or ["Nenhuma: todos os itens do modelo estão materializados."],
        origem="documento_do_relatorio",
    )
    conformidade = documento.get("conformidade") or []
    coleta.nota(
        "Conformidade do exercício (entregas)",
        [
            f"{o.get('relatorio')} {o.get('periodo')}: {o.get('status')}"
            + (f", prazo {o.get('prazo')}" if o.get("prazo") else "")
            for o in conformidade
        ],
        origem="documento_do_relatorio",
    )
    coleta.nota(
        "Critério do relatório",
        [documento.get("criterio_incompletude")],
        origem="documento_do_relatorio",
    )

    return _responder(
        session,
        principal,
        provider,
        capacidade=capacidade,
        ente=body.ente,
        ente_nome=ente_nome,
        esfera=documento.get("esfera"),
        periodo=periodo,
        as_of=body.as_of,
        pergunta=pergunta,
        coleta=coleta,
    )


def _fato_da_metrica(metrica: dict[str, Any], periodo: str | None) -> FatoContexto:
    """Métrica do relatório → fato do contexto, com a mesma fonte que o documento imprime."""
    disponivel = bool(metrica.get("disponivel"))
    valor = metrica.get("valor")
    return FatoContexto(
        codigo=str(metrica.get("codigo") or ""),
        rotulo=str(metrica.get("rotulo") or ""),
        valor_formatado=str(metrica.get("valor_formatado") or "Dado não disponível"),
        unidade=str(metrica.get("unidade") or ""),
        status=str(metrica.get("status") or ""),
        disponivel=disponivel,
        periodo=str(periodo or ""),
        source_ref=(metrica.get("source_ref") or {}) if disponivel else {},
        faixa=metrica.get("faixa"),
        valor=str(valor) if disponivel and valor is not None else None,
        memoria={"formula": metrica["formula"]} if metrica.get("formula") else {},
    )


# --------------------------------------------------------------------------- #
# 4. Busca em linguagem natural na Central de Dados
# --------------------------------------------------------------------------- #
def paginas_conhecidas() -> list[str]:
    """Páginas para as quais existe mapa de cobertura (a recusa útil da §6.1 sai daqui)."""
    return sorted(set(fontes_por_pagina()) | set(INDICADORES_POR_PAGINA))


def pagina_da_pergunta(pergunta: str) -> str | None:
    """Roteia a pergunta para uma página. ``None`` ⇒ não deduziu, e a resposta dirá isso.

    Roteamento, não interpretação: o vocabulário mapeia palavra de negócio para página, e
    a resposta continua sendo produzida por cobertura, qualidade e calendário. Sem
    correspondência, a plataforma **declara** que não entendeu e lista o que sabe
    responder — que é uma recusa útil, não um "não sei" seco.
    """
    from app.modules.assistant import vectors

    tokens = vectors.tokenize(pergunta)
    for token in tokens:
        pagina = _PALAVRA_PAGINA.get(token)
        if pagina:
            return pagina
    return None


def buscar_central_dados(
    session: Session,
    principal: Principal,
    provider: LLMProvider,
    body: CentralDadosRequest,
) -> InsightOut:
    """"Por que Saúde está vazia para meu município?" — cobertura + qualidade + calendário."""
    _org(principal)
    capacidade = "central_dados"
    ctx = _contexto(session, principal, capacidade)
    coleta = _Coleta()
    pagina = (body.pagina or "").strip().lower() or pagina_da_pergunta(body.pergunta)
    pergunta = body.pergunta.strip()

    if pagina is None:
        return _ausencia(
            capacidade=capacidade,
            ente=body.ente,
            ente_nome=None,
            periodo=body.periodo,
            as_of=body.as_of,
            pergunta=pergunta,
            motivo=(
                "Não consegui identificar a que página/assunto a pergunta se refere, e "
                "não vou responder por adivinhação. Sei explicar a cobertura destes "
                "assuntos: " + ", ".join(paginas_conhecidas()) + "."
            ),
            coleta=coleta,
            session=session,
            principal=principal,
        )

    cobertura = _chamar(
        coleta,
        ctx,
        "cobertura_do_ente",
        {"ente": body.ente, "pagina": pagina, "periodo": body.periodo, "as_of": body.as_of},
    )
    coleta.nota(
        f"Cobertura da página '{pagina}'",
        [
            (
                "Este ente TEM dado para a página no período consultado."
                if cobertura.get("tem_dado")
                else "Este ente NÃO tem dado para a página no período consultado."
            ),
            (
                f"Período mais recente com dado: {cobertura.get('periodo_mais_recente')}"
                if cobertura.get("periodo_mais_recente")
                else "Não há nenhum período com dado para este ente nesta página."
            ),
            f"No seu escopo, {cobertura.get('entes_com_dado', 0)} de "
            f"{cobertura.get('entes_no_escopo', 0)} entes têm dado para esta página.",
            (
                "Fontes que alimentam a página: "
                + ", ".join(f.get("fonte", "") for f in cobertura.get("fontes") or [])
                if cobertura.get("fontes")
                else None
            ),
            (
                "Lacunas de carga da plataforma: " + ", ".join(cobertura.get("lacunas") or [])
                if cobertura.get("lacunas")
                else None
            ),
            cobertura.get("observacao"),
        ],
        origem="mart_cobertura_fonte",
    )

    qualidade = _chamar(
        coleta,
        ctx,
        "qualidade_do_ente",
        {"ente": body.ente, "periodo": body.periodo, "as_of": body.as_of},
    )
    coleta.nota(
        "Qualidade do dado deste ente",
        [
            f"{c.get('rotulo')} ({c.get('check_codigo')}): {c.get('status')}"
            + (f" — {c.get('resumo')}" if c.get("resumo") else "")
            for c in qualidade.get("checks") or []
        ]
        or [str(qualidade.get("observacao") or "Nenhum check em falha ou aviso aberto.")],
        origem="data_quality_check",
    )

    calendario = _chamar(
        coleta, ctx, "calendario_do_ente", {"ente": body.ente, "as_of": body.as_of}
    )
    pendentes = [
        o
        for o in calendario.get("obrigacoes") or []
        if str(o.get("status")) in STATUS_NAO_ENTREGUE
    ]
    linhas_calendario: list[str | None] = [
        f"Cadência do RGF para este ente: {calendario.get('periodicidade_rgf')}."
        if calendario.get("periodicidade_rgf")
        else None
    ]
    if pendentes:
        linhas_calendario.extend(
            f"{o.get('relatorio')} {o.get('periodo')}: {o.get('status')}"
            + (f", prazo {o.get('prazo')}" if o.get("prazo") else "")
            + (f" — {o.get('base_legal')}" if o.get("base_legal") else "")
            for o in pendentes[:_MAX_OBRIGACOES]
        )
    else:
        # A ausência de pendência é resposta, e das boas: separa "o ente atrasou" de "o
        # prazo ainda não venceu", que é justamente o que a Central de Dados precisa dizer.
        linhas_calendario.append(
            str(
                calendario.get("observacao")
                or "Nenhuma obrigação pendente ou atrasada no calendário deste ente — "
                "o que estiver faltando não é atraso de entrega."
            )
        )
    coleta.nota(
        "Calendário de obrigações", linhas_calendario, origem="gold.calendario_obrigacao"
    )

    return _responder(
        session,
        principal,
        provider,
        capacidade=capacidade,
        ente=body.ente,
        ente_nome=None,
        esfera=calendario.get("esfera"),
        periodo=body.periodo or cobertura.get("periodo_mais_recente"),
        as_of=body.as_of,
        pergunta=pergunta,
        coleta=coleta,
    )
