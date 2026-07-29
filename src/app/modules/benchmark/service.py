"""Regras de benchmarking: coorte explícita, percentis, quantis e auditabilidade."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.benchmark import repository
from app.modules.benchmark.models import DimCoorte
from app.modules.benchmark.schemas import (
    BenchmarkEvolucaoResponse,
    BenchmarkRankingResponse,
    BenchmarkResponse,
    BenchmarkValue,
    CoberturaBenchmark,
    CoorteOut,
    DistribuicaoBenchmark,
    EvolucaoPonto,
    IndicadorDisponivel,
    OrdemRanking,
    OrdenacaoRanking,
    Sentido,
)
from app.modules.catalog import repository as catalog_repository
from app.modules.catalog.models import DimEnte
from app.modules.indicators import rotulos
from app.modules.indicators.models import MartIndicador
from app.modules.ingestion.models import DimEntrega
from app.shared.source_ref import SourceRef, composite_version_key

_CRITERIO_ORDEM = {"porte": 0, "regiao": 1, "pib": 2}
_RANKING_CACHE_MIN_ROWS = 100
_RANKING_CACHE_MAX_ENTRIES = 32
_RANKING_CACHE_TTL_SECONDS = 30.0
_LABELS = {
    "pessoal_executivo": "Pessoal do Executivo",
    "divida_consolidada_liquida": "Dívida consolidada líquida",
    "operacoes_credito": "Operações de crédito",
    "garantias": "Garantias",
    "aro": "Antecipação de receita orçamentária",
    "saude_minimo": "Aplicação em saúde",
    "educacao_mde": "Aplicação em educação (MDE)",
    "fundeb_profissionais": "FUNDEB — profissionais da educação",
    "rcl_per_capita": "RCL por habitante",
    "investimento_rcl": "Investimento sobre a RCL",
    "resultado_primario_rcl": "Resultado primário sobre a RCL",
}


@dataclass(frozen=True)
class _EffectiveMart:
    mart: MartIndicador
    homologada_em: datetime | None


@dataclass(frozen=True)
class _BenchmarkEnte:
    cod_ibge: str
    nome: str | None
    esfera: str | None
    uf: str | None
    regiao: str | None
    populacao: int | None
    pib: Decimal | None
    pop_ano_ref: int | None
    pib_ano_ref: int | None
    pop_source_ref: dict[str, Any] | None
    pib_source_ref: dict[str, Any] | None


@dataclass(frozen=True)
class _Snapshot:
    indicador: str
    indicador_rotulo: str
    unidade: str
    sentido: Sentido
    periodo: str
    as_of: datetime
    coorte: CoorteOut
    coortes_disponiveis: list[CoorteOut]
    indicadores_disponiveis: list[IndicadorDisponivel]
    valores: list[BenchmarkValue]
    distribuicao: DistribuicaoBenchmark
    cobertura: CoberturaBenchmark
    memoria: dict[str, Any]
    source_refs: list[SourceRef]


@dataclass(frozen=True)
class _CachedSnapshot:
    stored_at: float
    snapshot: _Snapshot


_snapshot_cache: OrderedDict[tuple[str, str, str, str, str], _CachedSnapshot] = (
    OrderedDict()
)
_snapshot_cache_lock = threading.Lock()


def _snapshot_cache_get(
    key: tuple[str, str, str, str, str],
) -> _Snapshot | None:
    now = time.monotonic()
    with _snapshot_cache_lock:
        cached = _snapshot_cache.get(key)
        if cached is None:
            return None
        if now - cached.stored_at > _RANKING_CACHE_TTL_SECONDS:
            del _snapshot_cache[key]
            return None
        _snapshot_cache.move_to_end(key)
        return cached.snapshot


def _snapshot_cache_put(
    key: tuple[str, str, str, str, str],
    snapshot: _Snapshot,
) -> None:
    reusable = replace(
        snapshot,
        memoria={**snapshot.memoria, "snapshot_reutilizado": True},
    )
    with _snapshot_cache_lock:
        _snapshot_cache[key] = _CachedSnapshot(
            stored_at=time.monotonic(),
            snapshot=reusable,
        )
        _snapshot_cache.move_to_end(key)
        while len(_snapshot_cache) > _RANKING_CACHE_MAX_ENTRIES:
            _snapshot_cache.popitem(last=False)


def _label(indicador: str) -> str:
    # `capitalize()` engolia as siglas do domínio ("Rcl per capita"); o registro canônico
    # trata sigla e mantém o nome igual em todas as telas.
    return _LABELS.get(indicador) or rotulos.rotulo(indicador)


def _unidade(mart: MartIndicador) -> str:
    """Unidade declarada da métrica — a base vem da linha, não de uma suposição.

    Antes da Sprint 25C todo percentual do mart era da RCL. Com ASPS/MDE/FUNDEB no mart,
    rotular tudo como ``percentual_rcl`` transformaria um número certo em informação
    errada para quem lê o eixo do gráfico.
    """
    if mart.valor_pct_rcl is None:
        # Um valor absoluto dividido pela população não é "R$": é R$ por habitante, e
        # o eixo do gráfico precisa dizer isso.
        return "brl_per_capita" if mart.denominador == "populacao" else "brl"
    return f"percentual_{mart.denominador or 'rcl'}"


def _source_ref(mart: MartIndicador) -> SourceRef:
    raw = mart.source_ref or {}
    if raw.get("relatorio"):
        try:
            return SourceRef.model_validate(raw)
        except ValidationError:
            pass
    return SourceRef(
        relatorio="MART-INDICADOR",
        anexo=mart.indicador,
        periodo=mart.periodo,
        versao_entrega=mart.versao_entrega,
    )


def _coorte_out(coorte: DimCoorte) -> CoorteOut:
    return CoorteOut.model_validate(coorte)


def _benchmark_ente(dim: DimEnte) -> _BenchmarkEnte:
    return _BenchmarkEnte(
        cod_ibge=dim.cod_ibge,
        nome=dim.nome,
        esfera=dim.esfera,
        uf=dim.uf,
        regiao=dim.regiao,
        populacao=dim.populacao,
        pib=Decimal(dim.pib) if dim.pib is not None else None,
        pop_ano_ref=dim.pop_ano_ref,
        pib_ano_ref=dim.pib_ano_ref,
        pop_source_ref=dim.pop_source_ref,
        pib_source_ref=dim.pib_source_ref,
    )


def _entes_no_as_of(
    session: Session, dims: list[DimEnte], as_of: datetime
) -> list[_BenchmarkEnte]:
    codigos = [dim.cod_ibge for dim in dims]
    populacoes = repository.ibge_populacao_as_of(
        session, cods_ibge=codigos, as_of=as_of
    )
    pibs = repository.ibge_pib_as_of(session, cods_ibge=codigos, as_of=as_of)
    siconfi_entes_version = repository.siconfi_entes_version_as_of(
        session, as_of=as_of
    )
    siconfi_populations = repository.siconfi_populacoes(
        session, cods_ibge=codigos
    )
    result: list[_BenchmarkEnte] = []
    for dim in dims:
        pop = populacoes.get(dim.cod_ibge)
        pib = pibs.get(dim.cod_ibge)
        siconfi_pop = siconfi_populations.get(dim.cod_ibge)
        use_siconfi_population = (
            pop is None
            and siconfi_pop is not None
            and siconfi_entes_version is not None
            and siconfi_pop[1] == siconfi_entes_version
        )
        siconfi_population = (
            siconfi_pop[0] if use_siconfi_population and siconfi_pop else None
        )
        siconfi_population_ref = (
            SourceRef(
                relatorio="SICONFI-ENTES",
                anexo="Cadastro de entes",
                versao_entrega=siconfi_pop[1],
            ).model_dump(mode="json")
            if use_siconfi_population and siconfi_pop
            else None
        )
        result.append(
            _BenchmarkEnte(
                cod_ibge=dim.cod_ibge,
                nome=dim.nome,
                esfera=dim.esfera,
                uf=dim.uf,
                regiao=dim.regiao,
                populacao=(
                    pop[0]
                    if pop
                    else siconfi_population
                    if siconfi_population is not None
                    else None
                ),
                pib=pib[0] if pib else None,
                pop_ano_ref=pop[1] if pop else None,
                pib_ano_ref=pib[1] if pib else None,
                pop_source_ref=(
                    SourceRef(
                        relatorio="IBGE-POP",
                        anexo="Agregado 6579 - variavel 9324",
                        periodo=str(pop[1]),
                        versao_entrega=pop[2],
                    ).model_dump(mode="json")
                    if pop
                    else siconfi_population_ref
                    if siconfi_population_ref is not None
                    else None
                ),
                pib_source_ref=(
                    SourceRef(
                        relatorio="IBGE-PIB",
                        anexo="Agregado 5938 - variavel 37 (mil reais)",
                        periodo=str(pib[1]),
                        versao_entrega=pib[2],
                    ).model_dump(mode="json")
                    if pib
                    else None
                ),
            )
        )
    return result


def _valor_criterio(ente: _BenchmarkEnte, criterio: str) -> Decimal | str | None:
    if criterio == "porte":
        return Decimal(ente.populacao) if ente.populacao is not None else None
    if criterio == "pib":
        return Decimal(ente.pib) if ente.pib is not None else None
    return ente.regiao.upper() if ente.regiao else None


def _pertence(ente: _BenchmarkEnte, coorte: DimCoorte) -> bool:
    value = _valor_criterio(ente, coorte.criterio)
    if value is None:
        return False
    if coorte.criterio == "regiao":
        return str(value).upper() == coorte.faixa.upper()
    assert isinstance(value, Decimal)
    if coorte.limite_inferior is not None and value < coorte.limite_inferior:
        return False
    if coorte.limite_superior is None:
        return True
    if coorte.inclusivo_superior:
        return value <= coorte.limite_superior
    return value < coorte.limite_superior


def _coortes_aplicaveis(
    session: Session, ente: _BenchmarkEnte, *, as_of: datetime | None
) -> list[DimCoorte]:
    result = [
        c
        for c in repository.list_coortes(session, as_of=as_of)
        if _pertence(ente, c)
    ]
    return sorted(result, key=lambda c: (_CRITERIO_ORDEM[c.criterio], c.ordem, c.codigo))


def _resolver_coorte(
    session: Session,
    ente: _BenchmarkEnte,
    coorte_raw: str | None,
    aplicaveis: list[DimCoorte],
    as_of: datetime | None,
) -> DimCoorte:
    if not aplicaveis:
        raise AppError(
            status=422,
            title="Ente sem coorte",
            detail=(
                f"O ente {ente.cod_ibge} não tem população, região ou PIB suficientes para "
                "definir uma coorte. Execute a ingestão SICONFI/IBGE e conforme dim_ente."
            ),
            type_="urn:plataforma-fiscal:error:coorte-indisponivel",
        )
    if not coorte_raw:
        return aplicaveis[0]

    # Bootstrap do frontend: ``porte``/``regiao``/``pib`` significa a faixa daquele
    # critério que se aplica ao ente. Depois disso o cliente usa o código explícito.
    if coorte_raw in _CRITERIO_ORDEM:
        selected = next((c for c in aplicaveis if c.criterio == coorte_raw), None)
        if selected is None:
            raise AppError(
                status=422,
                title="Critério indisponível para o ente",
                detail=f"O ente {ente.cod_ibge} não possui dado para a coorte '{coorte_raw}'.",
            )
        return selected

    selected = repository.get_coorte(session, coorte_raw, as_of=as_of)
    if selected is None or not selected.ativo:
        raise AppError(
            status=404,
            title="Coorte não encontrada",
            detail=f"Coorte '{coorte_raw}' inexistente ou inativa.",
        )
    if not any(c.id == selected.id for c in aplicaveis):
        raise AppError(
            status=422,
            title="Ente fora da coorte",
            detail=(
                f"O ente {ente.cod_ibge} não pertence à coorte explícita "
                f"'{selected.codigo}'."
            ),
            type_="urn:plataforma-fiscal:error:ente-fora-coorte",
        )
    return selected


def _resolver_periodo_indicador(
    session: Session,
    *,
    cod_ibge: str,
    indicador: str | None,
    periodo: str | None,
    as_of: datetime | None,
) -> tuple[str, str]:
    if as_of is not None:
        candidates = repository.list_periodos_indicadores_ente(
            session, cod_ibge=cod_ibge
        )
        for candidate_period, candidate_indicator in candidates:
            if periodo is not None and candidate_period != periodo:
                continue
            if indicador is not None and candidate_indicator != indicador:
                continue
            if _anchor_indicator_is_reproducible(
                session,
                cod_ibge=cod_ibge,
                indicador=candidate_indicator,
                periodo=candidate_period,
                as_of=as_of,
            ):
                return candidate_period, candidate_indicator
        requested = "/".join(value for value in (indicador, periodo) if value) or "solicitado"
        raise AppError(
            status=404,
            title="Indicador sem versao reproduzivel",
            detail=(
                f"Sem indicador {requested} reproduzivel para {cod_ibge} "
                f"em {as_of.isoformat()}."
            ),
        )

    resolved_period = periodo or repository.latest_periodo(
        session, cod_ibge=cod_ibge, indicador=indicador
    )
    if resolved_period is None:
        raise AppError(
            status=404,
            title="Indicador ausente",
            detail=f"Sem mart_indicador para o ente {cod_ibge}.",
        )
    indicadores = repository.list_indicadores_ente_periodo(
        session, cod_ibge=cod_ibge, periodo=resolved_period
    )
    if indicador is None:
        if not indicadores:
            raise AppError(
                status=404,
                title="Indicador ausente",
                detail=f"Sem mart_indicador para {cod_ibge} em {resolved_period}.",
            )
        return resolved_period, indicadores[0]
    if indicador not in indicadores:
        raise AppError(
            status=404,
            title="Indicador ausente",
            detail=f"Sem '{indicador}' para {cod_ibge} em {resolved_period}.",
        )
    return resolved_period, indicador


def _effective_rows(
    marts: list[MartIndicador],
    entregas: list[DimEntrega],
    *,
    allow_legacy_fallback: bool = True,
) -> dict[str, _EffectiveMart]:
    """Escolhe uma versão por ente; entregas bitemporais vencem o fallback legado."""
    entrega_by_key = {(e.cod_ibge, e.versao_entrega): e for e in entregas}
    grouped: dict[str, list[MartIndicador]] = defaultdict(list)
    for mart in marts:
        grouped[mart.cod_ibge].append(mart)

    result: dict[str, _EffectiveMart] = {}
    for cod_ibge, rows in grouped.items():
        rastreadas = [
            (entrega_by_key[(cod_ibge, row.versao_entrega)], row)
            for row in rows
            if (cod_ibge, row.versao_entrega) in entrega_by_key
        ]
        if rastreadas:
            entrega, row = max(
                rastreadas,
                key=lambda pair: (pair[0].homologada_em, pair[1].versao_entrega, str(pair[1].id)),
            )
            result[cod_ibge] = _EffectiveMart(row, entrega.homologada_em)
            continue
        # Compatibilidade com marts anteriores ou fixtures sem dim_entrega. A falta
        # da entrega fica explicitamente registrada na memória do ponto.
        if not allow_legacy_fallback:
            continue
        row = max(rows, key=lambda item: (item.versao_entrega, str(item.id)))
        result[cod_ibge] = _EffectiveMart(row, None)
    return result


def _latest_delivery_by_ente(entregas: list[DimEntrega]) -> dict[str, DimEntrega]:
    latest: dict[str, DimEntrega] = {}
    for entrega in entregas:
        current = latest.get(entrega.cod_ibge)
        if current is None or entrega.homologada_em > current.homologada_em:
            latest[entrega.cod_ibge] = entrega
    return latest


def _effective_personnel_rows(
    marts: list[MartIndicador],
    *,
    entregas_rreo: list[DimEntrega],
    entregas_rgf: list[DimEntrega],
    periodo_rreo: str,
    periodo_rgf: str,
) -> dict[str, _EffectiveMart]:
    """Seleciona somente o mart cuja chave representa o par RGF/RREO do corte."""
    rreo_by_ente = _latest_delivery_by_ente(entregas_rreo)
    rgf_by_ente = _latest_delivery_by_ente(entregas_rgf)
    mart_by_key = {(row.cod_ibge, row.versao_entrega): row for row in marts}
    result: dict[str, _EffectiveMart] = {}
    for cod_ibge in rreo_by_ente.keys() & rgf_by_ente.keys():
        rreo = rreo_by_ente[cod_ibge]
        rgf = rgf_by_ente[cod_ibge]
        version = composite_version_key(
            (
                SourceRef(
                    relatorio="RGF",
                    anexo="Anexo 01",
                    periodo=periodo_rgf,
                    versao_entrega=rgf.versao_entrega,
                ),
                SourceRef(
                    relatorio="RREO",
                    anexo="Anexo 03",
                    periodo=periodo_rreo,
                    versao_entrega=rreo.versao_entrega,
                ),
            )
        )
        row = mart_by_key.get((cod_ibge, version))
        if row is not None:
            result[cod_ibge] = _EffectiveMart(
                row, max(rgf.homologada_em, rreo.homologada_em)
            )
    return result


def _anchor_indicator_is_reproducible(
    session: Session,
    *,
    cod_ibge: str,
    indicador: str,
    periodo: str,
    as_of: datetime,
) -> bool:
    marts = repository.list_mart_indicador(
        session,
        cods_ibge=[cod_ibge],
        indicador=indicador,
        periodo=periodo,
    )
    entregas_rreo = repository.list_entregas_rreo(
        session, cods_ibge=[cod_ibge], periodo=periodo, as_of=as_of
    )
    if indicador != "pessoal_executivo":
        return cod_ibge in _effective_rows(
            marts, entregas_rreo, allow_legacy_fallback=False
        )
    periodo_rgf = _rgf_periodo(periodo)
    if periodo_rgf is None:
        return False
    entregas_rgf = repository.list_entregas_relatorio(
        session,
        cods_ibge=[cod_ibge],
        relatorio="RGF",
        periodo=periodo_rgf,
        as_of=as_of,
    )
    return cod_ibge in _effective_personnel_rows(
        marts,
        entregas_rreo=entregas_rreo,
        entregas_rgf=entregas_rgf,
        periodo_rreo=periodo,
        periodo_rgf=periodo_rgf,
    )


def _sentido(session: Session, indicador: str, esfera: str) -> Sentido:
    sentido_legal = repository.get_sentido_limite(session, indicador=indicador, esfera=esfera)
    return _sentido_legal(sentido_legal)


def _sentido_legal(sentido_legal: str | None) -> Sentido:
    if sentido_legal == "teto":
        return "menor_melhor"
    if sentido_legal == "piso":
        return "maior_melhor"
    return "neutro"


def _indicadores_disponiveis(
    session: Session,
    *,
    ente: _BenchmarkEnte,
    periodo: str,
    as_of: datetime,
    allow_legacy_fallback: bool,
) -> list[IndicadorDisponivel]:
    marts = repository.list_marts_ente_periodo(
        session,
        cod_ibge=ente.cod_ibge,
        periodo=periodo,
    )
    marts_por_indicador: dict[str, list[MartIndicador]] = defaultdict(list)
    for mart in marts:
        marts_por_indicador[mart.indicador].append(mart)
    codigos = sorted(marts_por_indicador)
    entregas = repository.list_entregas_rreo(
        session, cods_ibge=[ente.cod_ibge], periodo=periodo, as_of=as_of
    )
    periodo_rgf = _rgf_periodo(periodo)
    entregas_rgf = (
        repository.list_entregas_relatorio(
            session,
            cods_ibge=[ente.cod_ibge],
            relatorio="RGF",
            periodo=periodo_rgf,
            as_of=as_of,
        )
        if "pessoal_executivo" in marts_por_indicador and periodo_rgf is not None
        else []
    )
    sentidos = repository.get_sentidos_limites(
        session,
        indicadores=codigos,
        esfera=ente.esfera or "",
    )
    result: list[IndicadorDisponivel] = []
    for codigo in codigos:
        indicador_marts = marts_por_indicador[codigo]
        if codigo == "pessoal_executivo" and periodo_rgf is not None:
            effective = _effective_personnel_rows(
                indicador_marts,
                entregas_rreo=entregas,
                entregas_rgf=entregas_rgf,
                periodo_rreo=periodo,
                periodo_rgf=periodo_rgf,
            ).get(ente.cod_ibge)
        else:
            effective = _effective_rows(
                indicador_marts,
                entregas,
                allow_legacy_fallback=allow_legacy_fallback,
            ).get(ente.cod_ibge)
        if effective is None:
            continue
        unidade = _unidade(effective.mart)
        result.append(
            IndicadorDisponivel(
                codigo=codigo,
                rotulo=_label(codigo),
                unidade=unidade,
                sentido=_sentido_legal(sentidos.get(codigo)),
            )
        )
    return result


def _percent_rank(
    rows: list[tuple[_EffectiveMart, _BenchmarkEnte, Decimal]],
    *,
    ente_destaque: str,
    as_of: datetime,
    coorte: DimCoorte,
    per_capita: bool = False,
) -> list[BenchmarkValue]:
    """SQL PERCENT_RANK: (RANK - 1) / (N - 1), com RANK comum em empates."""
    ordered = sorted(rows, key=lambda item: (item[2], item[1].cod_ibge))
    total = len(ordered)
    denominator = Decimal(total - 1)
    rank = 1
    previous: Decimal | None = None
    values: list[BenchmarkValue] = []
    for index, (effective, dim, valor) in enumerate(ordered):
        if previous is not None and valor != previous:
            rank = index + 1
        percentile = Decimal(0) if total == 1 else (Decimal(rank - 1) / denominator) * 100
        source = _source_ref(effective.mart)
        values.append(
            BenchmarkValue(
                cod_ibge=dim.cod_ibge,
                nome=dim.nome,
                uf=dim.uf,
                valor=valor,
                percentil=percentile,
                posicao=rank,
                faixa=effective.mart.faixa,
                destaque=dim.cod_ibge == ente_destaque,
                as_of=as_of,
                source_ref=source,
                valor_per_capita=(
                    valor / Decimal(dim.populacao)
                    if per_capita and dim.populacao
                    else None
                ),
                populacao=dim.populacao if per_capita else None,
                pop_ano_ref=dim.pop_ano_ref if per_capita else None,
                memoria={
                    "metrica_origem": (
                        "valor_pct_rcl"
                        if effective.mart.valor_pct_rcl is not None
                        else "valor_rs"
                    ),
                    "versao_entrega": effective.mart.versao_entrega,
                    "entrega_homologada_em": (
                        effective.homologada_em.isoformat()
                        if effective.homologada_em is not None
                        else None
                    ),
                    "entrega_bitemporal_localizada": effective.homologada_em is not None,
                    "source_refs_componentes": (effective.mart.source_ref or {}).get(
                        "source_refs_componentes", []
                    ),
                    "chave_versao_composta": (effective.mart.source_ref or {}).get(
                        "chave_versao_composta"
                    ),
                    "tipo_registro_origem": (effective.mart.source_ref or {}).get(
                        "tipo_registro"
                    ),
                    "calculo_percentil": {
                        "formula": "100 * (RANK(valor ASC) - 1) / (N - 1); N=1 => 0",
                        "rank": rank,
                        "N": total,
                        "resultado": str(percentile),
                    },
                    "coorte": {
                        "codigo": coorte.codigo,
                        "criterio": coorte.criterio,
                        "faixa": coorte.faixa,
                        "unidade_criterio": coorte.unidade_criterio,
                        "limite_inferior": (
                            str(coorte.limite_inferior)
                            if coorte.limite_inferior is not None
                            else None
                        ),
                        "limite_superior": (
                            str(coorte.limite_superior)
                            if coorte.limite_superior is not None
                            else None
                        ),
                        "inclusivo_superior": coorte.inclusivo_superior,
                        "source_ref": coorte.source_ref,
                    },
                    "leitura_per_capita": (
                        {
                            "aplicavel": per_capita,
                            "populacao": dim.populacao,
                            "pop_ano_ref": dim.pop_ano_ref,
                            "formula": "valor_rs ÷ população",
                        }
                        if per_capita
                        else {"aplicavel": False, "motivo": "métrica já é uma razão"}
                    ),
                    "atributos_ente_na_coorte": {
                        "populacao": dim.populacao,
                        "pop_ano_ref": dim.pop_ano_ref,
                        "pop_source_ref": dim.pop_source_ref,
                        "regiao": dim.regiao,
                        "pib_mil_brl": str(dim.pib) if dim.pib is not None else None,
                        "pib_ano_ref": dim.pib_ano_ref,
                        "pib_source_ref": dim.pib_source_ref,
                    },
                },
            )
        )
        previous = valor
    return values


def _quantile(values: list[Decimal], probability: Decimal) -> Decimal:
    """Percentile-cont com interpolação linear Type 7: h=(n-1)×p."""
    if len(values) == 1:
        return values[0]
    position = Decimal(len(values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - Decimal(lower)
    return values[lower] + (values[upper] - values[lower]) * fraction


def _distribution(values: list[BenchmarkValue]) -> DistribuicaoBenchmark:
    ordered = sorted(item.valor for item in values)
    return DistribuicaoBenchmark(
        minimo=ordered[0],
        p10=_quantile(ordered, Decimal("0.10")),
        p25=_quantile(ordered, Decimal("0.25")),
        mediana=_quantile(ordered, Decimal("0.50")),
        p75=_quantile(ordered, Decimal("0.75")),
        p90=_quantile(ordered, Decimal("0.90")),
        maximo=ordered[-1],
    )


def _unique_sources(values: list[BenchmarkValue]) -> list[SourceRef]:
    seen: set[str] = set()
    result: list[SourceRef] = []
    for item in values:
        candidates = [item.source_ref]
        component_refs = (item.memoria or {}).get("source_refs_componentes", [])
        cohort_attributes = (item.memoria or {}).get("atributos_ente_na_coorte", {})
        cohort_refs = [
            cohort_attributes.get("pop_source_ref"),
            cohort_attributes.get("pib_source_ref"),
        ]
        for raw in [*component_refs, *cohort_refs]:
            if raw is None:
                continue
            try:
                candidates.append(SourceRef.model_validate(raw))
            except ValidationError:
                continue
        for source in candidates:
            key = json.dumps(source.model_dump(mode="json"), sort_keys=True)
            if key not in seen:
                seen.add(key)
                result.append(source)
    return result


def _rgf_periodo(periodo_rreo: str) -> str | None:
    """Converte o RREO bimestral usado na RCL ao RGF quadrimestral do numerador."""
    if "-Q" in periodo_rreo:
        return periodo_rreo
    try:
        ano, bimestre = periodo_rreo.split("-B", 1)
        numero = int(bimestre)
    except (ValueError, TypeError):
        return None
    return f"{ano}-Q{(numero + 1) // 2}"


def _add_personnel_lineage(
    *,
    indicador: str,
    periodo: str,
    values: list[BenchmarkValue],
) -> list[BenchmarkValue]:
    """Explicita os componentes persistidos na propria linha historica selecionada."""
    if indicador != "pessoal_executivo":
        return values
    periodo_rgf = _rgf_periodo(periodo)
    if periodo_rgf is None:
        return values

    traced: list[BenchmarkValue] = []
    for item in values:
        component_sources = (item.memoria or {}).get("source_refs_componentes", [])
        rgf = next(
            (source for source in component_sources if source.get("relatorio") == "RGF"),
            None,
        )
        rreo = next(
            (source for source in component_sources if source.get("relatorio") == "RREO"),
            None,
        )
        memory = {
            **(item.memoria or {}),
            "formula_indicador": "despesa_liquida_pessoal_RGF / RCL_12m_RREO * 100",
            "numerador": {
                "relatorio": "RGF",
                "anexo": "Anexo 01",
                "periodo": periodo_rgf,
                "versao_entrega": rgf.get("versao_entrega") if rgf else None,
            },
            "denominador": {
                "relatorio": "RREO",
                "anexo": "Anexo 03",
                "periodo": periodo,
                "versao_entrega": rreo.get("versao_entrega") if rreo else None,
            },
        }
        traced.append(item.model_copy(update={"memoria": memory}))
    return traced


def _snapshot_hash(
    *,
    coorte: DimCoorte,
    indicador: str,
    periodo: str,
    values: list[BenchmarkValue],
) -> str:
    payload = {
        "coorte": {
            "codigo": coorte.codigo,
            "criterio": coorte.criterio,
            "faixa": coorte.faixa,
            "rotulo": coorte.rotulo,
            "unidade_criterio": coorte.unidade_criterio,
            "limite_inferior": coorte.limite_inferior,
            "limite_superior": coorte.limite_superior,
            "inclusivo_superior": coorte.inclusivo_superior,
            "ordem": coorte.ordem,
            "ativo": coorte.ativo,
            "source_ref": coorte.source_ref,
        },
        "indicador": indicador,
        "periodo": periodo,
        "pontos": [
            {
                "ente": item.cod_ibge,
                "valor": str(item.valor),
                "percentil": str(item.percentil),
                "posicao": item.posicao,
                "faixa": item.faixa,
                "source": item.source_ref.model_dump(mode="json"),
                "memoria": item.memoria,
            }
            for item in sorted(values, key=lambda point: point.cod_ibge)
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _materialize(
    session: Session,
    *,
    coorte: DimCoorte,
    indicador: str,
    periodo: str,
    unidade: str,
    sentido: Sentido,
    as_of: datetime,
    snapshot_hash: str,
    values: list[BenchmarkValue],
) -> None:
    for item in values:
        repository.upsert_mart_benchmark(
            session,
            {
                "snapshot_hash": snapshot_hash,
                "coorte_id": coorte.id,
                "indicador": indicador,
                "periodo": periodo,
                "cod_ibge": item.cod_ibge,
                "valor": item.valor,
                "percentil": item.percentil,
                "posicao": item.posicao,
                "faixa": item.faixa,
                "unidade": unidade,
                "sentido": sentido,
                "versao_entrega": item.source_ref.versao_entrega or "sem-versao",
                "as_of": as_of,
                "source_ref": item.source_ref.model_dump(mode="json"),
                "memoria": item.memoria,
            },
        )


def _build_snapshot(
    session: Session,
    *,
    cod_ibge: str,
    indicador: str | None,
    coorte_raw: str | None,
    periodo: str | None,
    as_of: datetime | None,
) -> _Snapshot:
    # ``gold.dim_ente`` is conformed by ingestion/materialization. A page GET must
    # not re-read silver and issue an UPSERT before every benchmark calculation.
    dim_ente = catalog_repository.get_dim_ente(session, cod_ibge)
    if dim_ente is None or dim_ente.esfera is None:
        raise AppError(
            status=422,
            title="Ente não conformado",
            detail=f"dim_ente sem esfera para {cod_ibge}; ingira siconfi_entes.",
        )

    snapshot_as_of = as_of or datetime.now(UTC)
    ente = (
        _entes_no_as_of(session, [dim_ente], snapshot_as_of)[0]
        if as_of is not None
        else _benchmark_ente(dim_ente)
    )
    assert ente.esfera is not None
    resolved_period, resolved_indicator = _resolver_periodo_indicador(
        session,
        cod_ibge=cod_ibge,
        indicador=indicador,
        periodo=periodo,
        as_of=as_of,
    )
    # Mesmo uma consulta "atual" usa a versão bitemporal vigente no instante
    # efetivo. Assim, repetir a resposta com o `as_of` devolvido seleciona a
    # mesma definição de coorte e produz o mesmo snapshot.
    aplicaveis = _coortes_aplicaveis(session, ente, as_of=snapshot_as_of)
    selected = _resolver_coorte(
        session, ente, coorte_raw, aplicaveis, as_of=snapshot_as_of
    )
    if as_of is not None:
        sphere_dims = repository.list_entes_da_esfera(session, esfera=ente.esfera)
        cohort_dims = [
            item
            for item in _entes_no_as_of(session, sphere_dims, snapshot_as_of)
            if _pertence(item, selected)
        ]
    else:
        cohort_dims = [
            _benchmark_ente(item)
            for item in repository.list_entes_da_coorte(
                session, coorte=selected, esfera=ente.esfera
            )
        ]
    dim_by_code = {dim.cod_ibge: dim for dim in cohort_dims}
    if cod_ibge not in dim_by_code:
        # Defesa contra divergência entre a regra Python e a consulta SQL.
        raise AppError(
            status=422,
            title="Ente fora da coorte",
            detail=f"{cod_ibge} não satisfaz os limites persistidos de {selected.codigo}.",
        )

    marts = repository.list_mart_indicador(
        session,
        cods_ibge=dim_by_code,
        indicador=resolved_indicator,
        periodo=resolved_period,
    )
    entregas = repository.list_entregas_rreo(
        session,
        cods_ibge=dim_by_code,
        periodo=resolved_period,
        as_of=snapshot_as_of,
    )
    if resolved_indicator == "pessoal_executivo":
        periodo_rgf = _rgf_periodo(resolved_period)
        if periodo_rgf is None:
            effective = {}
        else:
            entregas_rgf = repository.list_entregas_relatorio(
                session,
                cods_ibge=dim_by_code,
                relatorio="RGF",
                periodo=periodo_rgf,
                as_of=snapshot_as_of,
            )
            effective = _effective_personnel_rows(
                marts,
                entregas_rreo=entregas,
                entregas_rgf=entregas_rgf,
                periodo_rreo=resolved_period,
                periodo_rgf=periodo_rgf,
            )
    else:
        effective = _effective_rows(
            marts, entregas, allow_legacy_fallback=as_of is None
        )
    target = effective.get(cod_ibge)
    if target is None:
        raise AppError(
            status=404,
            title="Indicador sem versão reproduzível",
            detail=(
                f"Sem {resolved_indicator} de {cod_ibge} em {resolved_period} "
                f"reproduzível em {snapshot_as_of.isoformat()}."
            ),
        )

    use_pct = target.mart.valor_pct_rcl is not None
    denominador = target.mart.denominador
    unidade = _unidade(target.mart)
    rows: list[tuple[_EffectiveMart, _BenchmarkEnte, Decimal]] = []
    base_divergente = 0
    for peer_code, peer in effective.items():
        dim = dim_by_code.get(peer_code)
        if dim is None:
            continue
        # Comparar percentuais de bases diferentes (RCL × impostos+transferências) daria
        # um ranking com números que não medem a mesma coisa.
        if use_pct and peer.mart.denominador != denominador:
            base_divergente += 1
            continue
        raw_value = peer.mart.valor_pct_rcl if use_pct else peer.mart.valor_rs
        if raw_value is not None:
            rows.append((peer, dim, Decimal(raw_value)))
    if not rows:
        raise AppError(
            status=404,
            title="Coorte sem valores comparáveis",
            detail=(
                f"Nenhum valor real de {resolved_indicator}/{unidade} em "
                f"{selected.codigo} para {resolved_period}."
            ),
        )

    values = _percent_rank(
        rows,
        ente_destaque=cod_ibge,
        as_of=snapshot_as_of,
        coorte=selected,
        # Só uma métrica em R$ vira R$/hab; ``rcl_per_capita`` já nasce dividida.
        per_capita=(unidade == "brl"),
    )
    values = _add_personnel_lineage(
        indicador=resolved_indicator,
        periodo=resolved_period,
        values=values,
    )
    anchor = next((item for item in values if item.cod_ibge == cod_ibge), None)
    if anchor is None:
        raise AppError(
            status=422,
            title="Ente sem métrica comparável",
            detail=f"{cod_ibge} não possui a métrica {unidade} exigida pela coorte.",
        )
    sentido = _sentido(session, resolved_indicator, ente.esfera)
    digest = _snapshot_hash(
        coorte=selected,
        indicador=resolved_indicator,
        periodo=resolved_period,
        values=values,
    )
    existing_snapshot = repository.snapshot_rows(
        session,
        coorte_id=selected.id,
        indicador=resolved_indicator,
        periodo=resolved_period,
        snapshot_hash=digest,
    )
    expected_codes = {item.cod_ibge for item in values}
    snapshot_complete = (
        len(existing_snapshot) == len(values)
        and {item.cod_ibge for item in existing_snapshot} == expected_codes
    )
    response_as_of = snapshot_as_of
    if snapshot_complete:
        # Revalidations HTTP precisam de representação estável. Um snapshot
        # imutável já materializado conserva o instante em que foi calculado, em
        # vez de trocar ``as_of`` por ``now()`` a cada GET sem mudança fiscal.
        response_as_of = existing_snapshot[0].as_of
        values = [
            item.model_copy(update={"as_of": response_as_of})
            for item in values
        ]
    if not snapshot_complete:
        _materialize(
            session,
            coorte=selected,
            indicador=resolved_indicator,
            periodo=resolved_period,
            unidade=unidade,
            sentido=sentido,
            as_of=snapshot_as_of,
            snapshot_hash=digest,
            values=values,
        )

    memoria = {
        "snapshot_hash": digest,
        "snapshot_reutilizado": snapshot_complete,
        "materializado_em": "gold.mart_benchmark",
        "fonte_valores": "gold.mart_indicador",
        "metrica": "valor_pct_rcl" if use_pct else "valor_rs",
        "denominador": denominador,
        "base_valor_ente": (
            str(target.mart.base_valor) if target.mart.base_valor is not None else None
        ),
        "entes_excluidos_por_base_divergente": base_divergente,
        "formula_percentil": "100 * (RANK(valor ASC) - 1) / (N - 1); N=1 => 0",
        "empates": "RANK comum; posições seguintes preservam a lacuna SQL-standard",
        "formula_quantis": "percentile_cont com interpolação linear Type 7: h=(N-1)*p",
        "criterio_coorte": selected.criterio,
        "faixa_coorte": selected.faixa,
        "mesma_esfera": ente.esfera,
        "entes_elegiveis_na_dimensao": len(cohort_dims),
        "entes_com_valor_comparavel": len(values),
        "entes_excluidos_sem_valor": len(cohort_dims) - len(values),
        "as_of_solicitado": as_of.isoformat() if as_of else None,
        "as_of_efetivo": response_as_of.isoformat(),
        "rastreio": (
            "Cada ponto e sua versão estão persistidos em gold.mart_benchmark pelo "
            "snapshot_hash; source_refs abaixo identificam as entregas de origem."
        ),
    }
    if resolved_indicator == "pessoal_executivo":
        memoria["formula_indicador"] = (
            "despesa_liquida_pessoal (RGF Anexo 01) / "
            "RCL_12m (RREO Anexo 03) * 100"
        )
        memoria["fontes_compostas"] = True
    options = _indicadores_disponiveis(
        session,
        ente=ente,
        periodo=resolved_period,
        as_of=response_as_of,
        allow_legacy_fallback=as_of is None,
    )
    elegiveis = len(cohort_dims)
    comparaveis = len(values)
    cobertura_pct = (
        Decimal(0)
        if elegiveis == 0
        else Decimal(comparaveis) / Decimal(elegiveis) * Decimal(100)
    )
    return _Snapshot(
        indicador=resolved_indicator,
        indicador_rotulo=_label(resolved_indicator),
        unidade=unidade,
        sentido=sentido,
        periodo=resolved_period,
        as_of=snapshot_as_of,
        coorte=_coorte_out(selected),
        coortes_disponiveis=[_coorte_out(item) for item in aplicaveis],
        indicadores_disponiveis=options,
        valores=values,
        distribuicao=_distribution(values),
        cobertura=CoberturaBenchmark(
            entes_elegiveis=elegiveis,
            entes_com_valor=comparaveis,
            entes_sem_valor=elegiveis - comparaveis,
            percentual=cobertura_pct,
            amostra_parcial=comparaveis < elegiveis,
        ),
        memoria=memoria,
        source_refs=_unique_sources(values),
    )


def _build_snapshot_cached(
    session: Session,
    *,
    cod_ibge: str,
    indicador: str | None,
    coorte_raw: str | None,
    periodo: str | None,
    as_of: datetime | None,
) -> _Snapshot:
    """Reuse a bounded, short-lived materialized snapshot for large rankings."""

    cacheable = (
        as_of is None
        and indicador is not None
        and coorte_raw is not None
        and coorte_raw not in _CRITERIO_ORDEM
        and periodo is not None
    )
    if cacheable:
        assert indicador is not None
        assert coorte_raw is not None
        assert periodo is not None
        identity = repository.latest_snapshot_identity(
            session,
            coorte=coorte_raw,
            indicador=indicador,
            periodo=periodo,
            cod_ibge_ancora=cod_ibge,
        )
        if identity is not None and identity[1] >= _RANKING_CACHE_MIN_ROWS:
            key = (cod_ibge, indicador, coorte_raw, periodo, identity[0])
            cached = _snapshot_cache_get(key)
            if cached is not None:
                return cached

    snapshot = _build_snapshot(
        session,
        cod_ibge=cod_ibge,
        indicador=indicador,
        coorte_raw=coorte_raw,
        periodo=periodo,
        as_of=as_of,
    )
    if cacheable:
        assert indicador is not None
        assert coorte_raw is not None
        assert periodo is not None
        identity = repository.latest_snapshot_identity(
            session,
            coorte=coorte_raw,
            indicador=indicador,
            periodo=periodo,
            cod_ibge_ancora=cod_ibge,
        )
        if identity is not None and identity[1] >= _RANKING_CACHE_MIN_ROWS:
            key = (cod_ibge, indicador, coorte_raw, periodo, identity[0])
            _snapshot_cache_put(key, snapshot)
    return snapshot


def build_benchmark(
    session: Session,
    *,
    cod_ibge: str,
    indicador: str | None = None,
    coorte: str | None = None,
    periodo: str | None = None,
    as_of: datetime | None = None,
) -> BenchmarkResponse:
    snapshot = _build_snapshot_cached(
        session,
        cod_ibge=cod_ibge,
        indicador=indicador,
        coorte_raw=coorte,
        periodo=periodo,
        as_of=as_of,
    )
    anchor = next(item for item in snapshot.valores if item.cod_ibge == cod_ibge)
    return BenchmarkResponse(
        indicador=snapshot.indicador,
        indicador_rotulo=snapshot.indicador_rotulo,
        unidade=snapshot.unidade,
        sentido=snapshot.sentido,
        periodo=snapshot.periodo,
        as_of=snapshot.as_of,
        coorte=snapshot.coorte,
        coortes_disponiveis=snapshot.coortes_disponiveis,
        indicadores_disponiveis=snapshot.indicadores_disponiveis,
        quantidade=len(snapshot.valores),
        cobertura=snapshot.cobertura,
        distribuicao=snapshot.distribuicao,
        ente=anchor,
        memoria=snapshot.memoria,
        source_refs=snapshot.source_refs,
    )


def build_evolucao(
    session: Session,
    *,
    cod_ibge: str,
    indicador: str | None = None,
    coorte: str | None = None,
    periodos: int = 6,
    as_of: datetime | None = None,
) -> BenchmarkEvolucaoResponse:
    """Trajetória do ente **dentro da coorte**, período a período (Sprint 25D).

    A foto de um período responde "onde estou"; a série responde "para onde estou indo
    em relação a quem se parece comigo" — a pergunta que faz um gestor agir. A coorte é
    fixada no período mais recente e repetida nos anteriores: se ela mudasse a cada
    ponto, a variação de posição misturaria movimento do ente com troca de régua.

    Período sem valor comparável **não vira ponto** e é devolvido em
    ``periodos_sem_comparacao`` — interpolar posição em ranking não significa nada.
    """
    disponiveis = [
        periodo
        for periodo, codigo in repository.list_periodos_indicadores_ente(
            session, cod_ibge=cod_ibge
        )
        if indicador is None or codigo == indicador
    ]
    if not disponiveis:
        raise AppError(
            status=404,
            title="Indicador ausente",
            detail=f"Sem mart_indicador de {indicador or 'qualquer indicador'} para {cod_ibge}.",
        )
    # list_periodos_indicadores_ente já vem em ordem decrescente de período.
    janela = sorted(dict.fromkeys(disponiveis), reverse=True)[:periodos]

    pontos: list[EvolucaoPonto] = []
    sem_comparacao: list[str] = []
    referencia: _Snapshot | None = None
    coorte_fixa = coorte
    for periodo in sorted(janela):
        try:
            snapshot = _build_snapshot(
                session,
                cod_ibge=cod_ibge,
                indicador=indicador,
                coorte_raw=coorte_fixa,
                periodo=periodo,
                as_of=as_of,
            )
        except AppError:
            sem_comparacao.append(periodo)
            continue
        anchor = next(
            (item for item in snapshot.valores if item.cod_ibge == cod_ibge), None
        )
        if anchor is None:
            sem_comparacao.append(periodo)
            continue
        referencia = snapshot
        coorte_fixa = coorte_fixa or snapshot.coorte.codigo
        pontos.append(
            EvolucaoPonto(
                periodo=snapshot.periodo,
                valor_ente=anchor.valor,
                posicao=anchor.posicao,
                quantidade=len(snapshot.valores),
                percentil=anchor.percentil,
                mediana=snapshot.distribuicao.mediana,
                p10=snapshot.distribuicao.p10,
                p90=snapshot.distribuicao.p90,
                valor_ente_per_capita=anchor.valor_per_capita,
                cobertura=snapshot.cobertura,
                as_of=snapshot.as_of,
                source_ref=anchor.source_ref,
            )
        )
    if referencia is None:
        raise AppError(
            status=404,
            title="Sem série comparável",
            detail=(
                f"Nenhum dos {len(janela)} períodos de {cod_ibge} tem coorte comparável "
                f"para {indicador or 'o indicador resolvido'}."
            ),
        )
    observacao = None
    if sem_comparacao:
        observacao = (
            f"{len(pontos)} de {len(janela)} períodos entraram na comparação; sem coorte "
            f"comparável em {', '.join(sem_comparacao)}. Períodos sem par não são "
            "interpolados."
        )
    return BenchmarkEvolucaoResponse(
        cod_ibge=cod_ibge,
        indicador=referencia.indicador,
        indicador_rotulo=referencia.indicador_rotulo,
        unidade=referencia.unidade,
        sentido=referencia.sentido,
        coorte=referencia.coorte,
        coortes_disponiveis=referencia.coortes_disponiveis,
        indicadores_disponiveis=referencia.indicadores_disponiveis,
        periodos_solicitados=periodos,
        periodos_sem_comparacao=sem_comparacao,
        observacao=observacao,
        pontos=pontos,
        memoria={
            "coorte_fixada_em": referencia.coorte.codigo,
            "regra_coorte": (
                "A coorte é a mesma em todos os pontos; mudar a régua entre períodos "
                "faria a posição variar sem que o ente tivesse mudado."
            ),
            "formula_percentil": referencia.memoria.get("formula_percentil"),
            "metrica": referencia.memoria.get("metrica"),
            "denominador": referencia.memoria.get("denominador"),
            "materializado_em": "gold.mart_benchmark (um snapshot por período)",
        },
        source_refs=referencia.source_refs,
    )


def _sort_values(
    values: list[BenchmarkValue], ordenar: OrdenacaoRanking, ordem: OrdemRanking
) -> list[BenchmarkValue]:
    reverse = ordem == "desc"
    if ordenar == "nome":
        return sorted(
            values,
            key=lambda item: ((item.nome or "").casefold(), item.cod_ibge),
            reverse=reverse,
        )
    if ordenar == "cod_ibge":
        return sorted(values, key=lambda item: item.cod_ibge, reverse=reverse)
    if ordenar == "valor":
        return sorted(values, key=lambda item: (item.valor, item.cod_ibge), reverse=reverse)
    if ordenar == "percentil":
        return sorted(values, key=lambda item: (item.percentil, item.cod_ibge), reverse=reverse)
    return sorted(values, key=lambda item: (item.posicao, item.cod_ibge), reverse=reverse)


def build_ranking(
    session: Session,
    *,
    cod_ibge: str,
    indicador: str | None = None,
    coorte: str | None = None,
    periodo: str | None = None,
    as_of: datetime | None = None,
    ordenar: OrdenacaoRanking = "posicao",
    ordem: OrdemRanking = "asc",
    pagina: int = 1,
    por_pagina: int = 100,
) -> BenchmarkRankingResponse:
    snapshot = _build_snapshot_cached(
        session,
        cod_ibge=cod_ibge,
        indicador=indicador,
        coorte_raw=coorte,
        periodo=periodo,
        as_of=as_of,
    )
    anchor = next(item for item in snapshot.valores if item.cod_ibge == cod_ibge)
    ordered = _sort_values(snapshot.valores, ordenar, ordem)
    start = (pagina - 1) * por_pagina
    total = len(ordered)
    total_pages = max(1, (total + por_pagina - 1) // por_pagina)
    page_items = [
        item
        if item.cod_ibge == cod_ibge
        else item.model_copy(update={"memoria": None})
        for item in ordered[start : start + por_pagina]
    ]
    return BenchmarkRankingResponse(
        indicador=snapshot.indicador,
        indicador_rotulo=snapshot.indicador_rotulo,
        unidade=snapshot.unidade,
        sentido=snapshot.sentido,
        periodo=snapshot.periodo,
        as_of=snapshot.as_of,
        coorte=snapshot.coorte,
        coortes_disponiveis=snapshot.coortes_disponiveis,
        indicadores_disponiveis=snapshot.indicadores_disponiveis,
        ordenar=ordenar,
        ordem=ordem,
        itens=page_items,
        ente_ancora=anchor,
        total=total,
        pagina=pagina,
        por_pagina=por_pagina,
        total_paginas=total_pages,
        cobertura=snapshot.cobertura,
        memoria=snapshot.memoria,
        source_refs=snapshot.source_refs,
    )
