"""Apuração dos mínimos constitucionais de saúde e educação.

Fonte primária e invariável: RREO Anexos 12 (ASPS) e 8 (MDE/FUNDEB). SIOPS,
SIOPE e FNDE são lidos somente nos endpoints de enriquecimento.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.cash_rap import service as cash_rap_service
from app.modules.catalog import repository as catalog_repo
from app.modules.catalog import service as catalog_service
from app.modules.health_edu import repository
from app.modules.health_edu.models import FatoEducacao, FatoSaude
from app.modules.health_edu.schemas import (
    EducacaoDetalhe,
    EnriquecimentoDetalhe,
    EnriquecimentoItem,
    FundebDetalhe,
    MemoriaComponente,
    MemoriaMinimo,
    MinimoArvoreOut,
    MinimosProjecao,
    ProjecaoIndicador,
    ProjecaoSerieItem,
    SaudeDetalhe,
    SerieMinimoItem,
    SerieMinimoOut,
)
from app.modules.indicators import service as indicators_service
from app.modules.ingestion import repository as ingestion_repo
from app.modules.ingestion.models import DimEntrega, SilverRreo
from app.shared.ausencia import ausencia_de_entrega
from app.shared.hierarchy import HierarchyNode, build_drill_envelope
from app.shared.source_ref import SourceRef

ZERO = Decimal(0)
HUNDRED = Decimal(100)
_PERIODO_RE = re.compile(r"^(\d{4})-B([1-6])$")
_VERSAO_NA = "nao_aplicavel"

_ANEXO_SAUDE = "Anexo 12 — ASPS"
_ANEXO_EDUCACAO = "Anexo 08 — MDE"

_SUBFUNCOES: dict[str, str] = {
    "ASPS_SUBFUNCAO_ATENCAO_BASICA": "10.ATENCAO_BASICA",
    "ASPS_SUBFUNCAO_ASSISTENCIA_HOSPITALAR_E_AMBULATORIAL": (
        "10.ASSISTENCIA_HOSPITALAR_E_AMBULATORIAL"
    ),
    "ASPS_SUBFUNCAO_SUPORTE_PROFILATICO_E_TERAPEUTICO": (
        "10.SUPORTE_PROFILATICO_E_TERAPEUTICO"
    ),
    "ASPS_SUBFUNCAO_VIGILANCIA_SANITARIA": "10.VIGILANCIA_SANITARIA",
    "ASPS_SUBFUNCAO_VIGILANCIA_EPIDEMIOLOGICA": "10.VIGILANCIA_EPIDEMIOLOGICA",
    "ASPS_SUBFUNCAO_ALIMENTACAO_E_NUTRICAO": "10.ALIMENTACAO_E_NUTRICAO",
    "ASPS_SUBFUNCAO_DEMAIS": "10.DEMAIS_SUBFUNCOES",
}


@dataclass(frozen=True)
class Contexto:
    cod_ibge: str
    periodo: str
    ano: int
    bimestre: int
    esfera: str
    versao_rreo: str
    as_of: datetime
    requested_as_of: datetime | None
    source_saude: SourceRef
    source_educacao: SourceRef


def _normalizar(value: str | None) -> str:
    if not value:
        return ""
    nfkd = unicodedata.normalize("NFKD", value)
    return " ".join(
        "".join(c for c in nfkd if not unicodedata.combining(c)).upper().split()
    )


def _periodo(periodo: str) -> tuple[int, int]:
    match = _PERIODO_RE.match(periodo)
    if match is None:
        raise AppError(
            status=422,
            title="Período inválido",
            detail=f"Saúde/Educação exige período RREO 'AAAA-Bn'; recebido '{periodo}'.",
        )
    return int(match.group(1)), int(match.group(2))


def _contexto(
    session: Session, cod_ibge: str, periodo: str, as_of: datetime | None
) -> Contexto:
    ano, bimestre = _periodo(periodo)
    ente = catalog_service.refresh_dim_ente(session, cod_ibge)
    if ente is None or ente.esfera not in ("municipal", "estadual"):
        raise AppError(
            status=404,
            title="Ente sem esfera",
            detail=f"Cadastre o ente {cod_ibge} no SICONFI antes de apurar os mínimos.",
        )
    versao = ingestion_repo.resolve_versao(
        session,
        cod_ibge=cod_ibge,
        relatorio="RREO",
        periodo=periodo,
        as_of=as_of,
    )
    if versao is None:
        raise ausencia_de_entrega(
            session,
            cod_ibge=cod_ibge,
            relatorio="RREO",
            periodo=periodo,
            title="RREO ausente",
            detail=f"Sem RREO vigente para {cod_ibge} em {periodo}.",
        )
    homologada = ingestion_repo.entrega_homologada_em(
        session,
        cod_ibge=cod_ibge,
        relatorio="RREO",
        periodo=periodo,
        versao_entrega=versao,
    )
    instante = as_of or homologada
    if instante is None:
        raise AppError(status=500, title="Entrega sem data", detail="RREO sem homologada_em.")
    return Contexto(
        cod_ibge=cod_ibge,
        periodo=periodo,
        ano=ano,
        bimestre=bimestre,
        esfera=ente.esfera,
        versao_rreo=versao,
        as_of=instante,
        requested_as_of=as_of,
        source_saude=SourceRef(
            relatorio="RREO", anexo=_ANEXO_SAUDE,
            periodo=periodo, versao_entrega=versao,
        ),
        source_educacao=SourceRef(
            relatorio="RREO", anexo=_ANEXO_EDUCACAO,
            periodo=periodo, versao_entrega=versao,
        ),
    )


def _estagio(ctx: Contexto) -> Literal["liquidado", "empenhado"]:
    return "empenhado" if ctx.bimestre == 6 else "liquidado"


def _linhas_codigo(linhas: list[SilverRreo], codigo: str) -> list[SilverRreo]:
    wanted = _normalizar(codigo).replace(" ", "_")
    return [
        row for row in linhas
        if _normalizar(row.cod_conta).replace(" ", "_") == wanted
    ]


def _valor(
    linhas: list[SilverRreo],
    codigo: str,
    *,
    estagio: str | None = None,
    obrigatorio: bool = True,
) -> Decimal:
    candidatas = _linhas_codigo(linhas, codigo)
    if estagio is not None:
        token = {
            "empenhado": "EMPENH",
            "liquidado": "LIQUID",
            "pago": "PAG",
        }.get(estagio, _normalizar(estagio))
        filtradas = [row for row in candidatas if token in _normalizar(row.coluna)]
        if filtradas:
            candidatas = filtradas
        else:
            # Conectores podem já emitir somente o estágio computável, sob coluna VALOR.
            candidatas = [
                row for row in candidatas if _normalizar(row.coluna) in ("", "VALOR")
            ]
    valores = [Decimal(row.valor) for row in candidatas if row.valor is not None]
    if not valores:
        if obrigatorio:
            raise AppError(
                status=422,
                title="RREO incompleto",
                detail=f"Anexo obrigatório sem o componente canônico '{codigo}'.",
            )
        return ZERO
    return sum(valores, ZERO)


def _limite(session: Session, indicador: str, esfera: str) -> Decimal:
    limite = catalog_repo.get_limite(
        session, indicador=indicador, esfera=esfera, poder=""
    )
    if limite is None:
        catalog_service.seed_limites_legais(session)
        limite = catalog_repo.get_limite(
            session, indicador=indicador, esfera=esfera, poder=""
        )
    if limite is None or limite.sentido != "piso":
        raise AppError(
            status=500,
            title="Piso não configurado",
            detail=f"Ausente gold.dim_limite_legal: {indicador}/{esfera}.",
        )
    return Decimal(limite.teto_pct)


def _percentual(numerador: Decimal, denominador: Decimal, codigo: str) -> Decimal:
    if denominador <= ZERO:
        raise AppError(
            status=422,
            title="Base inválida",
            detail=f"O denominador '{codigo}' deve ser positivo; recebido {denominador}.",
        )
    return numerador / denominador * HUNDRED


def _expurgo(
    session: Session,
    ctx: Contexto,
    area: Literal["saude", "educacao"],
    reportado_rreo: Decimal,
) -> tuple[Decimal, str, SourceRef | None, list[str], datetime | None, str]:
    """Consome internamente a API de domínio da Sprint 10, sempre fonte a fonte."""
    periodo_rgf = cash_rap_service.rgf_periodo_de_rreo(ctx.periodo)
    if periodo_rgf is None:
        return (
            reportado_rreo,
            _VERSAO_NA,
            None,
            [],
            None,
            "rreo_periodo_sem_rgf_correspondente",
        )
    versao = ingestion_repo.resolve_versao(
        session,
        cod_ibge=ctx.cod_ibge,
        relatorio="RGF",
        periodo=periodo_rgf,
        as_of=ctx.requested_as_of,
    )
    if versao is None:
        # O Anexo 5 costuma ser publicado apenas no fechamento anual. Nos
        # bimestres intermediarios, preservamos o expurgo explicitamente
        # reportado no proprio Anexo 8/12; o B6 continua exigindo a Sprint 10.
        if ctx.bimestre < 6:
            return (
                reportado_rreo,
                _VERSAO_NA,
                None,
                [],
                None,
                "rreo_fallback_sem_entrega_rgf",
            )
        raise AppError(
            status=404,
            title="RGF ausente para expurgo",
            detail=(
                f"A apuração de {ctx.periodo} exige o RGF {periodo_rgf} "
                "para expurgar RPNP sem lastro."
            ),
        )
    try:
        fontes = cash_rap_service.disponibilidades_por_fonte(
            session, ctx.cod_ibge, periodo_rgf, as_of=ctx.requested_as_of, soft=False
        )
    except AppError as exc:
        if ctx.bimestre < 6 and exc.status == 404:
            return (
                reportado_rreo,
                _VERSAO_NA,
                None,
                [],
                None,
                "rreo_fallback_rgf_sem_anexo_5",
            )
        raise

    def vinculada(descricao: str) -> bool:
        norm = _normalizar(descricao)
        if "EXCETO" in norm:
            return False
        if area == "saude":
            return "SAUDE" in norm or "SUS" in norm
        return "EDUCAC" in norm or "FUNDEB" in norm or "MDE" in norm

    selecionadas = [fonte for fonte in fontes if vinculada(fonte.fonte_descricao)]
    # Traço/célula não aplicável no A5 vira None; não há inscrição a expurgar nessa fonte.
    total = sum((fonte.rpnp_sem_lastro or ZERO for fonte in selecionadas), ZERO)
    source = SourceRef(
        relatorio="RGF", anexo="Anexo 05 — Disponibilidade de Caixa",
        periodo=periodo_rgf, versao_entrega=versao,
    )
    homologada = ingestion_repo.entrega_homologada_em(
        session, cod_ibge=ctx.cod_ibge, relatorio="RGF", periodo=periodo_rgf,
        versao_entrega=versao,
    )
    return (
        total, versao, source,
        [fonte.fonte_descricao for fonte in selecionadas],
        ctx.requested_as_of or homologada,
        "sprint10_rgf_anexo_5",
    )


def _componentes_lineage(
    fonte: SourceRef, rgf: SourceRef | None
) -> tuple[SourceRef, ...] | None:
    """Linhagem do mart: RREO é a base; o RGF entra só quando o expurgo veio dele."""
    return (fonte, rgf) if rgf is not None else None


def _materializar_mart(
    session: Session,
    ctx: Contexto,
    *,
    indicador: str,
    valor_rs: Decimal,
    base_valor: Decimal,
    denominador: str,
    fonte: SourceRef,
    source_rgf: SourceRef | None,
) -> None:
    """Publica o mínimo em ``gold.mart_indicador`` (Sprint 25C).

    Sem esta linha, os mínimos ficavam invisíveis para tudo que lê a gold: semáforo do
    dashboard, carteira, monitor de limites, motor de alertas e — o pedido da sprint —
    o benchmarking com a coorte. A base declarada é a do próprio demonstrativo (impostos
    e transferências; receitas do FUNDEB), **nunca** a RCL.
    """
    indicators_service.classificar_sobre_base(
        session,
        ctx.cod_ibge,
        ctx.periodo,
        indicador,
        valor_rs,
        base_valor,
        denominador=denominador,
        versao_entrega=ctx.versao_rreo,
        as_of=ctx.requested_as_of,
        source_ref=fonte,
        source_components=_componentes_lineage(fonte, source_rgf),
    )


def _componente(
    codigo: str,
    rotulo: str,
    valor: Decimal,
    operacao: Literal["base", "soma", "subtrai", "resultado", "referencia"],
    source: SourceRef,
    as_of: datetime,
) -> MemoriaComponente:
    return MemoriaComponente(
        codigo=codigo, rotulo=rotulo, valor=valor, operacao=operacao,
        source_ref=source, as_of=as_of,
    )


def _materializar_subfuncoes(
    session: Session, ctx: Contexto, linhas: list[SilverRreo]
) -> None:
    dims = {row.codigo for row in repository.list_funcoes(session)}
    rows: list[dict[str, Any]] = []
    estagio = _estagio(ctx)
    for codigo_rreo, codigo_dim in _SUBFUNCOES.items():
        if codigo_dim not in dims:
            continue
        if not _linhas_codigo(linhas, codigo_rreo):
            continue
        empenhado = _valor(linhas, codigo_rreo, estagio="empenhado", obrigatorio=False)
        liquidado = _valor(linhas, codigo_rreo, estagio="liquidado", obrigatorio=False)
        pago = _valor(linhas, codigo_rreo, estagio="pago", obrigatorio=False)
        computado = empenhado if estagio == "empenhado" else liquidado
        rows.append(
            {
                "cod_ibge": ctx.cod_ibge,
                "periodo": ctx.periodo,
                "funcao_codigo": codigo_dim,
                "empenhado": empenhado,
                "liquidado": liquidado,
                "pago": pago,
                "valor_computado": computado,
                "versao_rreo": ctx.versao_rreo,
            }
        )
    repository.replace_saude_subfuncoes(
        session,
        cod_ibge=ctx.cod_ibge,
        periodo=ctx.periodo,
        versao_rreo=ctx.versao_rreo,
        rows=rows,
    )


def _apurar_saude(
    session: Session, ctx: Contexto
) -> tuple[FatoSaude, SourceRef | None, dict[str, Any]]:
    linhas = repository.read_rreo_anexo(
        session,
        cod_ibge=ctx.cod_ibge,
        periodo=ctx.periodo,
        versao_entrega=ctx.versao_rreo,
        numero="12",
    )
    if not linhas:
        raise AppError(
            status=404,
            title="RREO Anexo 12 ausente",
            detail=f"Ingira o RREO ASPS oficial para {ctx.cod_ibge}/{ctx.periodo}.",
        )
    estagio = _estagio(ctx)
    base = _valor(linhas, "ASPS_BASE_IMPOSTOS_TRANSFERENCIAS")
    despesa_bruta = _valor(linhas, "ASPS_DESPESA_TOTAL", estagio=estagio)
    deducoes = _valor(linhas, "ASPS_DEDUCOES_OUTRAS", estagio=estagio, obrigatorio=False)
    if not _linhas_codigo(linhas, "ASPS_DEDUCOES_OUTRAS"):
        deducoes = sum(
            (
                _valor(linhas, codigo, estagio=estagio, obrigatorio=False)
                for codigo in ("ASPS_DEDUCAO_XIV", "ASPS_DEDUCAO_XV")
            ),
            ZERO,
        )
    reportado = _valor(linhas, "ASPS_RPNP_SEM_LASTRO_REPORTADO", obrigatorio=False)
    rpnp, versao_rgf, source_rgf, fontes, as_of_rgf, metodo_expurgo = _expurgo(
        session, ctx, "saude", reportado
    )
    aplicada = despesa_bruta - deducoes - rpnp
    piso = _limite(session, "saude_minimo", ctx.esfera)
    valor_minimo = base * piso / HUNDRED
    pct = _percentual(aplicada, base, "base ASPS de impostos+transferências")
    valores = {
        "cod_ibge": ctx.cod_ibge,
        "periodo": ctx.periodo,
        "base_impostos_transferencias": base,
        "despesa_bruta": despesa_bruta,
        "deducoes_outras": deducoes,
        "rpnp_sem_lastro": rpnp,
        "despesa_aplicada": aplicada,
        "pct_aplicado": pct,
        "minimo_pct": piso,
        "valor_minimo": valor_minimo,
        "abaixo_do_minimo": pct < piso,
        "versao_rreo": ctx.versao_rreo,
        "versao_rgf": versao_rgf,
    }
    repository.upsert_fato_saude(session, valores)
    _materializar_subfuncoes(session, ctx, linhas)
    fato = repository.get_fato_saude(
        session,
        cod_ibge=ctx.cod_ibge,
        periodo=ctx.periodo,
        versao_rreo=ctx.versao_rreo,
        versao_rgf=versao_rgf,
    )
    if fato is None:
        session.flush()
        fato = repository.get_fato_saude(
            session,
            cod_ibge=ctx.cod_ibge,
            periodo=ctx.periodo,
            versao_rreo=ctx.versao_rreo,
            versao_rgf=versao_rgf,
        )
    assert fato is not None
    _materializar_mart(
        session, ctx,
        indicador="saude_minimo",
        valor_rs=Decimal(fato.despesa_aplicada),
        base_valor=Decimal(fato.base_impostos_transferencias),
        denominador="impostos_transferencias",
        fonte=ctx.source_saude,
        source_rgf=source_rgf,
    )
    return fato, source_rgf, {
        "rpnp_reportado_rreo": reportado,
        "fontes_rgf_selecionadas": fontes,
        "periodo_rgf": source_rgf.periodo if source_rgf else None,
        "as_of_rgf": as_of_rgf.isoformat() if as_of_rgf else None,
        "metodo_expurgo": metodo_expurgo,
    }


def _apurar_educacao(
    session: Session, ctx: Contexto
) -> tuple[FatoEducacao, SourceRef | None, dict[str, Any]]:
    linhas = repository.read_rreo_anexo(
        session,
        cod_ibge=ctx.cod_ibge,
        periodo=ctx.periodo,
        versao_entrega=ctx.versao_rreo,
        numero="08",
    )
    if not linhas:
        raise AppError(
            status=404,
            title="RREO Anexo 8 ausente",
            detail=f"Ingira o RREO MDE oficial para {ctx.cod_ibge}/{ctx.periodo}.",
        )
    estagio = _estagio(ctx)
    base = _valor(linhas, "MDE_BASE_IMPOSTOS_TRANSFERENCIAS")
    impostos = _valor(linhas, "MDE_DESPESA_IMPOSTOS", estagio=estagio)
    fundeb_contribuicao = _valor(linhas, "MDE_TRANSFERENCIA_FUNDEB")
    deducoes = sum(
        (
            _valor(linhas, codigo, estagio=estagio, obrigatorio=False)
            for codigo in (
                "MDE_SUPERAVIT_EXERCICIO_ANTERIOR",
                "MDE_COMPLEMENTACAO_VAAF_EXERCICIO_ANTERIOR",
                "MDE_CANCELAMENTOS",
            )
        ),
        ZERO,
    )
    reportado = _valor(linhas, "MDE_RPNP_SEM_LASTRO_REPORTADO", obrigatorio=False)
    rpnp, versao_rgf, source_rgf, fontes, as_of_rgf, metodo_expurgo = _expurgo(
        session, ctx, "educacao", reportado
    )
    despesa_bruta = impostos + fundeb_contribuicao
    aplicada = despesa_bruta - deducoes - rpnp
    piso = _limite(session, "educacao_mde", ctx.esfera)
    valor_minimo = base * piso / HUNDRED
    pct = _percentual(aplicada, base, "base MDE de impostos+transferências")

    fundeb_base = _valor(linhas, "FUNDEB_BASE_PROFISSIONAIS")
    profissionais = _valor(linhas, "FUNDEB_PROFISSIONAIS", estagio=estagio)
    fundeb_piso = _limite(session, "fundeb_profissionais", ctx.esfera)
    fundeb_pct = _percentual(profissionais, fundeb_base, "base principal do FUNDEB")
    fundeb_minimo = fundeb_base * fundeb_piso / HUNDRED

    valores = {
        "cod_ibge": ctx.cod_ibge,
        "periodo": ctx.periodo,
        "base_impostos_transferencias": base,
        "despesa_bruta": despesa_bruta,
        "despesa_impostos": impostos,
        "despesa_fundeb": fundeb_contribuicao,
        "deducoes_outras": deducoes,
        "rpnp_sem_lastro": rpnp,
        "despesa_aplicada": aplicada,
        "pct_aplicado": pct,
        "minimo_pct": piso,
        "valor_minimo": valor_minimo,
        "abaixo_do_minimo": pct < piso,
        "fundeb_base_profissionais": fundeb_base,
        "fundeb_aplicado_profissionais": profissionais,
        "fundeb_pct_profissionais": fundeb_pct,
        "fundeb_minimo_pct": fundeb_piso,
        "fundeb_valor_minimo": fundeb_minimo,
        "fundeb_abaixo_do_minimo": fundeb_pct < fundeb_piso,
        "versao_rreo": ctx.versao_rreo,
        "versao_rgf": versao_rgf,
    }
    repository.upsert_fato_educacao(session, valores)
    fato = repository.get_fato_educacao(
        session,
        cod_ibge=ctx.cod_ibge,
        periodo=ctx.periodo,
        versao_rreo=ctx.versao_rreo,
        versao_rgf=versao_rgf,
    )
    if fato is None:
        session.flush()
        fato = repository.get_fato_educacao(
            session,
            cod_ibge=ctx.cod_ibge,
            periodo=ctx.periodo,
            versao_rreo=ctx.versao_rreo,
            versao_rgf=versao_rgf,
        )
    assert fato is not None
    _materializar_mart(
        session, ctx,
        indicador="educacao_mde",
        valor_rs=Decimal(fato.despesa_aplicada),
        base_valor=Decimal(fato.base_impostos_transferencias),
        denominador="impostos_transferencias",
        fonte=ctx.source_educacao,
        source_rgf=source_rgf,
    )
    # O FUNDEB tem base própria (receitas principais do fundo) — publicá-lo sob a base de
    # impostos daria um percentual diferente do que a lei exige medir.
    _materializar_mart(
        session, ctx,
        indicador="fundeb_profissionais",
        valor_rs=Decimal(fato.fundeb_aplicado_profissionais),
        base_valor=Decimal(fato.fundeb_base_profissionais),
        denominador="fundeb",
        fonte=ctx.source_educacao,
        source_rgf=None,
    )
    return fato, source_rgf, {
        "rpnp_reportado_rreo": reportado,
        "fontes_rgf_selecionadas": fontes,
        "periodo_rgf": source_rgf.periodo if source_rgf else None,
        "as_of_rgf": as_of_rgf.isoformat() if as_of_rgf else None,
        "metodo_expurgo": metodo_expurgo,
    }


def _serie_item(
    fato: FatoSaude | FatoEducacao, source: SourceRef, as_of: datetime
) -> SerieMinimoItem:
    ano, bimestre = _periodo(fato.periodo)
    return SerieMinimoItem(
        periodo=fato.periodo,
        exercicio=ano,
        parcial=bimestre < 6,
        estagio_legal="empenhado" if bimestre == 6 else "liquidado",
        base_impostos_transferencias=fato.base_impostos_transferencias,
        despesa_aplicada=fato.despesa_aplicada,
        pct_aplicado=fato.pct_aplicado,
        minimo_pct=fato.minimo_pct,
        abaixo_do_minimo=fato.abaixo_do_minimo,
        source_ref=source,
        as_of=as_of,
    )


def _serie(
    session: Session,
    cod_ibge: str,
    periodo_atual: str,
    area: Literal["saude", "educacao"],
    as_of: datetime | None,
) -> list[SerieMinimoItem]:
    ano, bimestre = _periodo(periodo_atual)
    numero = "12" if area == "saude" else "08"
    itens: list[SerieMinimoItem] = []
    for periodo in repository.distinct_periodos_anexo(session, cod_ibge=cod_ibge, numero=numero):
        try:
            p_ano, p_bim = _periodo(periodo)
            if p_ano != ano or p_bim > bimestre:
                continue
            ctx = _contexto(session, cod_ibge, periodo, as_of)
            fato = (
                _apurar_saude(session, ctx)[0]
                if area == "saude"
                else _apurar_educacao(session, ctx)[0]
            )
            source = ctx.source_saude if area == "saude" else ctx.source_educacao
            itens.append(_serie_item(fato, source, ctx.as_of))
        except AppError:
            continue
    return sorted(itens, key=lambda item: item.periodo)


def _periodos_por_exercicio(
    session: Session,
    *,
    cod_ibge: str,
    numero: str,
    ano_limite: int,
    bimestre_limite: int,
) -> dict[int, list[str]]:
    """Períodos publicados por exercício, sem ultrapassar o corte consultado."""
    por_ano: dict[int, list[str]] = {}
    for periodo in repository.distinct_periodos_anexo(
        session, cod_ibge=cod_ibge, numero=numero
    ):
        try:
            ano, bimestre = _periodo(periodo)
        except AppError:
            continue
        if ano > ano_limite or (ano == ano_limite and bimestre > bimestre_limite):
            continue
        por_ano.setdefault(ano, []).append(periodo)
    return por_ano


def build_serie(
    session: Session,
    cod_ibge: str,
    periodo: str,
    area: Literal["saude", "educacao"],
    *,
    anos: int = 5,
    as_of: datetime | None = None,
) -> SerieMinimoOut:
    """Série plurianual do mínimo: **um ponto por exercício**, no seu fechamento.

    A comparação que o gestor faz é entre exercícios ("aplicamos mais ou menos que no
    ano passado?"), não entre bimestres de anos diferentes — 25% acumulados no 2º
    bimestre e 25% no 6º medem coisas distintas. Por isso cada exercício entra pelo
    período mais avançado que ele publicou (B6 quando fechado), e o exercício corrente
    entra pelo período consultado, marcado como ``parcial``.

    Exercícios sem Anexo 8/12 publicado **não viram ponto** e são devolvidos em
    ``exercicios_sem_dado``: a ausência aparece como ausência.
    """
    ano_atual, bimestre_atual = _periodo(periodo)
    numero = "12" if area == "saude" else "08"
    por_ano = _periodos_por_exercicio(
        session,
        cod_ibge=cod_ibge,
        numero=numero,
        ano_limite=ano_atual,
        bimestre_limite=bimestre_atual,
    )
    janela = list(range(ano_atual - anos + 1, ano_atual + 1))
    itens: list[SerieMinimoItem] = []
    minimo_pct = ZERO
    fonte: SourceRef | None = None
    instante: datetime | None = None
    for exercicio in janela:
        candidatos = por_ano.get(exercicio)
        if not candidatos:
            continue
        escolhido = max(candidatos, key=lambda p: _periodo(p)[1])
        try:
            ctx = _contexto(session, cod_ibge, escolhido, as_of)
            fato = (
                _apurar_saude(session, ctx)[0]
                if area == "saude"
                else _apurar_educacao(session, ctx)[0]
            )
        except AppError:
            continue
        source = ctx.source_saude if area == "saude" else ctx.source_educacao
        itens.append(_serie_item(fato, source, ctx.as_of))
        minimo_pct = Decimal(fato.minimo_pct)
        fonte, instante = source, ctx.as_of

    com_dado = [item.exercicio for item in itens]
    sem_dado = [ano for ano in janela if ano not in com_dado]
    anexo = _ANEXO_SAUDE if area == "saude" else _ANEXO_EDUCACAO
    if fonte is None:
        # Nem o período consultado apurou: devolvemos a moldura vazia com a fonte
        # esperada, para a tela dizer o que falta em vez de mostrar um gráfico vazio.
        ctx = _contexto(session, cod_ibge, periodo, as_of)
        fonte = ctx.source_saude if area == "saude" else ctx.source_educacao
        instante = ctx.as_of
        minimo_pct = _limite(
            session, "saude_minimo" if area == "saude" else "educacao_mde", ctx.esfera
        )
    observacao = None
    if sem_dado:
        observacao = (
            f"{len(com_dado)} de {anos} exercícios têm o RREO {anexo} publicado para este "
            f"ente; sem dado em {', '.join(str(a) for a in sem_dado)}. A série mostra só "
            "o que foi apurado — não há estimativa de exercício ausente."
        )
    assert instante is not None
    return SerieMinimoOut(
        cod_ibge=cod_ibge,
        periodo=periodo,
        indicador=area,
        minimo_pct=minimo_pct,
        anos_solicitados=anos,
        exercicios_com_dado=com_dado,
        exercicios_sem_dado=sem_dado,
        cobertura_completa=not sem_dado,
        observacao=observacao,
        data=itens,
        trajetoria_exercicio=_serie(session, cod_ibge, periodo, area, as_of),
        source_ref=fonte,
        as_of=instante,
    )


def build_saude(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> SaudeDetalhe:
    ctx = _contexto(session, cod_ibge, periodo, as_of)
    fato, source_rgf, detalhes = _apurar_saude(session, ctx)
    rpnp_as_of = (
        datetime.fromisoformat(detalhes["as_of_rgf"])
        if detalhes.get("as_of_rgf") else ctx.as_of
    )
    memoria = MemoriaMinimo(
        formula_aplicacao="despesa_aplicada = despesa_bruta − deduções − RPNP_sem_lastro",
        formula_percentual="pct_aplicado = despesa_aplicada ÷ impostos_e_transferências × 100",
        estagio_legal=_estagio(ctx),
        regra_expurgo=(
            "RPNP sem lastro é apurado fonte a fonte no RGF Anexo 5; somente vínculos "
            "positivos de Saúde/SUS são deduzidos."
        ),
        componentes=[
            _componente("base", "Impostos + transferências", fato.base_impostos_transferencias,
                        "base", ctx.source_saude, ctx.as_of),
            _componente("despesa_bruta", "ASPS computáveis antes dos expurgos",
                        fato.despesa_bruta, "soma", ctx.source_saude, ctx.as_of),
            _componente("deducoes", "Outras deduções do Anexo 12", fato.deducoes_outras,
                        "subtrai", ctx.source_saude, ctx.as_of),
            _componente("rpnp_sem_lastro", "RPNP sem disponibilidade de caixa",
                        fato.rpnp_sem_lastro, "subtrai", source_rgf or ctx.source_saude,
                        rpnp_as_of),
            _componente("aplicado", "ASPS válida para o piso", fato.despesa_aplicada,
                        "resultado", ctx.source_saude, ctx.as_of),
        ],
        detalhes={**detalhes, "base_nao_e_rcl": True, "fonte_primaria": "RREO"},
        source_ref=ctx.source_saude,
        as_of=ctx.as_of,
    )
    return SaudeDetalhe(
        cod_ibge=cod_ibge,
        periodo=periodo,
        esfera=ctx.esfera,
        base_impostos_transferencias=fato.base_impostos_transferencias,
        despesa_bruta=fato.despesa_bruta,
        deducoes_outras=fato.deducoes_outras,
        rpnp_sem_lastro=fato.rpnp_sem_lastro,
        despesa_aplicada=fato.despesa_aplicada,
        pct_aplicado=fato.pct_aplicado,
        minimo_pct=fato.minimo_pct,
        valor_minimo=fato.valor_minimo,
        abaixo_do_minimo=fato.abaixo_do_minimo,
        folga=fato.despesa_aplicada - fato.valor_minimo,
        projecao_pct=fato.pct_aplicado,
        versao_entrega=fato.versao_rreo,
        source_ref=ctx.source_saude,
        source_ref_expurgo=source_rgf,
        as_of=ctx.as_of,
        memoria_calculo=memoria,
        serie=_serie(session, cod_ibge, periodo, "saude", as_of),
    )


def build_educacao(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> EducacaoDetalhe:
    ctx = _contexto(session, cod_ibge, periodo, as_of)
    fato, source_rgf, detalhes = _apurar_educacao(session, ctx)
    rpnp_as_of = (
        datetime.fromisoformat(detalhes["as_of_rgf"])
        if detalhes.get("as_of_rgf") else ctx.as_of
    )
    memoria = MemoriaMinimo(
        formula_aplicacao=(
            "despesa_aplicada = MDE_impostos + transferência_ao_FUNDEB − deduções "
            "− RPNP_sem_lastro"
        ),
        formula_percentual="pct_aplicado = despesa_aplicada ÷ impostos_e_transferências × 100",
        estagio_legal=_estagio(ctx),
        regra_expurgo=(
            "RPNP sem lastro é apurado fonte a fonte no RGF Anexo 5; somente vínculos "
            "positivos de Educação/FUNDEB são deduzidos."
        ),
        componentes=[
            _componente("base", "Impostos + transferências", fato.base_impostos_transferencias,
                        "base", ctx.source_educacao, ctx.as_of),
            _componente("mde_impostos", "MDE custeada por impostos", fato.despesa_impostos,
                        "soma", ctx.source_educacao, ctx.as_of),
            _componente("transferencia_fundeb", "Transferência ao FUNDEB",
                        fato.despesa_fundeb, "soma", ctx.source_educacao, ctx.as_of),
            _componente("deducoes", "Deduções MDE", fato.deducoes_outras,
                        "subtrai", ctx.source_educacao, ctx.as_of),
            _componente("rpnp_sem_lastro", "RPNP sem disponibilidade de caixa",
                        fato.rpnp_sem_lastro, "subtrai", source_rgf or ctx.source_educacao,
                        rpnp_as_of),
            _componente("aplicado", "MDE válida para o piso", fato.despesa_aplicada,
                        "resultado", ctx.source_educacao, ctx.as_of),
        ],
        detalhes={
            **detalhes,
            "base_nao_e_rcl": True,
            "fonte_primaria": "RREO",
            "fundeb_denominador": (
                "Receitas principais do FUNDEB; não inclui rendimentos nem ressarcimentos"
            ),
        },
        source_ref=ctx.source_educacao,
        as_of=ctx.as_of,
    )
    return EducacaoDetalhe(
        cod_ibge=cod_ibge,
        periodo=periodo,
        esfera=ctx.esfera,
        base_impostos_transferencias=fato.base_impostos_transferencias,
        despesa_bruta=fato.despesa_bruta,
        despesa_impostos=fato.despesa_impostos,
        despesa_fundeb=fato.despesa_fundeb,
        deducoes_outras=fato.deducoes_outras,
        rpnp_sem_lastro=fato.rpnp_sem_lastro,
        despesa_aplicada=fato.despesa_aplicada,
        pct_aplicado=fato.pct_aplicado,
        minimo_pct=fato.minimo_pct,
        valor_minimo=fato.valor_minimo,
        abaixo_do_minimo=fato.abaixo_do_minimo,
        folga=fato.despesa_aplicada - fato.valor_minimo,
        projecao_pct=fato.pct_aplicado,
        fundeb=FundebDetalhe(
            base=fato.fundeb_base_profissionais,
            aplicado_profissionais=fato.fundeb_aplicado_profissionais,
            pct_aplicado=fato.fundeb_pct_profissionais,
            minimo_pct=fato.fundeb_minimo_pct,
            valor_minimo=fato.fundeb_valor_minimo,
            abaixo_do_minimo=fato.fundeb_abaixo_do_minimo,
            source_ref=ctx.source_educacao,
            as_of=ctx.as_of,
        ),
        versao_entrega=fato.versao_rreo,
        source_ref=ctx.source_educacao,
        source_ref_expurgo=source_rgf,
        as_of=ctx.as_of,
        memoria_calculo=memoria,
        serie=_serie(session, cod_ibge, periodo, "educacao", as_of),
    )


def build_saude_arvore(
    session: Session,
    cod_ibge: str,
    periodo: str,
    node: str | None,
    *,
    as_of: datetime | None = None,
) -> MinimoArvoreOut:
    ctx = _contexto(session, cod_ibge, periodo, as_of)
    fato, _, _ = _apurar_saude(session, ctx)
    subfatos = repository.list_saude_subfuncoes(
        session, cod_ibge=cod_ibge, periodo=periodo, versao_rreo=ctx.versao_rreo
    )
    codigos = {"10", *(row.funcao_codigo for row in subfatos)}
    dims = repository.list_funcoes(session, codigos)
    by_codigo = {row.funcao_codigo: row for row in subfatos}
    nodes = [
        HierarchyNode(
            codigo=dim.codigo,
            descricao=dim.descricao,
            parent_codigo=dim.parent_codigo if dim.parent_codigo in codigos else None,
            nivel=dim.nivel,
            path=dim.path,
            measures=(
                {
                    "empenhado": by_codigo[dim.codigo].empenhado,
                    "liquidado": by_codigo[dim.codigo].liquidado,
                    "pago": by_codigo[dim.codigo].pago,
                    "valor_computado": by_codigo[dim.codigo].valor_computado,
                }
                if dim.codigo in by_codigo
                else {
                    "despesa_bruta": fato.despesa_bruta,
                    "despesa_aplicada": fato.despesa_aplicada,
                }
            ),
        )
        for dim in dims
    ]
    try:
        envelope = build_drill_envelope(
            nodes, node, period=periodo, source_ref=ctx.source_saude
        )
    except KeyError as exc:
        raise AppError(status=404, title="Subfunção inexistente", detail=str(exc)) from exc
    return MinimoArvoreOut(**envelope.model_dump(), as_of=ctx.as_of)


def build_educacao_arvore(
    session: Session,
    cod_ibge: str,
    periodo: str,
    node: str | None,
    *,
    as_of: datetime | None = None,
) -> MinimoArvoreOut:
    ctx = _contexto(session, cod_ibge, periodo, as_of)
    fato, _, _ = _apurar_educacao(session, ctx)
    nodes = [
        HierarchyNode(
            codigo="MDE", descricao="Manutenção e Desenvolvimento do Ensino",
            nivel=1, path="MDE",
            measures={
                "despesa_bruta": fato.despesa_bruta,
                "deducoes_e_expurgo": fato.deducoes_outras + fato.rpnp_sem_lastro,
                "despesa_aplicada": fato.despesa_aplicada,
            },
        ),
        HierarchyNode(
            codigo="MDE.IMPOSTOS", descricao="Impostos e transferências",
            parent_codigo="MDE", nivel=2, path="MDE.IMPOSTOS",
            measures={"valor_computado_bruto": fato.despesa_impostos},
        ),
        HierarchyNode(
            codigo="MDE.FUNDEB", descricao="Transferência ao FUNDEB",
            parent_codigo="MDE", nivel=2, path="MDE.FUNDEB",
            measures={
                "valor_computado_bruto": fato.despesa_fundeb,
                "base_profissionais": fato.fundeb_base_profissionais,
                "aplicado_profissionais": fato.fundeb_aplicado_profissionais,
                "pct_profissionais": fato.fundeb_pct_profissionais,
            },
        ),
    ]
    try:
        envelope = build_drill_envelope(
            nodes, node, period=periodo, source_ref=ctx.source_educacao
        )
    except KeyError as exc:
        raise AppError(status=404, title="Fonte MDE inexistente", detail=str(exc)) from exc
    return MinimoArvoreOut(**envelope.model_dump(), as_of=ctx.as_of)


def _projecao(
    indicador: Literal["saude", "educacao"],
    periodo: str,
    pct: Decimal,
    minimo: Decimal,
    aplicado: Decimal,
    valor_minimo: Decimal,
    source: SourceRef,
    as_of: datetime,
) -> ProjecaoIndicador:
    _, bimestre = _periodo(periodo)
    fator = Decimal(6) / Decimal(bimestre)
    return ProjecaoIndicador(
        indicador=indicador,
        periodo=periodo,
        pct_atual=pct,
        pct_projetado=pct,
        minimo_pct=minimo,
        valor_aplicado_projetado=aplicado * fator,
        valor_minimo_projetado=valor_minimo * fator,
        abaixo_do_minimo_projetado=pct < minimo,
        metodo=(
            "Carregamento da razão acumulada: numerador e base são anualizados pelo mesmo "
            "fator 6/bimestre; não mistura RCL nem dados SIOPS/SIOPE."
        ),
        source_ref=source,
        as_of=as_of,
    )


def projetar_minimos(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> tuple[ProjecaoIndicador, ProjecaoIndicador]:
    """Só as duas projeções (saúde, educação) do período — sem a série plurianual.

    Quem precisa apenas do veredito "a trajetória fura o piso?" — o motor de alertas —
    não deve pagar a série de 5 exercícios: ela custava mais do que a apuração dos dois
    domínios somados e era descartada em seguida.
    """
    ctx = _contexto(session, cod_ibge, periodo, as_of)
    saude, _, _ = _apurar_saude(session, ctx)
    educacao, _, _ = _apurar_educacao(session, ctx)
    return (
        _projecao(
            "saude", periodo, saude.pct_aplicado, saude.minimo_pct,
            saude.despesa_aplicada, saude.valor_minimo, ctx.source_saude, ctx.as_of,
        ),
        _projecao(
            "educacao", periodo, educacao.pct_aplicado, educacao.minimo_pct,
            educacao.despesa_aplicada, educacao.valor_minimo,
            ctx.source_educacao, ctx.as_of,
        ),
    )


def build_projecao(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> MinimosProjecao:
    ctx = _contexto(session, cod_ibge, periodo, as_of)
    saude, _, _ = _apurar_saude(session, ctx)
    educacao, _, _ = _apurar_educacao(session, ctx)
    serie_saude = {
        item.periodo: item for item in _serie(session, cod_ibge, periodo, "saude", as_of)
    }
    serie_educacao = {
        item.periodo: item for item in _serie(session, cod_ibge, periodo, "educacao", as_of)
    }
    serie: list[ProjecaoSerieItem] = []
    for codigo in sorted(set(serie_saude) | set(serie_educacao)):
        s = serie_saude.get(codigo)
        e = serie_educacao.get(codigo)
        serie.append(
            ProjecaoSerieItem(
                periodo=codigo,
                saude_pct=s.pct_aplicado if s else None,
                educacao_pct=e.pct_aplicado if e else None,
                source_ref_saude=s.source_ref if s else None,
                source_ref_educacao=e.source_ref if e else None,
                as_of=(s.as_of if s else e.as_of),  # type: ignore[union-attr]
            )
        )
    return MinimosProjecao(
        cod_ibge=cod_ibge,
        periodo=periodo,
        saude=_projecao(
            "saude", periodo, saude.pct_aplicado, saude.minimo_pct,
            saude.despesa_aplicada, saude.valor_minimo, ctx.source_saude, ctx.as_of,
        ),
        educacao=_projecao(
            "educacao", periodo, educacao.pct_aplicado, educacao.minimo_pct,
            educacao.despesa_aplicada, educacao.valor_minimo,
            ctx.source_educacao, ctx.as_of,
        ),
        serie=serie,
        source_ref=ctx.source_saude,
        as_of=ctx.as_of,
    )


def _latest_delivery(
    session: Session,
    relatorio: str,
    periodo_max: str,
    as_of: datetime | None,
) -> DimEntrega | None:
    stmt = select(DimEntrega).where(
        DimEntrega.cod_ibge == "BR",
        DimEntrega.relatorio == relatorio,
        DimEntrega.periodo <= periodo_max,
    )
    if as_of is None:
        stmt = stmt.where(DimEntrega.vigente.is_(True))
    else:
        stmt = stmt.where(DimEntrega.homologada_em <= as_of)
    return session.scalar(
        stmt.order_by(DimEntrega.periodo.desc(), DimEntrega.homologada_em.desc()).limit(1)
    )


def _defasagem(periodo_solicitado: str, periodo_fonte: str | None) -> int | None:
    if periodo_fonte is None:
        return None
    try:
        ano_s, bim_s = _periodo(periodo_solicitado)
        if "-M" in periodo_fonte:
            ano_f, mes_f = periodo_fonte.split("-M")
            bim_f = (int(mes_f) + 1) // 2
            ano_f_int = int(ano_f)
        else:
            ano_f_int, bim_f = _periodo(periodo_fonte)
        return max(0, (ano_s - ano_f_int) * 6 + bim_s - bim_f)
    except (ValueError, AppError):
        return None


def build_siops(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> EnriquecimentoDetalhe:
    ano, bimestre = _periodo(periodo)
    entrega = _latest_delivery(session, "SIOPS", periodo, as_of)
    instante = as_of or (entrega.homologada_em if entrega else datetime.now(UTC))
    if entrega is None:
        return EnriquecimentoDetalhe(
            cod_ibge=cod_ibge, periodo_solicitado=periodo, defasado=True,
            selo="indisponivel", as_of=instante,
        )
    rows = repository.read_siops(
        session,
        cod_ibge=cod_ibge,
        ano=ano,
        bimestre_max=bimestre,
        versao_entrega=entrega.versao_entrega,
    )
    if not rows:
        return EnriquecimentoDetalhe(
            cod_ibge=cod_ibge, periodo_solicitado=periodo, defasado=True,
            selo="indisponivel", source_ref=SourceRef(
                relatorio="SIOPS", periodo=entrega.periodo,
                versao_entrega=entrega.versao_entrega,
            ), as_of=instante,
        )
    ultimo = max(row.bimestre or 0 for row in rows)
    periodo_fonte = f"{ano}-B{ultimo}"
    source = SourceRef(
        relatorio="SIOPS", periodo=periodo_fonte,
        versao_entrega=entrega.versao_entrega,
    )
    itens = [
        EnriquecimentoItem(
            codigo=row.indicador_codigo,
            descricao=row.indicador_descricao or row.indicador_codigo.replace("_", " "),
            valor=row.valor,
            unidade=row.unidade,
            periodo=periodo_fonte,
            source_ref=source,
            as_of=instante,
        )
        for row in rows if (row.bimestre or 0) == ultimo
    ]
    atraso = _defasagem(periodo, periodo_fonte)
    return EnriquecimentoDetalhe(
        cod_ibge=cod_ibge,
        periodo_solicitado=periodo,
        periodo_fonte=periodo_fonte,
        ultima_atualizacao=entrega.homologada_em,
        defasado=(atraso or 0) > 0,
        defasagem_bimestres=atraso,
        selo="defasado" if (atraso or 0) > 0 else "atualizado",
        itens=itens,
        source_ref=source,
        as_of=instante,
    )


def build_siope(
    session: Session, cod_ibge: str, periodo: str, *, as_of: datetime | None = None
) -> EnriquecimentoDetalhe:
    ano, bimestre = _periodo(periodo)
    entrega = _latest_delivery(session, "SIOPE", periodo, as_of)
    instante = as_of or (entrega.homologada_em if entrega else datetime.now(UTC))
    itens: list[EnriquecimentoItem] = []
    source: SourceRef | None = None
    periodo_fonte: str | None = None
    ultima: datetime | None = None
    if entrega is not None:
        result = repository.read_siope(
            session, cod_ibge=cod_ibge, ano=ano, bimestre_max=bimestre,
            versao_entrega=entrega.versao_entrega,
        )
        if result:
            ultimo = max(int(row.bimestre or 0) for row in result)
            periodo_fonte = f"{ano}-B{ultimo}"
            source = SourceRef(
                relatorio="SIOPE", periodo=periodo_fonte,
                versao_entrega=entrega.versao_entrega,
            )
            itens.extend(
                EnriquecimentoItem(
                    codigo=str(row.indicador_codigo),
                    descricao=(
                        row.indicador_descricao
                        or str(row.indicador_codigo).replace("_", " ")
                    ),
                    valor=row.valor, unidade=row.unidade, periodo=periodo_fonte,
                    source_ref=source, as_of=instante,
                )
                for row in result if int(row.bimestre or 0) == ultimo
            )
            ultima = entrega.homologada_em

    # FNDE é outro enriquecimento: soma os repasses até o mês de fechamento do bimestre.
    periodo_fundeb_max = f"{ano}-M{bimestre * 2:02d}"
    fundeb_entrega = _latest_delivery(session, "FUNDEB", periodo_fundeb_max, as_of)
    if fundeb_entrega is not None:
        fundeb_rows = repository.read_fundeb(
            session,
            cod_ibge=cod_ibge,
            ano=ano,
            mes_max=bimestre * 2,
            versao_entrega=fundeb_entrega.versao_entrega,
        )
        if fundeb_rows:
            fundeb_source = SourceRef(
                relatorio="FUNDEB-FNDE", periodo=fundeb_entrega.periodo,
                versao_entrega=fundeb_entrega.versao_entrega,
            )
            itens.extend(
                [
                    EnriquecimentoItem(
                        codigo="FNDE_FUNDEB_REPASSE_ACUMULADO",
                        descricao="Repasses FUNDEB acumulados no FNDE",
                        valor=sum((row.valor_repassado or ZERO for row in fundeb_rows), ZERO),
                        unidade="R$", periodo=fundeb_entrega.periodo,
                        source_ref=fundeb_source, as_of=instante,
                    ),
                    EnriquecimentoItem(
                        codigo="FNDE_FUNDEB_COMPLEMENTACAO_UNIAO_ACUMULADA",
                        descricao="Complementação da União acumulada",
                        valor=sum((row.complementacao_uniao or ZERO for row in fundeb_rows), ZERO),
                        unidade="R$", periodo=fundeb_entrega.periodo,
                        source_ref=fundeb_source, as_of=instante,
                    ),
                ]
            )
            if source is None:
                source = fundeb_source
                periodo_fonte = fundeb_entrega.periodo
                ultima = fundeb_entrega.homologada_em

    atraso = _defasagem(periodo, periodo_fonte)
    indisponivel = not itens
    return EnriquecimentoDetalhe(
        cod_ibge=cod_ibge,
        periodo_solicitado=periodo,
        periodo_fonte=periodo_fonte,
        ultima_atualizacao=ultima,
        defasado=indisponivel or (atraso or 0) > 0,
        defasagem_bimestres=atraso,
        selo=(
            "indisponivel" if indisponivel
            else "defasado" if (atraso or 0) > 0
            else "atualizado"
        ),
        itens=itens,
        source_ref=source,
        as_of=instante,
    )
