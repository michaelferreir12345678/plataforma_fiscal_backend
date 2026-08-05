"""Provedor de séries do forecast: histórico (gold) + exógenas (silver) alinhados.

Fonte única das séries que a Sprint 14 projeta. Traduz cada indicador para a sua
tabela ``gold`` e alinha as **variáveis exógenas** reais (FPM de ``silver.tesouro_fpm``;
IPCA/Selic de ``silver.bcb_indice``) aos períodos fiscais da série — é aqui que a
regressão passa a **consumir de fato** essas fontes (critério de aceite).

Toda medida carrega rastreabilidade: ``versao_entrega``, ``as_of`` e ``source_ref``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app.modules.debt.models import FatoDivida
from app.modules.forecast.algorithms import MatrizExogenas
from app.modules.forecast.periodos import Periodo, ordenar_codigos, parse_periodo
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion import repository as ingestion_repo
from app.modules.ingestion.models import BcbIndice, TesouroFpm

# Códigos SGS/BCB usados como exógenas.
SERIE_IPCA = 433
SERIE_SELIC = 4390

#: Relatório do FPM em gold.dim_entrega — ingestão nacional batch (§6.7), não por ente.
_RELATORIO_FPM = "FPM"

UNIDADE_BRL = "BRL"
UNIDADE_PCT_RCL = "PCT_RCL"


@dataclass(frozen=True)
class LimiteRef:
    """Aponta o limite legal aplicável (para o cruzamento), por indicador/poder."""

    indicador_limite: str
    poder: str = ""


@dataclass(frozen=True)
class IndicadorConfig:
    chave: str
    descricao: str
    unidade: str
    relatorio: str
    anexo: str | None
    limite: LimiteRef | None


# Registro dos indicadores projetáveis (RCL, receita, pessoal, dívida).
INDICADORES: dict[str, IndicadorConfig] = {
    "rcl": IndicadorConfig(
        "rcl", "Receita Corrente Líquida (12m)", UNIDADE_BRL, "RREO", "Anexo 03", None
    ),
    "receita": IndicadorConfig(
        "receita", "Receita Corrente (RREO Anexo 03)", UNIDADE_BRL, "RREO", "Anexo 03", None
    ),
    "pessoal": IndicadorConfig(
        "pessoal",
        "Despesa com Pessoal — Executivo (% RCL)",
        UNIDADE_PCT_RCL,
        "RGF",
        "Anexo 01",
        LimiteRef("pessoal_executivo", "Executivo"),
    ),
    "divida": IndicadorConfig(
        "divida",
        "Dívida Consolidada Líquida (% RCL)",
        UNIDADE_PCT_RCL,
        "RGF",
        "Anexo 02",
        LimiteRef("divida_consolidada_liquida", ""),
    ),
}


@dataclass(frozen=True)
class PontoSerie:
    periodo: str
    valor: float
    versao_entrega: str
    as_of: datetime | None
    source_ref: dict


@dataclass
class SerieHistorica:
    indicador: str
    descricao: str
    unidade: str
    config: IndicadorConfig
    pontos: list[PontoSerie] = field(default_factory=list)

    @property
    def valores(self) -> list[float]:
        return [p.valor for p in self.pontos]

    @property
    def periodos(self) -> list[str]:
        return [p.periodo for p in self.pontos]


def indicador_config(indicador: str) -> IndicadorConfig | None:
    return INDICADORES.get(indicador)


def _as_of(
    session: Session, *, cod_ibge: str, relatorio: str, periodo: str, versao: str
) -> datetime | None:
    return ingestion_repo.entrega_homologada_em(
        session, cod_ibge=cod_ibge, relatorio=relatorio, periodo=periodo, versao_entrega=versao
    )


def _source_ref(cfg: IndicadorConfig, periodo: str, versao: str) -> dict:
    return {
        "relatorio": cfg.relatorio,
        "anexo": cfg.anexo,
        "periodo": periodo,
        "versao_entrega": versao,
    }


def _ordenar(pontos: list[PontoSerie]) -> list[PontoSerie]:
    ordem = {c: i for i, c in enumerate(ordenar_codigos([p.periodo for p in pontos]))}
    return sorted(pontos, key=lambda p: ordem[p.periodo])


def carregar_serie(session: Session, indicador: str, cod_ibge: str) -> SerieHistorica:
    """Monta a série histórica de ``indicador`` para o ente, ordenada no tempo."""
    cfg = INDICADORES[indicador]
    if indicador in ("rcl", "receita"):
        campo = FatoRcl.rcl_12m if indicador == "rcl" else FatoRcl.receita_corrente
        rows = session.execute(
            select(FatoRcl.periodo_ref, campo, FatoRcl.versao_entrega).where(
                FatoRcl.cod_ibge == cod_ibge, campo.is_not(None)
            )
        ).all()
        pontos = [
            PontoSerie(
                periodo=periodo,
                valor=float(valor),
                versao_entrega=versao,
                as_of=_as_of(
                    session, cod_ibge=cod_ibge, relatorio="RREO", periodo=periodo, versao=versao
                ),
                source_ref=_source_ref(cfg, periodo, versao),
            )
            for periodo, valor, versao in rows
        ]
    elif indicador == "pessoal":
        rows = session.execute(
            _dedup_por_periodo(
                select(
                    MartIndicador.periodo,
                    MartIndicador.valor_pct_rcl,
                    MartIndicador.versao_entrega,
                ).where(
                    MartIndicador.cod_ibge == cod_ibge,
                    MartIndicador.indicador == "pessoal_executivo",
                    MartIndicador.valor_pct_rcl.is_not(None),
                ),
                MartIndicador.periodo,
            )
        ).all()
        pontos = [
            PontoSerie(
                periodo=periodo,
                valor=float(valor),
                versao_entrega=versao,
                as_of=_as_of(
                    session, cod_ibge=cod_ibge, relatorio="RGF", periodo=periodo, versao=versao
                ),
                source_ref=_source_ref(cfg, periodo, versao),
            )
            for periodo, valor, versao in rows
        ]
    elif indicador == "divida":
        rows = session.execute(
            select(FatoDivida.periodo, FatoDivida.pct_rcl, FatoDivida.versao_entrega).where(
                FatoDivida.cod_ibge == cod_ibge, FatoDivida.pct_rcl.is_not(None)
            )
        ).all()
        pontos = [
            PontoSerie(
                periodo=periodo,
                valor=float(valor),
                versao_entrega=versao,
                as_of=_as_of(
                    session, cod_ibge=cod_ibge, relatorio="RGF", periodo=periodo, versao=versao
                ),
                source_ref=_source_ref(cfg, periodo, versao),
            )
            for periodo, valor, versao in rows
        ]
    else:  # pragma: no cover - guardado por validação na borda
        raise KeyError(indicador)

    return SerieHistorica(
        indicador=indicador,
        descricao=cfg.descricao,
        unidade=cfg.unidade,
        config=cfg,
        pontos=_ordenar(pontos),
    )


def _dedup_por_periodo(stmt: Select[Any], periodo_col: Any) -> Select[Any]:
    """Uma linha por período (mart_indicador guarda projeção vigente + histórico composto)."""
    return stmt.order_by(periodo_col).distinct(periodo_col)


# --------------------------------------------------------------------------- #
# Exógenas (silver) alinhadas aos períodos fiscais
# --------------------------------------------------------------------------- #
def _fpm_periodo(session: Session, cod_ibge: str, p: Periodo) -> float | None:
    """Soma do FPM líquido nos meses do período — só a versão **vigente** por mês (A14).

    ``silver.tesouro_fpm`` guarda ``versao_entrega`` sem coluna de vigência; somar tudo
    dobra o valor sempre que a fonte foi reingerida (185/185 entes com versão duplicada em
    2025). A vigência vem de ``gold.dim_entrega`` (ingestão nacional, ``cod_ibge='BR'``).
    """
    vigentes = ingestion_repo.resolve_versoes_por_mes(
        session, relatorio=_RELATORIO_FPM, ano=p.ano, meses=p.meses()
    )
    if not vigentes:
        return None
    condicoes = [
        and_(TesouroFpm.mes == mes, TesouroFpm.versao_entrega == versao)
        for mes, versao in vigentes.items()
    ]
    total = session.scalar(
        select(func.sum(TesouroFpm.valor_liquido)).where(
            TesouroFpm.cod_ibge == cod_ibge,
            TesouroFpm.ano == p.ano,
            or_(*condicoes),
        )
    )
    return float(total) if total is not None else None


def _bcb_media(session: Session, codigo_serie: int, p: Periodo) -> float | None:
    valores = session.scalars(
        select(BcbIndice.valor).where(
            BcbIndice.codigo_serie == codigo_serie,
            func.extract("year", BcbIndice.data_ref) == p.ano,
            func.extract("month", BcbIndice.data_ref).in_(p.meses()),
            BcbIndice.valor.is_not(None),
        )
    ).all()
    vals = [float(v) for v in valores if v is not None]
    return sum(vals) / len(vals) if vals else None


# Ordem canônica das exógenas candidatas (nome → leitor).
def _candidatas(
    session: Session, cod_ibge: str
) -> tuple[tuple[str, Callable[[Periodo], float | None]], ...]:
    return (
        ("FPM", lambda p: _fpm_periodo(session, cod_ibge, p)),
        ("IPCA", lambda p: _bcb_media(session, SERIE_IPCA, p)),
        ("SELIC", lambda p: _bcb_media(session, SERIE_SELIC, p)),
    )


@dataclass
class ExogenasAlinhadas:
    matriz: MatrizExogenas
    # Valores brutos por período (para memória/cenário): nome → {periodo: valor}
    historico_por_nome: dict[str, dict[str, float]]
    media_por_nome: dict[str, float]
    fontes: dict[str, str]


_FONTE_EXOG = {
    "FPM": "silver.tesouro_fpm",
    "IPCA": "silver.bcb_indice#433",
    "SELIC": "silver.bcb_indice#4390",
}


def alinhar_exogenas(
    session: Session,
    cod_ibge: str,
    periodos_hist: list[str],
    periodos_futuro: list[str],
    *,
    overrides_futuro: dict[str, list[float]] | None = None,
) -> ExogenasAlinhadas:
    """Alinha as exógenas aos períodos históricos e projeta-as ao horizonte.

    Uma exógena só entra no desenho se **todos** os períodos históricos tiverem valor
    (coluna sem buracos). Para o futuro, extrapola pela média histórica, salvo override
    de cenário (``overrides_futuro``).
    """
    hist_p = [parse_periodo(c) for c in periodos_hist]
    fut_p = [parse_periodo(c) for c in periodos_futuro]
    overrides_futuro = overrides_futuro or {}

    nomes: list[str] = []
    colunas_hist: dict[str, list[float]] = {}
    media_por_nome: dict[str, float] = {}
    historico_por_nome: dict[str, dict[str, float]] = {}

    for nome, leitor in _candidatas(session, cod_ibge):
        valores = [leitor(p) for p in hist_p]
        if any(v is None for v in valores):  # coluna incompleta → descartada
            continue
        col = [float(v) for v in valores]  # type: ignore[arg-type]
        nomes.append(nome)
        colunas_hist[nome] = col
        media_por_nome[nome] = sum(col) / len(col)
        historico_por_nome[nome] = dict(zip(periodos_hist, col, strict=True))

    # Monta as linhas (uma por período) na ordem canônica de ``nomes``.
    historico = [[colunas_hist[nome][t] for nome in nomes] for t in range(len(periodos_hist))]

    futuro: list[list[float]] = []
    for h in range(len(periodos_futuro)):
        linha: list[float] = []
        for nome in nomes:
            if nome in overrides_futuro and h < len(overrides_futuro[nome]):
                linha.append(overrides_futuro[nome][h])
            else:
                valor = _fpm_periodo(session, cod_ibge, fut_p[h]) if nome == "FPM" else None
                if nome == "IPCA":
                    valor = _bcb_media(session, SERIE_IPCA, fut_p[h])
                elif nome == "SELIC":
                    valor = _bcb_media(session, SERIE_SELIC, fut_p[h])
                linha.append(valor if valor is not None else media_por_nome[nome])
        futuro.append(linha)

    return ExogenasAlinhadas(
        matriz=MatrizExogenas(nomes=nomes, historico=historico, futuro=futuro),
        historico_por_nome=historico_por_nome,
        media_por_nome=media_por_nome,
        fontes={nome: _FONTE_EXOG[nome] for nome in nomes},
    )
