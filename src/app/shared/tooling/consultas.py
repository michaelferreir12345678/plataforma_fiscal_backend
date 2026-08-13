"""Consulta guiada — catálogo parametrizado (§6.1 do plano de MCP, Sprint IA-1b).

**A ideia, e por que ela vale mais que SQL livre.** Em vez de o modelo escrever a
consulta, ele escolhe uma de um catálogo curado e preenche parâmetros. Cada consulta aqui
é SQL escrito por gente, revisado e versionado; o modelo decide *qual* usar e *com quais
valores*. O que se ganha: o SQL é auditável **antes** de rodar (não depois), o plano de
execução é previsível, a superfície de ataque é zero — não existe string de SQL vinda do
modelo — e, o que mais importa nesta base:

> **a vigência está sempre correta porque quem escreveu a consulta sabia do A14.**

Essa frase é o motivo de o catálogo existir. Os achados A14/A15 — *"versão que existe,
vigência que não se declara"* — foram leitura que somava versões superadas junto com a
vigente. Custaram duas sprints, uma migration corretiva e reprocessamento em produção. Um
modelo escrevendo ``SUM(valor_rs) FROM gold.mart_indicador`` reproduz o A14 com sintaxe
impecável e resultado plausível, e ninguém revisa SQL embutido numa resposta em prosa.

Aqui isso é **estrutural**, não lembrado: toda consulta parte de :func:`entregas_vigentes`,
que devolve exatamente uma ``versao_entrega`` por ``(ente, período)``, e junta o fato por
essa chave. Uma versão superada não é filtrada depois — ela **não tem como entrar**, porque
a chave do join só existe para a vigente. É a mesma razão por que a IA-4 exige views: a
vigência fica resolvida por quem sabe do problema, não por quem gera SQL.

**Escopo: agregado × nominal.** Duas regras diferentes, ambas obrigatórias e aplicadas no
mesmo funil (:func:`executar`), nunca por consulta:

- consulta **agregada** ("quem na minha carteira estourou o teto") é *restringida* ao
  escopo licenciado — pedir "todos os municípios do CE" significa, sem ambiguidade, todos
  os que quem pergunta pode ver;
- consulta **nominal** (o usuário nomeia os entes) *afirma* o escopo com
  ``assert_ente_in_scope``, para que a recusa seja explícita e distinga fora-da-carteira de
  sem-licença. Omitir em silêncio um ente que o gestor nomeou seria responder outra
  pergunta.

**Por que não existe uma ferramenta ``listar_consultas``.** O catálogo já viaja no contexto
como a própria lista de ferramentas. Transformá-lo numa chamada gastaria um passo do agente
para descobrir o que já estava disponível — o erro que o §2.3 do plano descreve (dicionário
virando ferramenta). A recusa útil ("não sei, e este é o catálogo do que sei responder")
sai de graça: o modelo enxerga os nomes e as descrições sem gastar chamada.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field
from sqlalchemy import Select, and_, nulls_first, nulls_last, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.catalog.models import DimEnte
from app.modules.indicators import rotulos
from app.modules.indicators.models import MartIndicador
from app.modules.ingestion.models import DimEntrega
from app.shared.scope import assert_ente_in_scope, carteira_scope_ibges
from app.shared.source_ref import SourceRef, fonte_gravada
from app.shared.tooling.base import Tool, ToolContext, ToolInput, ToolOutput, ToolRegistry

#: Teto de linhas de qualquer consulta guiada. Contenção, não paginação: uma resposta de
#: 5.570 linhas não é lida por ninguém e estoura o contexto do modelo antes de informar.
LIMITE_MAXIMO = 200
LIMITE_PADRAO = 50

#: Faixas de ``gold.mart_indicador``. ``prudencial`` e ``estourado`` são as que motivam uma
#: pergunta; ``normal`` existe para permitir o complemento ("quem está confortável").
FAIXAS = ("normal", "alerta", "prudencial", "estourado")

RELATORIOS = ("RREO", "RGF", "DCA", "MSC")


# --------------------------------------------------------------------------- #
# Vigência — a peça que impede o A14
# --------------------------------------------------------------------------- #
def entregas_vigentes(relatorio: str, *, as_of: datetime | None = None) -> Select[Any]:
    """``(cod_ibge, periodo, versao_entrega)`` da entrega **vigente** de ``relatorio``.

    Sem ``as_of``: a marcada como vigente (a retificação supera a anterior, não a apaga —
    regra invariante 3 do `CLAUDE.md`). Com ``as_of``: a que **estava** vigente naquele
    instante, ou seja, a de maior ``homologada_em`` até ele; ``DISTINCT ON`` garante uma
    por ``(ente, período)``, que é o que torna o join seguro.

    Usar isto como **fonte do join** (e não como filtro adicional) é o que dá a garantia:
    um fato de versão superada não casa com nenhuma linha desta subconsulta.
    """
    colunas = (DimEntrega.cod_ibge, DimEntrega.periodo, DimEntrega.versao_entrega)
    if as_of is None:
        return select(*colunas).where(
            DimEntrega.relatorio == relatorio, DimEntrega.vigente.is_(True)
        )
    return (
        select(*colunas)
        .distinct(DimEntrega.cod_ibge, DimEntrega.periodo)
        .where(DimEntrega.relatorio == relatorio, DimEntrega.homologada_em <= as_of)
        .order_by(
            DimEntrega.cod_ibge, DimEntrega.periodo, DimEntrega.homologada_em.desc()
        )
    )


def mart_vigente(
    *, relatorio: str = "RREO", as_of: datetime | None = None
) -> tuple[Select[Any], Any]:
    """``select`` de ``gold.mart_indicador`` já restrito à entrega vigente.

    Devolve também a subconsulta de entregas para quem precisar de outras colunas dela.
    Todo acesso ao mart neste módulo passa por aqui — não há caminho alternativo.
    """
    vigentes = entregas_vigentes(relatorio, as_of=as_of).subquery("vigentes")
    stmt = select(MartIndicador).join(
        vigentes,
        and_(
            MartIndicador.cod_ibge == vigentes.c.cod_ibge,
            MartIndicador.periodo == vigentes.c.periodo,
            MartIndicador.versao_entrega == vigentes.c.versao_entrega,
        ),
    )
    return stmt, vigentes


# --------------------------------------------------------------------------- #
# Contrato do catálogo
# --------------------------------------------------------------------------- #
class ConsultaInput(ToolInput):
    """Entrada comum. ``as_of`` é de primeira classe também aqui (G5).

    Note que **não** existe campo ``ente``: estas consultas atravessam entes, e o registro
    recusaria o nome sem ``recebe_ente=True``. O escopo entra pelo funil de
    :func:`executar`, que é obrigatório e não tem desvio.
    """

    as_of: datetime | None = Field(
        default=None,
        description=(
            "Instante bitemporal. Ausente ⇒ as versões vigentes; presente ⇒ as que estavam "
            "vigentes naquele instante."
        ),
    )
    limite: int = Field(
        default=LIMITE_PADRAO,
        ge=1,
        le=LIMITE_MAXIMO,
        description=f"Máximo de linhas (teto do sistema: {LIMITE_MAXIMO}).",
    )


@dataclass(frozen=True)
class Escopo:
    """Escopo já resolvido — o que uma consulta guiada pode enxergar.

    Construído **só** por :func:`executar`. Como toda função de consulta o exige como
    argumento, nenhuma delas consegue rodar sem que o escopo tenha sido resolvido antes —
    a garantia é do tipo, não da disciplina de quem escreve a próxima consulta.
    """

    cods: tuple[str, ...]

    @property
    def vazio(self) -> bool:
        return not self.cods


Executor = Callable[[ToolContext, Any, Escopo], ToolOutput]


@dataclass(frozen=True)
class ConsultaGuiada:
    """Uma consulta do catálogo: SQL de gente, com a vigência declarada por escrito."""

    nome: str
    descricao: str
    entrada: type[ConsultaInput]
    saida: type[ToolOutput]
    executar: Executor
    #: Como esta consulta resolve vigência. Campo obrigatório e revisado — é o resumo que
    #: um auditor lê para conferir que o A14 não voltou por aqui.
    vigencia: str
    #: A consulta nomeia entes (⇒ afirma escopo) ou agrega (⇒ restringe ao escopo)?
    nominal: bool = False
    saida_tem_numero_fiscal: bool = True


# --------------------------------------------------------------------------- #
# 1. entes_que_ultrapassaram_faixa
# --------------------------------------------------------------------------- #
class UltrapassaramFaixaIn(ConsultaInput):
    indicador: str = Field(
        min_length=2, max_length=64, description="Código do indicador (ex.: pessoal_executivo)."
    )
    faixas: list[str] = Field(
        default_factory=lambda: ["prudencial", "estourado"],
        description=f"Faixas de interesse. Valores: {', '.join(FAIXAS)}.",
    )
    periodo_inicial: str | None = Field(default=None, max_length=16)
    periodo_final: str | None = Field(default=None, max_length=16)
    uf: str | None = Field(
        default=None, max_length=2, description="Sigla da UF, para recortar o escopo."
    )
    populacao_minima: int | None = Field(default=None, ge=0)
    populacao_maxima: int | None = Field(default=None, ge=0)


class LinhaEnteIndicador(ToolOutput):
    cod_ibge: str
    nome: str | None = None
    uf: str | None = None
    periodo: str
    faixa: str | None = None
    valor_pct: Decimal | None = None
    valor_rs: Decimal | None = None
    teto_pct: Decimal | None = None
    denominador: str | None = None
    versao_entrega: str
    source_ref: SourceRef | None = None


class UltrapassaramFaixaOut(ToolOutput):
    consulta: str = "entes_que_ultrapassaram_faixa"
    indicador: str
    rotulo: str
    faixas: list[str] = Field(default_factory=list)
    total: int = 0
    truncado: bool = False
    resultados: list[LinhaEnteIndicador] = Field(default_factory=list)
    observacao: str | None = None

    def linhas(self) -> int:
        return len(self.resultados)


def _executar_ultrapassaram(
    ctx: ToolContext, entrada: UltrapassaramFaixaIn, escopo: Escopo
) -> UltrapassaramFaixaOut:
    rotulo = rotulos.rotulo(entrada.indicador)
    faixas = [f for f in entrada.faixas if f in FAIXAS]
    if not faixas:
        raise AppError(
            status=422,
            title="Faixa desconhecida",
            detail=f"Faixas válidas: {', '.join(FAIXAS)}. Recebido: {entrada.faixas}.",
        )
    if escopo.vazio:
        return UltrapassaramFaixaOut(
            indicador=entrada.indicador, rotulo=rotulo, faixas=faixas, observacao=_SEM_ESCOPO
        )

    stmt, _ = mart_vigente(as_of=entrada.as_of)
    stmt = (
        stmt.add_columns(DimEnte.nome, DimEnte.uf)
        .join(DimEnte, DimEnte.cod_ibge == MartIndicador.cod_ibge, isouter=True)
        .where(
            MartIndicador.indicador == entrada.indicador,
            MartIndicador.faixa.in_(faixas),
            MartIndicador.cod_ibge.in_(escopo.cods),
        )
    )
    if entrada.periodo_inicial:
        stmt = stmt.where(MartIndicador.periodo >= entrada.periodo_inicial)
    if entrada.periodo_final:
        stmt = stmt.where(MartIndicador.periodo <= entrada.periodo_final)
    if entrada.uf:
        stmt = stmt.where(DimEnte.uf == entrada.uf.upper())
    if entrada.populacao_minima is not None:
        stmt = stmt.where(DimEnte.populacao >= entrada.populacao_minima)
    if entrada.populacao_maxima is not None:
        stmt = stmt.where(DimEnte.populacao <= entrada.populacao_maxima)
    stmt = stmt.order_by(
        MartIndicador.periodo.desc(), nulls_last(MartIndicador.valor_pct_rcl.desc())
    ).limit(entrada.limite + 1)

    linhas = [
        _linha(mart, nome, uf) for mart, nome, uf in ctx.session.execute(stmt).all()
    ]
    truncado = len(linhas) > entrada.limite
    linhas = linhas[: entrada.limite]
    return UltrapassaramFaixaOut(
        indicador=entrada.indicador,
        rotulo=rotulo,
        faixas=faixas,
        total=len(linhas),
        truncado=truncado,
        resultados=linhas,
        observacao=_observacao(linhas, truncado, entrada.limite),
    )


# --------------------------------------------------------------------------- #
# 2. ranking_indicador_na_coorte
# --------------------------------------------------------------------------- #
class RankingIn(ConsultaInput):
    indicador: str = Field(min_length=2, max_length=64)
    periodo: str | None = Field(
        default=None, max_length=16, description="Período canônico. Ausente ⇒ o mais recente."
    )
    ordem: str = Field(
        default="desc",
        description="'desc' (maiores primeiro) ou 'asc' (menores primeiro).",
    )
    uf: str | None = Field(default=None, max_length=2)
    populacao_minima: int | None = Field(default=None, ge=0)
    populacao_maxima: int | None = Field(default=None, ge=0)


class RankingOut(ToolOutput):
    consulta: str = "ranking_indicador_na_coorte"
    indicador: str
    rotulo: str
    periodo: str | None = None
    ordem: str = "desc"
    total: int = 0
    truncado: bool = False
    resultados: list[LinhaEnteIndicador] = Field(default_factory=list)
    observacao: str | None = None

    def linhas(self) -> int:
        return len(self.resultados)


def _executar_ranking(ctx: ToolContext, entrada: RankingIn, escopo: Escopo) -> RankingOut:
    """Ordena; **não** calcula percentil.

    Ordenar é recorte; percentil e distribuição são estatística sobre a coorte, e essa já
    existe pronta em ``benchmark`` (``comparar_com_coorte``). Recalculá-la aqui criaria uma
    segunda régua para o mesmo número — exatamente o que a §7 do `CLAUDE.md` proíbe.
    """
    rotulo = rotulos.rotulo(entrada.indicador)
    if entrada.ordem not in ("asc", "desc"):
        raise AppError(
            status=422, title="Ordem inválida", detail="Use 'asc' ou 'desc'."
        )
    if escopo.vazio:
        return RankingOut(indicador=entrada.indicador, rotulo=rotulo, observacao=_SEM_ESCOPO)

    periodo = entrada.periodo or _periodo_mais_recente(
        ctx.session, indicador=entrada.indicador, cods=escopo.cods, as_of=entrada.as_of
    )
    if periodo is None:
        return RankingOut(
            indicador=entrada.indicador,
            rotulo=rotulo,
            observacao=(
                f"Não há nenhum período com '{entrada.indicador}' apurado no seu escopo. "
                f"Sem apuração não há ranking — a plataforma não estima o que não calculou."
            ),
        )

    stmt, _ = mart_vigente(as_of=entrada.as_of)
    stmt = (
        stmt.add_columns(DimEnte.nome, DimEnte.uf)
        .join(DimEnte, DimEnte.cod_ibge == MartIndicador.cod_ibge, isouter=True)
        .where(
            MartIndicador.indicador == entrada.indicador,
            MartIndicador.periodo == periodo,
            MartIndicador.cod_ibge.in_(escopo.cods),
        )
    )
    if entrada.uf:
        stmt = stmt.where(DimEnte.uf == entrada.uf.upper())
    if entrada.populacao_minima is not None:
        stmt = stmt.where(DimEnte.populacao >= entrada.populacao_minima)
    if entrada.populacao_maxima is not None:
        stmt = stmt.where(DimEnte.populacao <= entrada.populacao_maxima)
    coluna = MartIndicador.valor_pct_rcl
    ordenacao = (
        nulls_last(coluna.desc()) if entrada.ordem == "desc" else nulls_first(coluna.asc())
    )
    stmt = stmt.order_by(ordenacao, MartIndicador.cod_ibge).limit(entrada.limite + 1)

    linhas = [_linha(mart, nome, uf) for mart, nome, uf in ctx.session.execute(stmt).all()]
    truncado = len(linhas) > entrada.limite
    linhas = linhas[: entrada.limite]
    return RankingOut(
        indicador=entrada.indicador,
        rotulo=rotulo,
        periodo=periodo,
        ordem=entrada.ordem,
        total=len(linhas),
        truncado=truncado,
        resultados=linhas,
        observacao=_observacao(linhas, truncado, entrada.limite),
    )


def _periodo_mais_recente(
    session: Session, *, indicador: str, cods: Sequence[str], as_of: datetime | None
) -> str | None:
    """Último período com o indicador apurado **em versão vigente** dentro do escopo."""
    stmt, _ = mart_vigente(as_of=as_of)
    stmt = (
        stmt.with_only_columns(MartIndicador.periodo)
        .where(MartIndicador.indicador == indicador, MartIndicador.cod_ibge.in_(cods))
        .order_by(MartIndicador.periodo.desc())
        .limit(1)
    )
    return session.scalar(stmt)


# --------------------------------------------------------------------------- #
# 3. serie_do_indicador_por_ente
# --------------------------------------------------------------------------- #
class SeriePorEnteIn(ConsultaInput):
    indicador: str = Field(min_length=2, max_length=64)
    entes: list[str] = Field(
        min_length=1,
        max_length=20,
        description="Códigos IBGE (7 dígitos, ou 2 para o ente estadual).",
    )
    periodo_inicial: str | None = Field(default=None, max_length=16)
    periodo_final: str | None = Field(default=None, max_length=16)


class SeriePorEnteOut(ToolOutput):
    consulta: str = "serie_do_indicador_por_ente"
    indicador: str
    rotulo: str
    entes: list[str] = Field(default_factory=list)
    total: int = 0
    truncado: bool = False
    resultados: list[LinhaEnteIndicador] = Field(default_factory=list)
    observacao: str | None = None

    def linhas(self) -> int:
        return len(self.resultados)


def _executar_serie_por_ente(
    ctx: ToolContext, entrada: SeriePorEnteIn, escopo: Escopo
) -> SeriePorEnteOut:
    """Consulta **nominal**: o escopo de cada ente pedido foi afirmado em :func:`executar`.

    Uma linha por ``(ente, período)`` — nunca duas. É aqui que a diferença aparece a olho
    nu: sem o join de vigência, um ente com retificação apareceria duas vezes no mesmo
    período, com dois valores diferentes, e qualquer soma sobre isso seria o A14.
    """
    rotulo = rotulos.rotulo(entrada.indicador)
    stmt, _ = mart_vigente(as_of=entrada.as_of)
    stmt = (
        stmt.add_columns(DimEnte.nome, DimEnte.uf)
        .join(DimEnte, DimEnte.cod_ibge == MartIndicador.cod_ibge, isouter=True)
        .where(
            MartIndicador.indicador == entrada.indicador,
            MartIndicador.cod_ibge.in_(list(entrada.entes)),
        )
    )
    if entrada.periodo_inicial:
        stmt = stmt.where(MartIndicador.periodo >= entrada.periodo_inicial)
    if entrada.periodo_final:
        stmt = stmt.where(MartIndicador.periodo <= entrada.periodo_final)
    stmt = stmt.order_by(MartIndicador.cod_ibge, MartIndicador.periodo).limit(entrada.limite + 1)

    linhas = [_linha(mart, nome, uf) for mart, nome, uf in ctx.session.execute(stmt).all()]
    truncado = len(linhas) > entrada.limite
    linhas = linhas[: entrada.limite]
    sem_dado = sorted(set(entrada.entes) - {linha.cod_ibge for linha in linhas})
    observacao = _observacao(linhas, truncado, entrada.limite)
    if sem_dado:
        faltantes = ", ".join(sem_dado[:5]) + (" e outros" if len(sem_dado) > 5 else "")
        observacao = (
            (observacao + " ") if observacao else ""
        ) + f"Sem '{entrada.indicador}' apurado para: {faltantes}."
    return SeriePorEnteOut(
        indicador=entrada.indicador,
        rotulo=rotulo,
        entes=list(entrada.entes),
        total=len(linhas),
        truncado=truncado,
        resultados=linhas,
        observacao=observacao,
    )


# --------------------------------------------------------------------------- #
# 4. entes_sem_entrega_da_fonte
# --------------------------------------------------------------------------- #
class SemEntregaIn(ConsultaInput):
    relatorio: str = Field(
        default="RREO", description=f"Relatório da fonte. Valores: {', '.join(RELATORIOS)}."
    )
    periodo: str = Field(
        min_length=4, max_length=16, description="Período canônico a conferir (ex.: '2024-B6')."
    )
    uf: str | None = Field(default=None, max_length=2)


class EnteSemEntrega(ToolOutput):
    cod_ibge: str
    nome: str | None = None
    uf: str | None = None


class SemEntregaOut(ToolOutput):
    """Ausência de entrega — por construção, **sem número fiscal**: não há valor a citar."""

    consulta: str = "entes_sem_entrega_da_fonte"
    relatorio: str
    periodo: str
    entes_no_escopo: int = 0
    entes_com_dado: int = 0
    total: int = 0
    truncado: bool = False
    resultados: list[EnteSemEntrega] = Field(default_factory=list)
    observacao: str | None = None

    def linhas(self) -> int:
        return len(self.resultados)


def _executar_sem_entrega(
    ctx: ToolContext, entrada: SemEntregaIn, escopo: Escopo
) -> SemEntregaOut:
    """Quem, no escopo, **não** tem entrega vigente do relatório no período.

    "Sem entrega" é ausência de versão **vigente** — não ausência de linha em
    ``dim_entrega``. A diferença importa: um ente cuja única entrega foi superada e ainda
    não substituída não tem dado publicável, e contá-lo como entregue esconderia
    exatamente o caso que a pergunta procura.
    """
    if escopo.vazio:
        return SemEntregaOut(
            relatorio=entrada.relatorio, periodo=entrada.periodo, observacao=_SEM_ESCOPO
        )
    if entrada.relatorio not in RELATORIOS:
        raise AppError(
            status=422,
            title="Relatório desconhecido",
            detail=f"Relatórios válidos: {', '.join(RELATORIOS)}.",
        )

    vigentes = entregas_vigentes(entrada.relatorio, as_of=entrada.as_of).subquery("vigentes")
    entregues = set(
        ctx.session.scalars(
            select(vigentes.c.cod_ibge).where(
                vigentes.c.periodo == entrada.periodo,
                vigentes.c.cod_ibge.in_(escopo.cods),
            )
        )
    )
    faltantes = [cod for cod in escopo.cods if cod not in entregues]

    stmt = select(DimEnte.cod_ibge, DimEnte.nome, DimEnte.uf).where(
        DimEnte.cod_ibge.in_(faltantes)
    )
    if entrada.uf:
        stmt = stmt.where(DimEnte.uf == entrada.uf.upper())
    stmt = stmt.order_by(DimEnte.uf, DimEnte.nome).limit(entrada.limite + 1)
    achados = [
        EnteSemEntrega(cod_ibge=cod, nome=nome, uf=uf)
        for cod, nome, uf in ctx.session.execute(stmt).all()
    ]
    truncado = len(achados) > entrada.limite
    achados = achados[: entrada.limite]
    return SemEntregaOut(
        relatorio=entrada.relatorio,
        periodo=entrada.periodo,
        entes_no_escopo=len(escopo.cods),
        entes_com_dado=len(entregues),
        total=len(achados),
        truncado=truncado,
        resultados=achados,
        observacao=(
            f"{len(achados)} de {len(escopo.cods)} entes do seu escopo sem {entrada.relatorio} "
            f"vigente em {entrada.periodo}. A ausência pode ser do ente (não publicou) ou da "
            f"nossa carga; a Central de Dados mostra qual dos dois."
            if achados
            else (
                f"Todos os entes do seu escopo com cadastro têm {entrada.relatorio} vigente "
                f"em {entrada.periodo}."
            )
        ),
    )


# --------------------------------------------------------------------------- #
# Peças comuns
# --------------------------------------------------------------------------- #
_SEM_ESCOPO = (
    "Sua organização não tem entes licenciados na carteira, então não há sobre o que "
    "consultar. Isso não é ausência de dado: é ausência de escopo."
)


def _linha(mart: MartIndicador, nome: str | None, uf: str | None) -> LinhaEnteIndicador:
    return LinhaEnteIndicador(
        cod_ibge=mart.cod_ibge,
        nome=nome,
        uf=uf,
        periodo=mart.periodo,
        faixa=mart.faixa,
        valor_pct=mart.valor_pct_rcl,
        valor_rs=mart.valor_rs,
        teto_pct=mart.teto_pct,
        denominador=mart.denominador,
        versao_entrega=mart.versao_entrega,
        source_ref=fonte_gravada(
            mart.source_ref,
            SourceRef(
                relatorio="RREO", periodo=mart.periodo, versao_entrega=mart.versao_entrega
            ),
        ),
    )


def _observacao(linhas: Sequence[Any], truncado: bool, limite: int) -> str | None:
    if truncado:
        return (
            f"Resposta truncada em {limite} linhas. Estreite o recorte (período, UF, faixa "
            f"populacional) — a lista completa existe, mas não cabe numa resposta."
        )
    if not linhas:
        return (
            "Nenhuma linha atende ao recorte. Isso é um resultado, não uma falha: pode "
            "significar que ninguém no seu escopo está nessa situação."
        )
    return None


# --------------------------------------------------------------------------- #
# O catálogo
# --------------------------------------------------------------------------- #
CATALOGO: tuple[ConsultaGuiada, ...] = (
    ConsultaGuiada(
        nome="entes_que_ultrapassaram_faixa",
        descricao=(
            "Lista os entes do seu escopo que ficaram numa faixa de risco (alerta, "
            "prudencial ou estourado) de um indicador, num intervalo de períodos, com "
            "recorte opcional por UF e faixa populacional. Responde perguntas do tipo "
            "'quais municípios acima de 50 mil habitantes ultrapassaram o prudencial de "
            "pessoal em 2024'. Cada linha traz o valor, o teto e a entrega que a originou."
        ),
        entrada=UltrapassaramFaixaIn,
        saida=UltrapassaramFaixaOut,
        executar=_executar_ultrapassaram,
        vigencia=(
            "join com a entrega vigente de RREO por (ente, período): uma versão por par, "
            "nunca a superada. Com as_of, a que estava vigente naquele instante."
        ),
    ),
    ConsultaGuiada(
        nome="ranking_indicador_na_coorte",
        descricao=(
            "Ordena os entes do seu escopo por um indicador num período, do maior para o "
            "menor (ou o inverso), com recorte por UF e porte. Responde 'quem está pior' e "
            "'onde eu me situo na fila'. Para percentil e distribuição da coorte, use "
            "comparar_com_coorte: esta consulta ordena, não calcula estatística."
        ),
        entrada=RankingIn,
        saida=RankingOut,
        executar=_executar_ranking,
        vigencia=(
            "mesma resolução por (ente, período); o período padrão é o mais recente que "
            "tem apuração vigente dentro do escopo, não o mais recente do calendário."
        ),
    ),
    ConsultaGuiada(
        nome="serie_do_indicador_por_ente",
        descricao=(
            "Série de um indicador para um conjunto de entes nomeados, período a período — "
            "a consulta de comparação lado a lado ('compare a dívida de A, B e C nos "
            "últimos dois anos'). Entes fora da sua carteira ou sem licença são recusados "
            "explicitamente, não omitidos em silêncio."
        ),
        entrada=SeriePorEnteIn,
        saida=SeriePorEnteOut,
        executar=_executar_serie_por_ente,
        vigencia=(
            "uma linha por (ente, período) garantida pelo join com a entrega vigente — é "
            "o caso em que a retificação duplicaria a série se a vigência não fosse "
            "resolvida."
        ),
        nominal=True,
    ),
    ConsultaGuiada(
        nome="entes_sem_entrega_da_fonte",
        descricao=(
            "Lista os entes do seu escopo que não têm entrega vigente de um relatório "
            "(RREO, RGF, DCA, MSC) num período. Responde 'quem ainda não publicou' e "
            "sustenta a cobrança de prazo. Não devolve valor fiscal: é uma consulta sobre "
            "ausência."
        ),
        entrada=SemEntregaIn,
        saida=SemEntregaOut,
        executar=_executar_sem_entrega,
        vigencia=(
            "ausência é definida como ausência de entrega VIGENTE — um ente cuja única "
            "entrega foi superada conta como sem entrega, que é o caso que a pergunta "
            "procura."
        ),
        saida_tem_numero_fiscal=False,
    ),
)


# --------------------------------------------------------------------------- #
# O funil — escopo aqui, e só aqui
# --------------------------------------------------------------------------- #
def executar(consulta: ConsultaGuiada, ctx: ToolContext, entrada: ConsultaInput) -> ToolOutput:
    """Resolve o escopo e executa. **Único** caminho de execução do catálogo.

    Nenhuma consulta recebe ``ente``, então o gate do envelope (que só age sobre
    ``EnteToolInput``) não as alcança — e é exatamente por isso que o escopo é aplicado
    aqui, num lugar só, e não dentro de cada consulta. Uma consulta futura que esquecesse
    de filtrar herdaria a falha; assim, ela nem recebe o dado para filtrar: recebe o
    :class:`Escopo` já resolvido e é obrigada a usá-lo.
    """
    nomeados = _entes_nomeados(entrada)
    if nomeados:
        # Nominal: recusa explícita, com a distinção entre fora-da-carteira e sem-licença.
        for cod in nomeados:
            assert_ente_in_scope(ctx.session, ctx.principal, cod)
        escopo = Escopo(cods=tuple(nomeados))
    else:
        # Agregada: o conjunto licenciado é o universo da pergunta.
        escopo = Escopo(cods=tuple(sorted(carteira_scope_ibges(ctx.session, ctx.principal))))
    return consulta.executar(ctx, entrada, escopo)


def _entes_nomeados(entrada: ConsultaInput) -> list[str]:
    valores = getattr(entrada, "entes", None)
    if not valores:
        return []
    return list(dict.fromkeys(str(v) for v in valores))


def _tool(consulta: ConsultaGuiada) -> Tool:
    def handler(ctx: ToolContext, entrada: Any) -> ToolOutput:
        return executar(consulta, ctx, entrada)

    return Tool(
        nome=consulta.nome,
        descricao=consulta.descricao,
        entrada=consulta.entrada,
        saida=consulta.saida,
        handler=handler,
        capacidade="ver",
        # As consultas atravessam entes; o escopo é aplicado no funil acima, não pelo
        # envelope. Declarar ``recebe_ente=True`` seria mentira: não existe um ente.
        recebe_ente=False,
        saida_tem_numero_fiscal=consulta.saida_tem_numero_fiscal,
    )


def registrar(registro: ToolRegistry) -> ToolRegistry:
    """Registra o catálogo de consultas guiadas como ferramentas tipadas.

    Uma ferramenta por consulta, e não uma ferramenta ``consulta_guiada(nome, params)``:
    assim cada uma leva o **seu** JSON Schema até o modelo, o ``extra='forbid'`` age sobre
    os parâmetros reais e o registro aplica as mesmas validações de carga. Um dicionário
    de parâmetros genérico devolveria a validação para runtime — e para o modelo.
    """
    for consulta in CATALOGO:
        registro.register(_tool(consulta))
    return registro
