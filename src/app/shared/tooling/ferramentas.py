"""Ampliação do catálogo de ferramentas (Sprint IA-1b) — oito capacidades, zero cálculo.

A IA-1a provou o contrato com duas ferramentas; esta sprint leva o catálogo às demais
capacidades do produto. **Nenhuma linha aqui calcula nada fiscal.** Cada ferramenta chama
o serviço que já é a fonte única daquele número (§7 do `CLAUDE.md`) e se limita a três
tarefas honestas: tipar a entrada, traduzir a saída para o que um modelo consegue ler, e
carregar a procedência junto.

Se alguma destas funções algum dia precisar somar, dividir ou classificar um valor fiscal,
o lugar é ``indicators/`` — e o número tem de estar materializado antes de chegar aqui.

**Duas decisões de tradução que valem para todas.**

1. **Onde mora o ``source_ref``.** Onde a resposta inteira sai de uma única entrega
   (drill de receita/despesa), a fonte fica na raiz e cobre a árvore. Onde cada linha vem
   de uma entrega diferente — a série histórica, com um período por entrega; a lista de
   limites, em que ``garantias`` vem do RGF e a dívida do RREO — a fonte é **por item**.
   Carimbar uma raiz só nesses casos produziria uma procedência uniforme e errada, que é
   pior que nenhuma: erra com aparência de rigor (decisão 3 da IA-1a).

2. **Memória de cálculo viaja como texto.** Ver ``comum.texto``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from app.core.errors import AppError
from app.modules.alerts import service as alerts_service
from app.modules.benchmark import service as benchmark_service
from app.modules.catalog import service as catalog_service
from app.modules.coverage import service as coverage_service
from app.modules.expense import classificacao as expense_cls
from app.modules.expense import service as expense_service
from app.modules.indicators import rotulos
from app.modules.limits import repository as limits_repo
from app.modules.limits import service as limits_service
from app.modules.quality import service as quality_service
from app.modules.quality.schemas import CheckOut
from app.modules.reports.service import formatar_valor
from app.modules.revenue import service as revenue_service
from app.shared.envelope import DrillEnvelope
from app.shared.scope import carteira_scope_ibges
from app.shared.source_ref import SourceRef, fonte_gravada
from app.shared.tooling.base import EnteToolInput, Tool, ToolContext, ToolOutput
from app.shared.tooling.comum import (
    RELATORIO_ANCORA,
    periodo_efetivo,
    sem_entrega,
    sem_vigente,
    texto,
)

#: Denominadores em que o percentual é a leitura principal; nos demais, manda o valor em R$.
_FORMATO_PCT = "PERCENTUAL"
_FORMATO_BRL = "BRL"


def _fonte_do_mart(bruto: dict | None, *, periodo: str, versao: str) -> SourceRef:
    """Procedência de uma linha do mart, com o RREO do denominador como reserva."""
    return fonte_gravada(
        bruto, SourceRef(relatorio=RELATORIO_ANCORA, periodo=periodo, versao_entrega=versao)
    )


# --------------------------------------------------------------------------- #
# 1. serie_historica
# --------------------------------------------------------------------------- #
class SerieHistoricaIn(EnteToolInput):
    """Entrada de ``serie_historica``."""

    indicador: str = Field(
        min_length=2,
        max_length=64,
        description="Código do indicador (ex.: pessoal_executivo, divida_consolidada_liquida).",
    )
    periodo_inicial: str | None = Field(
        default=None, max_length=16, description="Recorte inicial inclusivo (ex.: '2023-B1')."
    )
    periodo_final: str | None = Field(
        default=None, max_length=16, description="Recorte final inclusivo (ex.: '2024-B6')."
    )


class PontoSerie(ToolOutput):
    """Um período da série — com a **sua** entrega, não a do período mais recente."""

    periodo: str
    valor_rs: Decimal | None = None
    valor_pct: Decimal | None = None
    faixa: str | None = None
    versao_entrega: str | None = None
    source_ref: SourceRef | None = None


class LimiteDaFaixa(ToolOutput):
    """Os limiares que dão sentido ao rótulo da faixa — com procedência própria.

    Existem por uma razão medida. Sem eles, o ponto da série dizia ``faixa="alerta"`` e
    não dizia *alerta a partir de quanto*; pedido a explicar a posição em relação ao
    limite, o modelo completava a lacuna calculando (54% × 0,90 = 48,6%). A conta estava
    certa e o número não tinha fonte — que é o que a regra 2.2 do prompt passou a proibir.
    Proibir sem fornecer tornaria a pergunta irrespondível.

    Objeto separado, e não campos soltos na raiz, porque a **procedência é outra**: os
    pontos da série vêm da entrega do RREO/RGF daquele período; estes números vêm da norma,
    via ``gold.dim_limite_legal``. Herdar a cobertura de um ``source_ref`` de entrega
    diria, falsamente, que o teto foi apurado junto com o valor.
    """

    #: ``teto`` (não ultrapassar) ou ``piso`` (mínimo a aplicar). Sem isto o limiar é
    #: ambíguo: em mínimo de saúde/educação a semântica é invertida — ficar **abaixo** é
    #: a irregularidade.
    indicador: str | None = None
    sentido: str
    teto_pct: Decimal | None = None
    alerta_pct: Decimal | None = None
    prudencial_pct: Decimal | None = None
    #: Os mesmos três números já na formatação pt-BR que a plataforma imprime. Não é
    #: redundância decorativa: entregue só o ``Decimal`` cru, o modelo formatava sozinho e
    #: escolhia ponto ("48.60%"). Em português o ponto é separador de milhar, então a
    #: verificação lia "48.60%" como milhar, caía num token solto ("60%") e acusava sem
    #: lastro um número que tinha lastro. A regra 2.1 do prompt manda copiar o valor
    #: exatamente como aparece — faltava fazê-lo aparecer já correto.
    teto_formatado: str | None = None
    alerta_formatado: str | None = None
    prudencial_formatado: str | None = None
    source_ref: SourceRef | None = None


class SerieHistoricaOut(ToolOutput):
    ente: str
    indicador: str
    rotulo: str
    disponivel: bool
    total: int = 0
    pontos: list[PontoSerie] = Field(default_factory=list)
    #: No topo, e não por ponto, porque ``gold.dim_limite_legal`` guarda um limiar por
    #: indicador/esfera/poder, sem vigência temporal: repeti-lo em cada ponto sugeriria
    #: que ele varia ao longo da série, o que este modelo de dados não afirma.
    limite: LimiteDaFaixa | None = None
    as_of: datetime | None = None
    observacao: str | None = None

    def linhas(self) -> int:
        return len(self.pontos)


def executar_serie_historica(ctx: ToolContext, entrada: SerieHistoricaIn) -> SerieHistoricaOut:
    """Evolução do indicador, período a período — **uma entrega por ponto** (§6.5).

    Este é o ponto exato em que a família A14/A15 nasce: uma série que resolvesse a
    vigência uma vez e a repetisse em todos os períodos misturaria versões superadas com
    vigentes numa mesma linha de gráfico. ``limits.serie_historica`` resolve a vigência
    **de cada período**, e é por isso que cada ponto traz a sua ``versao_entrega``.
    """
    serie = limits_service.serie_historica(
        ctx.session, entrada.ente, entrada.indicador, as_of=entrada.as_of
    )
    pontos = [
        PontoSerie(
            periodo=item.periodo,
            valor_rs=item.valor_rs,
            valor_pct=item.valor_pct_rcl,
            faixa=item.faixa,
            versao_entrega=item.versao_entrega,
            source_ref=item.source_ref,
        )
        for item in serie
        if _no_recorte(item.periodo, entrada.periodo_inicial, entrada.periodo_final)
    ]
    observacao = None
    if not pontos:
        observacao = (
            f"Não há série materializada de '{entrada.indicador}' para {entrada.ente}"
            + (" no recorte pedido." if entrada.periodo_inicial or entrada.periodo_final else ".")
            + " A plataforma não interpola período sem apuração."
        )
    # A esfera decide o teto (54% município × 49% estado), então o limiar é lido do ente,
    # nunca de uma constante. Duas ausências legítimas, e as duas silenciosas de propósito:
    # ente sem esfera cadastrada (``_esfera_do_ente`` levanta 422) e indicador gerencial
    # sem limite legal. Nos dois casos o bloco fica ausente e a série continua respondendo
    # — enriquecer a resposta não pode derrubar o que já funcionava, e ausência aqui é mais
    # honesta que um zero que o modelo leria como "teto de 0%".
    try:
        esfera = limits_service._esfera_do_ente(ctx.session, entrada.ente)
        dim = limits_service._limite_dim(ctx.session, entrada.indicador, esfera)
    except AppError:
        dim = None
    limite = (
        LimiteDaFaixa(
            sentido=dim.sentido,
            teto_pct=dim.teto_pct,
            alerta_pct=dim.alerta_pct,
            prudencial_pct=dim.prudencial_pct,
            teto_formatado=formatar_valor(dim.teto_pct, _FORMATO_PCT),
            alerta_formatado=formatar_valor(dim.alerta_pct, _FORMATO_PCT),
            prudencial_formatado=formatar_valor(dim.prudencial_pct, _FORMATO_PCT),
            # Sem ``anexo``: ``dim_limite_legal`` não guarda o dispositivo, e escrever
            # "LRF art. X" por indicador seria inventar citação legal com ar de rigor.
            source_ref=SourceRef(relatorio="LRF"),
        )
        if dim is not None
        else None
    )
    return SerieHistoricaOut(
        ente=entrada.ente,
        indicador=entrada.indicador,
        rotulo=rotulos.rotulo(entrada.indicador),
        disponivel=bool(pontos),
        total=len(pontos),
        pontos=pontos,
        limite=limite,
        as_of=entrada.as_of,
        observacao=observacao,
    )


def _no_recorte(periodo: str, inicial: str | None, final: str | None) -> bool:
    """Recorte por comparação textual — válida porque o período canônico é ordenável.

    ``'2024-B1' < '2024-B6' < '2025-B1'`` em ordem lexicográfica porque o formato é
    ``AAAA-Bn`` com o ano à frente e zero ambiguidade de largura. Não é uma conta fiscal:
    é filtro de recorte sobre uma chave já canônica (§6.6).
    """
    if inicial and periodo < inicial:
        return False
    return not (final and periodo > final)


# --------------------------------------------------------------------------- #
# 2. limites_do_ente
# --------------------------------------------------------------------------- #
class LimitesDoEnteIn(EnteToolInput):
    """Entrada de ``limites_do_ente``."""

    periodo: str | None = Field(
        default=None,
        max_length=16,
        description="Período fiscal canônico (ex.: '2024-B6'). Ausente ⇒ o mais recente.",
    )


class LimiteFerramenta(ToolOutput):
    indicador: str
    rotulo: str
    #: ``teto`` (não ultrapassar) ou ``piso`` (mínimo a aplicar) — a semântica é invertida.
    sentido: str
    valor_rs: Decimal | None = None
    valor_pct: Decimal | None = None
    valor_formatado: str = "Dado não disponível"
    faixa: str | None = None
    teto_pct: Decimal | None = None
    alerta_pct: Decimal | None = None
    prudencial_pct: Decimal | None = None
    distancia_teto: Decimal | None = None
    distancia_alerta: Decimal | None = None
    denominador: str = "rcl"
    base_valor: Decimal | None = None
    source_ref: SourceRef | None = None


class LimitesDoEnteOut(ToolOutput):
    ente: str
    ente_nome: str | None = None
    esfera: str | None = None
    periodo: str | None = None
    versao_entrega: str | None = None
    as_of: datetime | None = None
    disponivel: bool = False
    total: int = 0
    itens: list[LimiteFerramenta] = Field(default_factory=list)
    #: Os limites **legais** aplicáveis ao ente, mesmo sem apuração no período. A linha de
    #: ``gold.dim_limite_legal`` não depende de o ente ter entregue relatório: o teto do
    #: Executivo estadual é 49% havendo ou não RGF. Sem isto, uma pergunta sobre período
    #: sem dado levava o modelo a explicar a norma e **derivar** as faixas dela ("o
    #: prudencial equivale a 46,55%") — número correto e sem procedência, que é o que a
    #: regra 2.2 proíbe e ele fazia por não ter de onde copiar.
    limites_aplicaveis: list[LimiteDaFaixa] = Field(default_factory=list)
    observacao: str | None = None

    def linhas(self) -> int:
        return len(self.itens) or len(self.limites_aplicaveis)


def _limites_aplicaveis(ctx: ToolContext, ente: str) -> list[LimiteDaFaixa]:
    """Os tetos/pisos que valem para o ente — independentes de haver apuração.

    Serve à ausência com saída: quando não há número do ente, a resposta útil não é o
    silêncio, é "não há apuração para este período, e os limites que se aplicam a você são
    estes". De quebra, tira do modelo o motivo para derivar a faixa a partir do teto.
    """
    try:
        esfera = limits_service._esfera_do_ente(ctx.session, ente)
    except AppError:
        return []
    aplicaveis: list[LimiteDaFaixa] = []
    for indicador in sorted(limits_service.SEMAFORO_INDICADORES):
        dim = limits_service._limite_dim(ctx.session, indicador, esfera)
        if dim is None:
            continue
        aplicaveis.append(
            LimiteDaFaixa(
                indicador=indicador,
                sentido=dim.sentido,
                teto_pct=dim.teto_pct,
                alerta_pct=dim.alerta_pct,
                prudencial_pct=dim.prudencial_pct,
                teto_formatado=formatar_valor(dim.teto_pct, _FORMATO_PCT),
                alerta_formatado=formatar_valor(dim.alerta_pct, _FORMATO_PCT),
                prudencial_formatado=formatar_valor(dim.prudencial_pct, _FORMATO_PCT),
                source_ref=SourceRef(relatorio="LRF"),
            )
        )
    return aplicaveis


def executar_limites_do_ente(ctx: ToolContext, entrada: LimitesDoEnteIn) -> LimitesDoEnteOut:
    """Todos os limites legais do ente no período, com faixa e distância ao teto.

    A conformidade inteira numa chamada: é o que responde "estou dentro dos limites?" sem
    o modelo ter de adivinhar quais indicadores existem e pedir um por um.

    **A procedência é por item, não da lista.** ``build_limites`` carimba a raiz como RREO
    porque é dali que sai a RCL do denominador; ``garantias`` e ``operacoes_credito``, no
    entanto, são apurados do RGF. Repetir o carimbo da raiz em cada item reproduziria o
    defeito que a IA-1a documentou, então cada item recebe a fonte gravada na sua própria
    linha do mart.
    """
    periodo = periodo_efetivo(
        ctx, ente=entrada.ente, periodo=entrada.periodo, as_of=entrada.as_of
    )
    if periodo is None:
        return LimitesDoEnteOut(
            ente=entrada.ente,
            limites_aplicaveis=_limites_aplicaveis(ctx, entrada.ente),
            observacao=sem_entrega(entrada.ente),
        )

    resposta = limits_service.build_limites(
        ctx.session, entrada.ente, periodo, as_of=entrada.as_of
    )
    versao = resposta.versao_entrega or None
    if versao is None:
        return LimitesDoEnteOut(
            ente=entrada.ente,
            periodo=periodo,
            limites_aplicaveis=_limites_aplicaveis(ctx, entrada.ente),
            observacao=sem_vigente(
                entrada.ente, periodo, entrada.as_of, relatorio=RELATORIO_ANCORA
            ),
        )

    fontes = {
        mart.indicador: mart.source_ref
        for mart in limits_repo.list_mart_by_periodo(
            ctx.session, cod_ibge=entrada.ente, periodo=periodo, versao_entrega=versao
        )
    }
    ente_dim = catalog_service.refresh_dim_ente(ctx.session, entrada.ente)
    itens = [
        LimiteFerramenta(
            indicador=item.indicador,
            rotulo=rotulos.rotulo(item.indicador),
            sentido=item.sentido,
            valor_rs=item.valor_rs,
            valor_pct=item.valor_pct_rcl,
            valor_formatado=formatar_valor(
                item.valor_pct_rcl if item.valor_pct_rcl is not None else item.valor_rs,
                _FORMATO_PCT if item.valor_pct_rcl is not None else _FORMATO_BRL,
            ),
            faixa=item.faixa,
            teto_pct=item.teto_pct,
            alerta_pct=item.alerta_pct,
            prudencial_pct=item.prudencial_pct,
            distancia_teto=item.distancia_teto,
            distancia_alerta=item.distancia_alerta,
            denominador=item.denominador,
            base_valor=item.base_valor,
            source_ref=_fonte_do_mart(
                fontes.get(item.indicador), periodo=periodo, versao=versao
            ),
        )
        for item in resposta.itens
    ]
    return LimitesDoEnteOut(
        ente=entrada.ente,
        ente_nome=ente_dim.nome if ente_dim else None,
        esfera=resposta.itens[0].esfera if resposta.itens else None,
        periodo=periodo,
        versao_entrega=versao,
        as_of=resposta.as_of,
        disponivel=bool(itens),
        total=len(itens),
        itens=itens,
        observacao=(
            None
            if itens
            else (
                f"Nenhum limite legal apurado para {entrada.ente} em {periodo}. Os "
                f"indicadores gerenciais (sem teto legal) ficam fora desta lista por "
                f"construção — para eles use indicador_do_ente."
            )
        ),
    )


# --------------------------------------------------------------------------- #
# 3 e 4. drill_receita / drill_despesa
# --------------------------------------------------------------------------- #
class DrillReceitaIn(EnteToolInput):
    """Entrada de ``drill_receita``."""

    periodo: str | None = Field(
        default=None, max_length=16, description="Período canônico. Ausente ⇒ o mais recente."
    )
    node: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Código da origem de receita a abrir. Ausente ⇒ as raízes da hierarquia. "
            "Para descer, chame de novo com o código de um dos 'children'."
        ),
    )


class DrillDespesaIn(DrillReceitaIn):
    """Entrada de ``drill_despesa`` — a despesa tem **dois eixos**, e eles não se misturam."""

    eixo: str = Field(
        default=expense_cls.EIXO_FUNCAO,
        description=(
            "'funcao' (Anexo 02 — em que política pública se gastou) ou 'natureza' "
            "(Anexo 01 — em que se gastou: pessoal, custeio, investimento). São recortes "
            "do mesmo total por ângulos diferentes; somá-los duplicaria a despesa."
        ),
    )


class NoDrill(ToolOutput):
    codigo: str
    descricao: str
    nivel: int | None = None
    measures: dict[str, Any] = Field(default_factory=dict)
    has_children: bool = False


class DrillOut(ToolOutput):
    """Envelope de drill (§6.1) traduzido para a ferramenta — mesma semântica da tela."""

    ente: str
    periodo: str | None = None
    eixo: str | None = None
    node: NoDrill | None = None
    breadcrumb: list[NoDrill] = Field(default_factory=list)
    children: list[NoDrill] = Field(default_factory=list)
    measures: dict[str, Any] = Field(default_factory=dict)
    as_of: datetime | None = None
    source_ref: SourceRef | None = None
    observacao: str | None = None

    def linhas(self) -> int:
        return len(self.children)


def _no(codigo: str, descricao: str, nivel: int | None, **extra: Any) -> NoDrill:
    return NoDrill(codigo=codigo, descricao=descricao, nivel=nivel, **extra)


def _traduzir_drill(
    envelope: DrillEnvelope, *, ente: str, periodo: str, eixo: str | None
) -> DrillOut:
    return DrillOut(
        ente=ente,
        periodo=periodo,
        eixo=eixo,
        node=(
            _no(envelope.node.codigo, envelope.node.descricao, envelope.node.nivel)
            if envelope.node
            else None
        ),
        breadcrumb=[_no(b.codigo, b.descricao, b.nivel) for b in envelope.breadcrumb],
        children=[
            _no(
                c.codigo,
                c.descricao,
                c.nivel,
                measures=dict(c.measures),
                has_children=c.has_children,
            )
            for c in envelope.children
        ],
        measures=dict(envelope.measures),
        as_of=envelope.as_of,
        source_ref=envelope.source_ref,
        observacao=(
            None
            if envelope.children or envelope.node
            else (
                f"Não há árvore de receita/despesa materializada para {ente} em {periodo}. "
                f"A ausência é da carga, não do ente — confira com cobertura_do_ente."
            )
        ),
    )


def executar_drill_receita(ctx: ToolContext, entrada: DrillReceitaIn) -> DrillOut:
    """Drill da natureza da receita (§6.1), reusando ``revenue.build_arvore``.

    O envelope traz a fonte na raiz e ela cobre a árvore inteira: todos os nós vêm da
    **mesma** entrega, resolvida uma vez. É o oposto da série histórica, e a diferença é
    justamente o que decide onde o ``source_ref`` fica.
    """
    periodo = periodo_efetivo(
        ctx, ente=entrada.ente, periodo=entrada.periodo, as_of=entrada.as_of
    )
    if periodo is None:
        return DrillOut(ente=entrada.ente, observacao=sem_entrega(entrada.ente))
    envelope = revenue_service.build_arvore(
        ctx.session, entrada.ente, periodo, entrada.node, as_of=entrada.as_of
    )
    return _traduzir_drill(envelope, ente=entrada.ente, periodo=periodo, eixo=None)


def executar_drill_despesa(ctx: ToolContext, entrada: DrillDespesaIn) -> DrillOut:
    """Drill da despesa por função ou natureza, reusando ``expense.build_arvore``."""
    periodo = periodo_efetivo(
        ctx, ente=entrada.ente, periodo=entrada.periodo, as_of=entrada.as_of
    )
    if periodo is None:
        return DrillOut(ente=entrada.ente, eixo=entrada.eixo, observacao=sem_entrega(entrada.ente))
    envelope = expense_service.build_arvore(
        ctx.session,
        entrada.ente,
        periodo,
        entrada.node,
        eixo=entrada.eixo,
        as_of=entrada.as_of,
    )
    return _traduzir_drill(envelope, ente=entrada.ente, periodo=periodo, eixo=entrada.eixo)


# --------------------------------------------------------------------------- #
# 5. cobertura_do_ente
# --------------------------------------------------------------------------- #
class CoberturaDoEnteIn(EnteToolInput):
    """Entrada de ``cobertura_do_ente``."""

    pagina: str = Field(
        min_length=2,
        max_length=48,
        description=(
            "Página/assunto cuja cobertura se quer medir: dashboard, limites, "
            "saude-educacao, divida, resultado, benchmarking, carteira."
        ),
    )
    periodo: str | None = Field(default=None, max_length=16, description="Período de interesse.")


class FonteCobertura(ToolOutput):
    fonte: str
    descricao: str | None = None
    orgao: str | None = None
    entes_com_dado: int = 0
    periodo_mais_recente: str | None = None


class IndicadorCobertura(ToolOutput):
    indicador: str
    rotulo: str
    entes_com_dado: int = 0
    periodo_mais_recente: str | None = None


class CoberturaDoEnteOut(ToolOutput):
    """Para quantos entes esta página **de fato** responde — nunca um número fiscal."""

    ente: str
    pagina: str
    tem_dado: bool = False
    periodo_solicitado: str | None = None
    periodo_mais_recente: str | None = None
    entes_no_escopo: int = 0
    entes_com_dado: int = 0
    fontes: list[FonteCobertura] = Field(default_factory=list)
    indicadores: list[IndicadorCobertura] = Field(default_factory=list)
    lacunas: list[str] = Field(default_factory=list)
    observacao: str | None = None

    def linhas(self) -> int:
        return len(self.fontes) + len(self.indicadores)


def executar_cobertura_do_ente(
    ctx: ToolContext, entrada: CoberturaDoEnteIn
) -> CoberturaDoEnteOut:
    """Distingue "o ente não entregou" de "a plataforma não carregou".

    É a ferramenta que sustenta o guardrail §9 na prática: sem ela, o assistente diante de
    um dado ausente só consegue dizer "não tenho o dado", e o gestor conclui a leitura mais
    disponível — a de que o **seu** setor contábil falhou. A cobertura é medida dentro da
    carteira de quem pergunta, que é o único denominador sobre o qual ele pode agir.
    """
    if entrada.as_of is not None:
        # ``gold.mart_cobertura_fonte`` é um retrato corrente: a rematerialização apaga
        # e reconstrói suas linhas. Filtrar pelo carimbo atual produziria falso histórico
        # (e, pior, culparia o ente por uma lacuna que pode ser apenas do nosso snapshot).
        # Até a cobertura ter armazenamento/consulta bitemporal, falhar é a única resposta
        # honesta para um corte explícito.
        raise AppError(
            status=422,
            title="Cobertura histórica indisponível",
            detail=(
                "A cobertura por página ainda é materializada apenas como estado atual e "
                "não pode ser reproduzida com 'as_of'. Remova o corte para consultar a "
                "cobertura corrente."
            ),
            type_="urn:plataforma-fiscal:error:cobertura-as-of-nao-suportado",
        )
    cobertura = coverage_service.build_cobertura_pagina(
        ctx.session,
        pagina=entrada.pagina,
        cod_ibge=entrada.ente,
        periodo=entrada.periodo,
        entes_do_escopo=carteira_scope_ibges(ctx.session, ctx.principal),
    )
    return CoberturaDoEnteOut(
        ente=entrada.ente,
        pagina=cobertura.pagina,
        tem_dado=cobertura.ente.tem_dado,
        periodo_solicitado=cobertura.ente.periodo_solicitado,
        periodo_mais_recente=cobertura.ente.periodo_mais_recente,
        entes_no_escopo=cobertura.escopo.entes_no_escopo,
        entes_com_dado=cobertura.escopo.entes_com_dado,
        fontes=[
            FonteCobertura(
                fonte=f.fonte,
                descricao=f.descricao,
                orgao=f.orgao,
                entes_com_dado=f.entes_com_dado,
                periodo_mais_recente=f.periodo_mais_recente,
            )
            for f in cobertura.fontes
        ],
        indicadores=[
            IndicadorCobertura(
                indicador=i.indicador,
                rotulo=rotulos.rotulo(i.indicador),
                entes_com_dado=i.entes_com_dado,
                periodo_mais_recente=i.periodo_mais_recente,
            )
            for i in cobertura.indicadores
        ],
        lacunas=list(cobertura.lacunas),
        observacao=cobertura.observacao,
    )


# --------------------------------------------------------------------------- #
# 6. qualidade_do_ente
# --------------------------------------------------------------------------- #
class QualidadeDoEnteIn(EnteToolInput):
    """Entrada de ``qualidade_do_ente``."""

    periodo: str | None = Field(
        default=None,
        max_length=16,
        description="Período a selar. Ausente ⇒ todos os checks abertos do ente.",
    )


class CheckFerramenta(ToolOutput):
    """Um check aberto. Os dois lados só saem **com** a entrega que os fundamenta."""

    check_codigo: str
    rotulo: str
    fonte: str
    status: str
    periodo: str | None = None
    versao_entrega: str | None = None
    esquerda: Decimal | None = None
    direita: Decimal | None = None
    diferenca: Decimal | None = None
    tolerancia: Decimal | None = None
    #: Leitura textual do check quando ele não compara dois valores publicados.
    resumo: str | None = None
    source_ref: SourceRef | None = None


class QualidadeDoEnteOut(ToolOutput):
    ente: str
    periodo: str | None = None
    as_of: datetime | None = Field(
        default=None,
        description="Corte bitemporal efetivamente solicitado; nulo significa estado corrente.",
    )
    total: int = 0
    checks: list[CheckFerramenta] = Field(default_factory=list)
    observacao: str | None = None

    def linhas(self) -> int:
        return len(self.checks)


def executar_qualidade_do_ente(ctx: ToolContext, entrada: QualidadeDoEnteIn) -> QualidadeDoEnteOut:
    """Checks em falha/aviso sobre o número que o ente vê (Sprint 26), sem reexecutá-los.

    **Por que um check pode sair sem os dois números.** ``freshness`` mede *dias de
    atraso*, não reais, e não se ancora em nenhuma entrega — a ausência é justamente o que
    ele reporta. Sem entrega não há ``source_ref``, e devolver ``esquerda``/``direita``
    nesse caso seria oferecer dois números sem procedência a um modelo que os citaria como
    fatos apurados. O check continua aparecendo, com status e ``resumo`` em texto: o gestor
    precisa saber que a fonte está atrasada, e essa informação não é um valor fiscal.
    """
    abertos = quality_service.selo_do_ente(
        ctx.session, entrada.ente, entrada.periodo, as_of=entrada.as_of
    )
    checks: list[CheckFerramenta] = []
    for check in abertos:
        tem_fonte = check.source_ref is not None
        checks.append(
            CheckFerramenta(
                check_codigo=check.check_codigo,
                rotulo=check.rotulo,
                fonte=check.fonte,
                status=check.status,
                periodo=check.periodo,
                versao_entrega=check.versao_entrega,
                esquerda=check.esquerda if tem_fonte else None,
                direita=check.direita if tem_fonte else None,
                diferenca=check.diferenca if tem_fonte else None,
                tolerancia=check.tolerancia if tem_fonte else None,
                resumo=None if tem_fonte else _resumo_sem_fonte(check),
                source_ref=check.source_ref,
            )
        )
    return QualidadeDoEnteOut(
        ente=entrada.ente,
        periodo=entrada.periodo,
        as_of=entrada.as_of,
        total=len(checks),
        checks=checks,
        observacao=(
            None
            if checks
            else (
                f"Nenhum check em falha ou aviso aberto para {entrada.ente}"
                + (f" em {entrada.periodo}" if entrada.periodo else "")
                + ". Isso não é atestado de exatidão: significa que as verificações "
                "executadas passaram."
            )
        ),
    )


def _resumo_sem_fonte(check: CheckOut) -> str:
    """Texto do check que não se ancora numa entrega (ver docstring da ferramenta)."""
    detalhe = check.detalhe or {}
    motivo = detalhe.get("motivo") or detalhe.get("regra")
    partes = [f"{check.rotulo}: {check.status}"]
    if motivo:
        partes.append(str(motivo))
    if check.esquerda is not None and check.tolerancia is not None:
        partes.append(
            f"medido {texto(check.esquerda)} contra tolerância {texto(check.tolerancia)} "
            f"(em dias, não em R$)"
        )
    partes.append("sem entrega conferida — este check mede a ausência, não um valor publicado")
    return "; ".join(partes)


# --------------------------------------------------------------------------- #
# 7. alertas_do_ente
# --------------------------------------------------------------------------- #
class AlertasDoEnteIn(EnteToolInput):
    """Entrada de ``alertas_do_ente`` — sem parâmetro de ordenação, de propósito."""


class ContadoresAlerta(ToolOutput):
    critico: int = 0
    atencao: int = 0
    informativo: int = 0
    total: int = 0


class AlertaFerramenta(ToolOutput):
    id: str
    categoria: str
    severidade: str
    titulo: str
    motivo_legal: str
    acao_sugerida: str
    prazo: date | None = None
    status: str
    indicador: str | None = None
    periodo: str | None = None
    link: str | None = None
    memoria: dict[str, str | None] = Field(default_factory=dict)
    source_ref: SourceRef | None = None


class AlertasDoEnteOut(ToolOutput):
    ente: str
    gerado_em: datetime | None = None
    contadores: ContadoresAlerta = Field(default_factory=ContadoresAlerta)
    alertas: list[AlertaFerramenta] = Field(default_factory=list)
    observacao: str | None = None

    def linhas(self) -> int:
        return len(self.alertas)


def executar_alertas_do_ente(ctx: ToolContext, entrada: AlertasDoEnteIn) -> AlertasDoEnteOut:
    """Fila de alertas do ente, **na ordem que o motor determinou** (Sprint 15).

    A ferramenta não aceita critério de ordenação e não devolve ``prioridade`` como
    número. É deliberado: priorizar é regra auditável em ``alerts/rules.py``, e a decisão
    registrada no plano (§8) é que o modelo **explica** a ordem, não a produz. Expor um
    peso numérico convidaria a reordenar; a ordem da lista já é a resposta.
    """
    fila = alerts_service.listar_fila(
        ctx.session, ctx.principal, escopo="ente", cod_ibge=entrada.ente
    )
    alertas = [
        AlertaFerramenta(
            id=a.id,
            categoria=a.categoria,
            severidade=a.severidade,
            titulo=a.titulo,
            motivo_legal=a.motivo_legal,
            acao_sugerida=a.acao_sugerida,
            prazo=a.prazo,
            status=a.status,
            indicador=a.indicador,
            periodo=a.periodo,
            link=a.link,
            memoria={str(k): texto(v) for k, v in (a.memoria or {}).items()},
            source_ref=a.source_ref,
        )
        for a in fila.alertas
    ]
    return AlertasDoEnteOut(
        ente=entrada.ente,
        gerado_em=fila.gerado_em,
        contadores=ContadoresAlerta(
            critico=fila.contadores.critico,
            atencao=fila.contadores.atencao,
            informativo=fila.contadores.informativo,
            total=fila.contadores.total,
        ),
        alertas=alertas,
        observacao=(
            None
            if alertas
            else (
                f"Nenhum alerta ativo para {entrada.ente}. A fila é avaliada na leitura, "
                f"então isto reflete o estado de agora — não um período fechado."
            )
        ),
    )


# --------------------------------------------------------------------------- #
# 8. comparar_com_coorte
# --------------------------------------------------------------------------- #
class CompararComCoorteIn(EnteToolInput):
    """Entrada de ``comparar_com_coorte``."""

    indicador: str | None = Field(
        default=None,
        max_length=64,
        description="Indicador a comparar. Ausente ⇒ o que a plataforma tiver apurado.",
    )
    coorte: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Código da coorte, ou o critério: 'porte', 'regiao' ou 'pib' (a faixa daquele "
            "critério em que o ente se enquadra). Ausente ⇒ a coorte padrão do ente."
        ),
    )
    periodo: str | None = Field(default=None, max_length=16, description="Período canônico.")


class DistribuicaoFerramenta(ToolOutput):
    minimo: Decimal
    p25: Decimal
    mediana: Decimal
    p75: Decimal
    maximo: Decimal


class CompararComCoorteOut(ToolOutput):
    ente: str
    indicador: str | None = None
    rotulo: str | None = None
    unidade: str | None = None
    #: ``teto`` ou ``piso`` — sem isto, "acima da mediana" não tem sinal definido.
    sentido: str | None = None
    periodo: str | None = None
    as_of: datetime | None = None
    coorte: str | None = None
    coorte_rotulo: str | None = None
    disponivel: bool = False
    valor_ente: Decimal | None = None
    posicao: int | None = None
    percentil: Decimal | None = None
    quantidade: int | None = None
    entes_com_valor: int | None = None
    distribuicao: DistribuicaoFerramenta | None = None
    observacao: str | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)

    def linhas(self) -> int:
        return 1 if self.disponivel else 0


def executar_comparar_com_coorte(
    ctx: ToolContext, entrada: CompararComCoorteIn
) -> CompararComCoorteOut:
    """Posição do ente dentro da coorte, reusando ``benchmark.build_benchmark``.

    ``sentido`` acompanha o número porque sem ele a comparação é ambígua: estar acima da
    mediana é ruim num teto (pessoal, dívida) e bom num piso (saúde, educação). Um modelo
    que receba só a posição escreve a frase errada com a estatística certa.

    A ausência de comparação é resposta (404 do benchmark ⇒ ``disponivel=false``): coorte
    sem amostra ou indicador não apurado não devem virar exceção, porque "não há com quem
    comparar" é exatamente o que o gestor precisa ouvir.
    """
    try:
        resposta = benchmark_service.build_benchmark(
            ctx.session,
            cod_ibge=entrada.ente,
            indicador=entrada.indicador,
            coorte=entrada.coorte,
            periodo=entrada.periodo,
            as_of=entrada.as_of,
        )
    except AppError as exc:
        return CompararComCoorteOut(
            ente=entrada.ente,
            indicador=entrada.indicador,
            periodo=entrada.periodo,
            observacao=(
                f"Sem comparação disponível: {exc.detail or exc.title}. A plataforma não "
                f"compara com uma coorte que não tem amostra."
            ),
        )
    return CompararComCoorteOut(
        ente=entrada.ente,
        indicador=resposta.indicador,
        rotulo=resposta.indicador_rotulo,
        unidade=resposta.unidade,
        sentido=resposta.sentido,
        periodo=resposta.periodo,
        as_of=resposta.as_of,
        coorte=resposta.coorte.codigo,
        coorte_rotulo=resposta.coorte.rotulo,
        disponivel=True,
        valor_ente=resposta.ente.valor,
        posicao=resposta.ente.posicao,
        percentil=resposta.ente.percentil,
        quantidade=resposta.quantidade,
        entes_com_valor=resposta.cobertura.entes_com_valor,
        distribuicao=DistribuicaoFerramenta(
            minimo=resposta.distribuicao.minimo,
            p25=resposta.distribuicao.p25,
            mediana=resposta.distribuicao.mediana,
            p75=resposta.distribuicao.p75,
            maximo=resposta.distribuicao.maximo,
        ),
        source_refs=list(resposta.source_refs),
    )


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #
def _h_serie(ctx: ToolContext, entrada: Any) -> ToolOutput:
    return executar_serie_historica(ctx, entrada)


def _h_limites(ctx: ToolContext, entrada: Any) -> ToolOutput:
    return executar_limites_do_ente(ctx, entrada)


def _h_receita(ctx: ToolContext, entrada: Any) -> ToolOutput:
    return executar_drill_receita(ctx, entrada)


def _h_despesa(ctx: ToolContext, entrada: Any) -> ToolOutput:
    return executar_drill_despesa(ctx, entrada)


def _h_cobertura(ctx: ToolContext, entrada: Any) -> ToolOutput:
    return executar_cobertura_do_ente(ctx, entrada)


def _h_qualidade(ctx: ToolContext, entrada: Any) -> ToolOutput:
    return executar_qualidade_do_ente(ctx, entrada)


def _h_alertas(ctx: ToolContext, entrada: Any) -> ToolOutput:
    return executar_alertas_do_ente(ctx, entrada)


def _h_coorte(ctx: ToolContext, entrada: Any) -> ToolOutput:
    return executar_comparar_com_coorte(ctx, entrada)


def ferramentas() -> tuple[Tool, ...]:
    """As oito da IA-1b, na ordem em que um gestor as usaria."""
    return (
        Tool(
            nome="serie_historica",
            descricao=(
                "Evolução de um indicador do ente período a período, com valor, percentual "
                "e faixa em cada ponto. Use para responder 'como isso vem se comportando', "
                "'piorou ou melhorou' e 'desde quando está acima do limite'. Cada ponto traz "
                "a entrega que o originou — períodos diferentes têm versões diferentes."
            ),
            entrada=SerieHistoricaIn,
            saida=SerieHistoricaOut,
            handler=_h_serie,
            capacidade="ver",
            recebe_ente=True,
            saida_tem_numero_fiscal=True,
        ),
        Tool(
            nome="limites_do_ente",
            descricao=(
                "Todos os limites legais do ente num período (pessoal, dívida, operações de "
                "crédito, garantias, mínimos de saúde e educação) com valor, faixa, teto e "
                "distância até o teto e até o alerta. Use para 'estou dentro dos limites?' "
                "e para varrer a conformidade sem pedir indicador por indicador."
            ),
            entrada=LimitesDoEnteIn,
            saida=LimitesDoEnteOut,
            handler=_h_limites,
            capacidade="ver",
            recebe_ente=True,
            saida_tem_numero_fiscal=True,
        ),
        Tool(
            nome="drill_receita",
            descricao=(
                "Abre a receita do ente por origem, um nível por vez (previsto, arrecadado, "
                "percentual de realização). Chame sem 'node' para as raízes e de novo com o "
                "código de um filho para descer. Use para 'de onde vem a receita' e 'qual "
                "origem caiu'."
            ),
            entrada=DrillReceitaIn,
            saida=DrillOut,
            handler=_h_receita,
            capacidade="ver",
            recebe_ente=True,
            saida_tem_numero_fiscal=True,
        ),
        Tool(
            nome="drill_despesa",
            descricao=(
                "Abre a despesa do ente um nível por vez, por função (em que política "
                "pública se gastou) ou por natureza (pessoal, custeio, investimento), com os "
                "estágios empenhado/liquidado/pago. Use para 'em que o município gasta' e "
                "'o que cresceu'. Os dois eixos são recortes do mesmo total: não os some."
            ),
            entrada=DrillDespesaIn,
            saida=DrillOut,
            handler=_h_despesa,
            capacidade="ver",
            recebe_ente=True,
            saida_tem_numero_fiscal=True,
        ),
        Tool(
            nome="cobertura_do_ente",
            descricao=(
                "Diz para quantos entes e períodos uma página da plataforma de fato "
                "responde, e se este ente tem dado nela. Use SEMPRE que um dado esperado "
                "estiver ausente, antes de concluir qualquer coisa: distingue 'o ente não "
                "publicou' de 'a plataforma ainda não carregou'. Não devolve valor fiscal."
            ),
            entrada=CoberturaDoEnteIn,
            saida=CoberturaDoEnteOut,
            handler=_h_cobertura,
            capacidade="ver",
            recebe_ente=True,
            saida_tem_numero_fiscal=False,
        ),
        Tool(
            nome="qualidade_do_ente",
            descricao=(
                "Verificações de qualidade em falha ou aviso sobre os números do ente "
                "(conciliações entre relatórios, soma de filhos, atualidade da fonte). Use "
                "antes de afirmar um número que a plataforma marcou como divergente, e para "
                "responder 'posso confiar neste dado'."
            ),
            entrada=QualidadeDoEnteIn,
            saida=QualidadeDoEnteOut,
            handler=_h_qualidade,
            capacidade="ver",
            recebe_ente=True,
            saida_tem_numero_fiscal=True,
        ),
        Tool(
            nome="alertas_do_ente",
            descricao=(
                "Fila de alertas ativos do ente, já priorizada pelo motor de regras, com "
                "motivo legal, ação sugerida e prazo de cada um. Use para 'o que eu preciso "
                "resolver primeiro'. A ordem vem da regra, é auditável e não deve ser "
                "reordenada: explique por que o primeiro é o primeiro."
            ),
            entrada=AlertasDoEnteIn,
            saida=AlertasDoEnteOut,
            handler=_h_alertas,
            capacidade="ver",
            recebe_ente=True,
            saida_tem_numero_fiscal=True,
        ),
        Tool(
            nome="comparar_com_coorte",
            descricao=(
                "Posição do ente frente a entes comparáveis (coorte por porte, região ou "
                "PIB) num indicador: valor, posição, percentil e a distribuição da coorte. "
                "Use para 'isso é alto?' — a resposta depende de com quem se compara. "
                "Devolve o sentido (teto ou piso), sem o qual 'acima da mediana' não tem "
                "sinal definido."
            ),
            entrada=CompararComCoorteIn,
            saida=CompararComCoorteOut,
            handler=_h_coorte,
            capacidade="ver",
            recebe_ente=True,
            saida_tem_numero_fiscal=True,
        ),
    )
