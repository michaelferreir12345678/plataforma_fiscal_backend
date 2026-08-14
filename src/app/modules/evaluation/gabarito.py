"""Oráculo da avaliação — o gabarito **derivado do banco**, não escrito à mão.

O conjunto dourado diz *qual* indicador a pergunta cobra; quanto ele vale, quem diz é o
banco, aqui, no momento da execução. A diferença não é estilística:

- gabarito escrito no arquivo envelhece em silêncio na primeira mudança de cálculo, e
  passa a reprovar respostas corretas (ou, pior, a aprovar as erradas);
- gabarito derivado do banco reprova exatamente uma coisa — a resposta que **diverge do
  que a plataforma tem**, que é a definição operacional de alucinação numérica aqui.

**Este módulo não passa pelo caminho do assistente de propósito.** Ele lê ``gold`` com
SQL próprio, curto e direto. Se reutilizasse ``retriever``/``build_document``, estaria
conferindo o assistente contra ele mesmo: qualquer defeito na leitura apareceria nos dois
lados e se cancelaria. Um oráculo tem de ter caminho independente do que ele julga — é a
mesma razão pela qual a validação fiscal da Sprint 28 compara contra o demonstrativo
publicado, e não contra outra consulta nossa.

Nenhum cálculo fiscal acontece aqui: o módulo **lê** valores já materializados. A regra
do CLAUDE.md §7 (cálculo só em ``indicators/``) fica preservada.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion.models import DimEntrega
from app.modules.result.models import FatoResultado

#: Unidades — o que a conferência precisa saber para não comparar % com R$.
UNIDADE_PCT = "PERCENTUAL"
UNIDADE_RS = "MONETARIO"


@dataclass(frozen=True)
class ValorDeReferencia:
    """O que o banco tem para (ente, período, indicador). ``valor is None`` ⇒ não existe.

    ``valor is None`` é informação de primeira classe, não ausência de informação: é o
    gabarito das perguntas da categoria ``ausente``. A resposta que citar um número ali
    está inventando, e é isso que a avaliação precisa provar.
    """

    ente: str
    periodo: str | None
    indicador: str
    valor: Decimal | None
    unidade: str | None = None
    versao_entrega: str | None = None
    relatorio: str | None = None
    #: Alternativas aceitáveis para a mesma grandeza (ex.: % e R$ do mesmo indicador).
    #: Citar qualquer uma é citar o número certo; citar nenhuma é o que reprova.
    equivalentes: tuple[Decimal, ...] = ()

    @property
    def existe(self) -> bool:
        return self.valor is not None

    def valores_aceitos(self) -> tuple[Decimal, ...]:
        if self.valor is None:
            return ()
        return (self.valor, *self.equivalentes)


def _versao_vigente(session: Session, *, cod_ibge: str, relatorio: str, periodo: str) -> str | None:
    return session.scalar(
        select(DimEntrega.versao_entrega).where(
            DimEntrega.cod_ibge == cod_ibge,
            DimEntrega.relatorio == relatorio,
            DimEntrega.periodo == periodo,
            DimEntrega.vigente.is_(True),
        )
    )


def _do_mart(
    session: Session, *, cod_ibge: str, periodo: str, indicador: str
) -> ValorDeReferencia | None:
    """Lê ``gold.mart_indicador`` na versão vigente do período.

    A ordenação por ``versao_entrega`` decrescente é o desempate para o caso de
    retificação (a versão mais nova supera, nunca apaga — §2, regra 3).
    """
    linha = session.scalars(
        select(MartIndicador)
        .where(
            MartIndicador.cod_ibge == cod_ibge,
            MartIndicador.periodo == periodo,
            MartIndicador.indicador == indicador,
        )
        .order_by(MartIndicador.versao_entrega.desc())
        .limit(1)
    ).first()
    if linha is None:
        return None
    pct = linha.valor_pct_rcl
    reais = linha.valor_rs
    principal = pct if pct is not None else reais
    if principal is None:
        return None
    equivalentes = tuple(v for v in (pct, reais) if v is not None and v != principal)
    fonte = linha.source_ref or {}
    return ValorDeReferencia(
        ente=cod_ibge,
        periodo=periodo,
        indicador=indicador,
        valor=principal,
        unidade=UNIDADE_PCT if pct is not None else UNIDADE_RS,
        versao_entrega=linha.versao_entrega,
        relatorio=str(fonte.get("relatorio")) if fonte.get("relatorio") else None,
        equivalentes=equivalentes,
    )


def _rcl(session: Session, *, cod_ibge: str, periodo: str) -> ValorDeReferencia | None:
    versao = _versao_vigente(session, cod_ibge=cod_ibge, relatorio="RREO", periodo=periodo)
    if versao is None:
        return None
    linha = session.scalars(
        select(FatoRcl).where(
            FatoRcl.cod_ibge == cod_ibge,
            FatoRcl.periodo_ref == periodo,
            FatoRcl.versao_entrega == versao,
        )
    ).first()
    if linha is None:
        return None
    return ValorDeReferencia(
        ente=cod_ibge,
        periodo=periodo,
        indicador="rcl",
        valor=linha.rcl_12m,
        unidade=UNIDADE_RS,
        versao_entrega=versao,
        relatorio="RREO",
    )


def _resultado_primario(
    session: Session, *, cod_ibge: str, periodo: str
) -> ValorDeReferencia | None:
    versao = _versao_vigente(session, cod_ibge=cod_ibge, relatorio="RREO", periodo=periodo)
    if versao is None:
        return None
    linha = session.scalars(
        select(FatoResultado).where(
            FatoResultado.cod_ibge == cod_ibge,
            FatoResultado.periodo == periodo,
            FatoResultado.versao_entrega == versao,
        )
    ).first()
    if linha is None or linha.resultado_primario is None:
        return None
    return ValorDeReferencia(
        ente=cod_ibge,
        periodo=periodo,
        indicador="resultado_primario",
        valor=linha.resultado_primario,
        unidade=UNIDADE_RS,
        versao_entrega=versao,
        relatorio="RREO",
    )


#: Indicadores que não moram em ``mart_indicador``. O mapa é explícito para que a
#: ausência de um código aqui signifique "procure no mart", e não "não sei procurar".
_LEITORES_ESPECIAIS = {
    "rcl": _rcl,
    "resultado_primario": _resultado_primario,
}


def valor_de_referencia(
    session: Session, *, cod_ibge: str, periodo: str | None, indicador: str
) -> ValorDeReferencia:
    """O que o banco tem. Sempre devolve um laudo — ``valor=None`` quando não há dado."""
    vazio = ValorDeReferencia(ente=cod_ibge, periodo=periodo, indicador=indicador, valor=None)
    if periodo is None:
        return vazio
    leitor = _LEITORES_ESPECIAIS.get(indicador)
    achado = (
        leitor(session, cod_ibge=cod_ibge, periodo=periodo)
        if leitor is not None
        else _do_mart(session, cod_ibge=cod_ibge, periodo=periodo, indicador=indicador)
    )
    return achado or vazio


def periodo_efetivo(session: Session, *, cod_ibge: str, periodo: str | None) -> str | None:
    """Período que a plataforma usaria quando a pergunta não informa um.

    Reproduz a resolução do ``retriever`` (última entrega RREO vigente) por consulta
    própria — o oráculo precisa saber *sobre qual período* vai cobrar o número, senão
    compararia o valor do bimestre errado.
    """
    if periodo is not None:
        return periodo
    return session.scalar(
        select(DimEntrega.periodo)
        .where(
            DimEntrega.cod_ibge == cod_ibge,
            DimEntrega.relatorio == "RREO",
            DimEntrega.vigente.is_(True),
        )
        .order_by(DimEntrega.periodo.desc())
        .limit(1)
    )


def ha_entrega_mais_recente(
    session: Session, *, cod_ibge: str, periodo: str, relatorio: str = "RREO"
) -> str | None:
    """Período da entrega mais recente que **supera** a consultada — ou ``None``.

    É o gabarito da categoria ``defasado``: a defasagem que a resposta tem de sinalizar
    existe objetivamente no banco, e a avaliação a confirma antes de cobrar o sinal.
    """
    mais_recente = session.scalar(
        select(DimEntrega.periodo)
        .where(
            DimEntrega.cod_ibge == cod_ibge,
            DimEntrega.relatorio == relatorio,
            DimEntrega.vigente.is_(True),
        )
        .order_by(DimEntrega.periodo.desc())
        .limit(1)
    )
    if mais_recente is None or mais_recente <= periodo:
        return None
    return str(mais_recente)
