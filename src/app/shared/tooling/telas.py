"""Ferramentas que a IA nas telas exigiu (Sprint IA-5) — e nada além delas.

A IA-5 leva a inteligência para fora do Assistente: "Explique este número", a explicação
da fila de alertas, a narrativa do relatório e a busca em linguagem natural na Central de
Dados. Três dessas quatro capacidades se servem do catálogo que já existe (IA-1a/IA-1b).
Duas coisas faltavam, e a regra da §2.2 do plano de MCP é clara sobre onde elas entram:
**capacidade nova é ferramenta nova no registro**, com escopo, licença, ``as_of``,
``source_ref`` e auditoria aplicados pelo envelope — nunca um caminho paralelo que fale
com o banco direto (lição A22/E1).

- ``documento_do_relatorio`` — o documento que ``reports.build_document`` monta, sem
  gerar arquivo nem persistir nada. A narrativa executiva precisa **dos mesmos números e
  das mesmas fontes** do relatório; reconstruí-los aqui produziria uma segunda régua para
  o mesmo documento, que é exatamente o que a §7 do ``CLAUDE.md`` proíbe.
- ``calendario_do_ente`` — o calendário de obrigações (``gold.calendario_obrigacao``) com
  prazo, situação da entrega e base legal. É a terceira perna da resposta "por que esta
  página está vazia?": cobertura diz *se temos*, qualidade diz *se confere*, e o
  calendário diz *se já era devido* — sem ele, "não há dado" não distingue o ente que
  atrasou do período que ainda nem venceu.

**Zero cálculo fiscal.** As duas leem serviços que já existem e apenas tipam a saída.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from app.core.errors import AppError
from app.modules.alerts import service as alerts_service
from app.modules.reports import service as reports_service
from app.modules.reports.models import MODELOS, Relatorio
from app.shared.source_ref import SourceRef
from app.shared.tooling.base import (
    EnteToolInput,
    Tool,
    ToolContext,
    ToolOutput,
)
from app.shared.tooling.comum import periodo_efetivo, sem_entrega

#: Modelo usado quando ninguém disse qual. É o mesmo do resumo executivo do Assistente —
#: a narrativa de tela nasce do documento que o gestor já conhece.
MODELO_PADRAO = "executivo"


# --------------------------------------------------------------------------- #
# 1. documento_do_relatorio
# --------------------------------------------------------------------------- #
class DocumentoDoRelatorioIn(EnteToolInput):
    """Entrada de ``documento_do_relatorio``."""

    periodo: str | None = Field(
        default=None,
        max_length=16,
        description="Período fiscal canônico (ex.: '2024-B6'). Ausente ⇒ o mais recente.",
    )
    modelo: str = Field(
        default=MODELO_PADRAO,
        max_length=24,
        description=(
            "Modelo do relatório: executivo, limites, comparativo, conformidade ou "
            "boletim. Cada um seleciona um conjunto de seções."
        ),
        json_schema_extra={"enum": list(MODELOS)},
    )


class MetricaRelatorio(ToolOutput):
    """Uma métrica do documento — **a mesma** que o PDF/CSV imprime, com a fonte dela."""

    codigo: str
    rotulo: str
    disponivel: bool
    valor: Decimal | None = None
    valor_formatado: str = "Dado não disponível"
    unidade: str
    status: str
    faixa: str | None = None
    #: A fórmula declarada na memória de cálculo do relatório (texto, nunca número solto).
    formula: str | None = None
    source_ref: SourceRef | None = None


class ObrigacaoRelatorio(ToolOutput):
    """Uma linha da seção de conformidade (calendário do exercício do relatório)."""

    relatorio: str
    periodo: str
    prazo: date | None = None
    status: str
    versao_entrega: str | None = None
    source_ref: SourceRef | None = None


class IncompletudeRelatorio(ToolOutput):
    """Ausência/defasagem que o relatório declara em vez de omitir."""

    tipo: str
    codigo: str
    mensagem: str
    periodo_esperado: str | None = None
    periodo_encontrado: str | None = None


class DocumentoDoRelatorioOut(ToolOutput):
    """O documento do relatório, tipado. Nenhum número novo: só os que ele já imprime."""

    ente: str
    ente_nome: str | None = None
    esfera: str | None = None
    uf: str | None = None
    modelo: str
    titulo: str | None = None
    periodo: str | None = None
    as_of: datetime | None = None
    disponivel: bool = False
    total: int = 0
    metricas: list[MetricaRelatorio] = Field(default_factory=list)
    conformidade: list[ObrigacaoRelatorio] = Field(default_factory=list)
    dados_incompletos: list[IncompletudeRelatorio] = Field(default_factory=list)
    criterio_incompletude: str | None = None
    observacao: str | None = None

    def linhas(self) -> int:
        return len(self.metricas)


def _fonte_da_metrica(metrica: dict[str, Any]) -> SourceRef | None:
    """A primeira fonte declarada pela métrica — a lista já vem deduplicada do relatório."""
    for bruto in metrica.get("source_refs") or []:
        if isinstance(bruto, dict) and bruto.get("relatorio"):
            return SourceRef(
                relatorio=str(bruto["relatorio"]),
                anexo=bruto.get("anexo"),
                periodo=bruto.get("periodo"),
                versao_entrega=bruto.get("versao_entrega"),
            )
    return None


def _fonte_da_obrigacao(item: dict[str, Any]) -> SourceRef | None:
    bruto = item.get("source_ref")
    if not isinstance(bruto, dict) or not bruto.get("relatorio"):
        return None
    return SourceRef(
        relatorio=str(bruto["relatorio"]),
        anexo=bruto.get("anexo"),
        periodo=bruto.get("periodo"),
        versao_entrega=bruto.get("versao_entrega"),
    )


def _prazo(valor: Any) -> date | None:
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str) and valor:
        try:
            return date.fromisoformat(valor[:10])
        except ValueError:  # pragma: no cover - o relatório sempre grava ISO
            return None
    return None


def executar_documento_do_relatorio(
    ctx: ToolContext, entrada: DocumentoDoRelatorioIn
) -> DocumentoDoRelatorioOut:
    """Monta o documento do relatório **sem** gerar arquivo nem gravar linha nenhuma.

    A linha ``Relatorio`` é transitória (não entra na sessão), exatamente como o
    ``retriever`` do Assistente já faz desde a Sprint 17: o que se quer é a *fotografia*
    que o modelo de relatório produz — com a mesma resolução de versão, o mesmo ``as_of``
    e a mesma declaração de incompletude —, não um artefato para baixar. Pedir a narrativa
    não pode encher a fila de relatórios do gestor.

    **Limite conhecido:** as seções são as **canônicas do modelo**. Um relatório que o
    gestor gerou com um subconjunto de seções é narrado pelo modelo inteiro, então a
    narrativa pode citar um item que aquele PDF não trazia. Preferimos isso ao inverso
    (narrar menos do que o documento tem) enquanto a ferramenta não receber ``secoes``:
    número a mais **com fonte** é conferível; número de menos passa despercebido.
    """
    if entrada.modelo not in MODELOS:
        raise AppError(
            status=422,
            title="Modelo de relatório desconhecido",
            detail=f"Modelos disponíveis: {', '.join(MODELOS)}.",
        )
    periodo = periodo_efetivo(
        ctx, ente=entrada.ente, periodo=entrada.periodo, as_of=entrada.as_of
    )
    if periodo is None:
        return DocumentoDoRelatorioOut(
            ente=entrada.ente,
            modelo=entrada.modelo,
            as_of=entrada.as_of,
            observacao=sem_entrega(entrada.ente),
        )

    as_of = entrada.as_of or datetime.now(UTC)
    assert ctx.principal.org_id is not None  # o envelope já recusou sem organização
    row = Relatorio(
        org_id=ctx.principal.org_id,
        lote_id=uuid.uuid4(),
        modelo=entrada.modelo,
        modelo_versao="v1",
        formato="pdf",
        escopo="ente",
        cod_ibge=entrada.ente,
        periodo=periodo,
        as_of=as_of,
        status="processando",
        progresso=0,
        parametros={},
        cabecalho={},
        source_refs=[],
        memoria={},
        dados_incompletos=[],
        criado_por=ctx.principal.usuario_id,
    )
    documento = reports_service.build_document(ctx.session, row, datetime.now(UTC))
    cabecalho = documento.get("cabecalho") or {}
    metricas = [
        MetricaRelatorio(
            codigo=str(m.get("codigo")),
            rotulo=str(m.get("rotulo")),
            disponivel=m.get("valor") is not None,
            valor=m.get("valor"),
            valor_formatado=str(m.get("valor_formatado") or "Dado não disponível"),
            unidade=str(m.get("unidade") or ""),
            status=str(m.get("status") or ""),
            faixa=m.get("faixa"),
            formula=(m.get("memoria") or {}).get("formula"),
            source_ref=_fonte_da_metrica(m),
        )
        for m in documento.get("metricas") or []
    ]
    return DocumentoDoRelatorioOut(
        ente=entrada.ente,
        ente_nome=cabecalho.get("ente"),
        esfera=cabecalho.get("esfera"),
        uf=cabecalho.get("uf"),
        modelo=entrada.modelo,
        titulo=documento.get("titulo"),
        periodo=periodo,
        as_of=as_of,
        disponivel=any(m.disponivel for m in metricas),
        total=len(metricas),
        metricas=metricas,
        conformidade=[
            ObrigacaoRelatorio(
                relatorio=str(item.get("relatorio")),
                periodo=str(item.get("periodo")),
                prazo=_prazo(item.get("prazo")),
                status=str(item.get("status") or ""),
                versao_entrega=item.get("versao_entrega"),
                source_ref=_fonte_da_obrigacao(item),
            )
            for item in documento.get("conformidade") or []
        ],
        dados_incompletos=[
            IncompletudeRelatorio(
                tipo=str(issue.get("tipo") or "ausente"),
                codigo=str(issue.get("codigo") or ""),
                mensagem=str(issue.get("mensagem") or ""),
                periodo_esperado=issue.get("periodo_esperado"),
                periodo_encontrado=issue.get("periodo_encontrado"),
            )
            for issue in documento.get("dados_incompletos") or []
        ],
        criterio_incompletude=documento.get("criterio_incompletude"),
        observacao=(
            None
            if any(m.disponivel for m in metricas)
            else (
                f"O modelo '{entrada.modelo}' não tem nenhuma métrica materializada para "
                f"{entrada.ente} em {periodo}. O relatório continuaria sendo emitido — com "
                f"cada item ausente declarado —, mas não há número para narrar."
            )
        ),
    )


# --------------------------------------------------------------------------- #
# 2. calendario_do_ente
# --------------------------------------------------------------------------- #
class CalendarioDoEnteIn(EnteToolInput):
    """Entrada de ``calendario_do_ente``."""

    relatorio: str | None = Field(
        default=None,
        max_length=8,
        description="Filtra por relatório (RREO, RGF, DCA). Ausente ⇒ todos.",
    )


class ObrigacaoCalendario(ToolOutput):
    """Uma obrigação do calendário: prazo, situação e a base legal do prazo."""

    relatorio: str
    periodo: str
    periodicidade: str | None = None
    prazo: date | None = None
    status: str
    entregue_em: datetime | None = None
    versao_entrega: str | None = None
    base_legal: str | None = None
    source_ref: SourceRef | None = None


class CalendarioDoEnteOut(ToolOutput):
    """O calendário do ente. Metadado de obrigação — não carrega valor fiscal."""

    ente: str
    esfera: str | None = None
    #: Quadrimestral ou semestral conforme porte (§ cadência do RGF, Sprint 15).
    periodicidade_rgf: str | None = None
    total: int = 0
    #: Situação vem por obrigação, e não como contador agregado: um "2 pendentes" na raiz
    #: seria um número sem ``source_ref`` a mais na saída (a guarda da G4 teria de abrir
    #: exceção para ele) e esconderia **quais** estão pendentes, que é o que o gestor faz.
    obrigacoes: list[ObrigacaoCalendario] = Field(default_factory=list)
    observacao: str | None = None

    def linhas(self) -> int:
        return len(self.obrigacoes)


#: Situações que significam "ainda não entregue" no calendário da Sprint 15
#: (``alerts/engine.py``: entregue | pendente | atrasado).
STATUS_PENDENTE = "pendente"
STATUS_ATRASADO = "atrasado"
STATUS_NAO_ENTREGUE: frozenset[str] = frozenset({STATUS_PENDENTE, STATUS_ATRASADO})


def executar_calendario_do_ente(
    ctx: ToolContext, entrada: CalendarioDoEnteIn
) -> CalendarioDoEnteOut:
    """Calendário de obrigações do ente, com prazo, situação e base legal.

    Responde à parte da pergunta "por que esta página está vazia?" que cobertura e
    qualidade não alcançam: **o prazo já venceu?**. Sem isso, a plataforma trataria igual
    o ente que atrasou a entrega e o bimestre que ainda não era devido — e diria ao gestor
    que ele tem uma pendência que não existe.
    """
    resposta = alerts_service.calendario(ctx.session, ctx.principal, entrada.ente)
    filtro = (entrada.relatorio or "").strip().upper() or None
    itens = [
        ObrigacaoCalendario(
            relatorio=item.relatorio,
            periodo=item.periodo,
            periodicidade=item.periodicidade,
            prazo=item.prazo,
            status=item.status,
            entregue_em=item.entregue_em,
            versao_entrega=item.versao_entrega,
            base_legal=item.base_legal,
            source_ref=item.source_ref,
        )
        for item in resposta.itens
        if filtro is None or item.relatorio.upper() == filtro
    ]
    return CalendarioDoEnteOut(
        ente=entrada.ente,
        esfera=resposta.esfera,
        periodicidade_rgf=resposta.periodicidade_rgf,
        total=len(itens),
        obrigacoes=itens,
        observacao=(
            None
            if itens
            else (
                f"Não há obrigações materializadas no calendário de {entrada.ente}"
                + (f" para {filtro}" if filtro else "")
                + ". O calendário é derivado das entregas conhecidas; sem nenhuma, não há "
                "prazo a cobrar."
            )
        ),
    )


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #
def _h_documento(ctx: ToolContext, entrada: Any) -> ToolOutput:
    return executar_documento_do_relatorio(ctx, entrada)


def _h_calendario(ctx: ToolContext, entrada: Any) -> ToolOutput:
    return executar_calendario_do_ente(ctx, entrada)


def ferramentas() -> tuple[Tool, ...]:
    """As duas ferramentas da IA-5. Entram na mesma matriz de isolamento das anteriores."""
    return (
        Tool(
            nome="documento_do_relatorio",
            descricao=(
                "Devolve o documento de um relatório do ente (executivo, limites, "
                "comparativo, conformidade ou boletim) exatamente como a plataforma o "
                "emite: cada métrica com valor, unidade, faixa, memória e fonte, mais as "
                "ausências declaradas. Use para narrar ou resumir um relatório sem "
                "recalcular nada. Não gera arquivo e não persiste solicitação."
            ),
            entrada=DocumentoDoRelatorioIn,
            saida=DocumentoDoRelatorioOut,
            handler=_h_documento,
            capacidade="ver",
            recebe_ente=True,
            saida_tem_numero_fiscal=True,
        ),
        Tool(
            nome="calendario_do_ente",
            descricao=(
                "Calendário de obrigações do ente (RREO, RGF, DCA) com prazo legal, "
                "situação da entrega e base legal do prazo. Use para responder se um dado "
                "ausente já era devido, se está atrasado ou se o prazo ainda não venceu. "
                "Devolve metadado de obrigação, não valores fiscais."
            ),
            entrada=CalendarioDoEnteIn,
            saida=CalendarioDoEnteOut,
            handler=_h_calendario,
            capacidade="ver",
            recebe_ente=True,
            saida_tem_numero_fiscal=False,
        ),
    )
