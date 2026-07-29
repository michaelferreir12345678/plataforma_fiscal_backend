"""Acesso a dados de coortes, indicadores e snapshots de benchmarking."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, String, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.benchmark.models import DimCoorte, DimCoorteVersao, MartBenchmark
from app.modules.catalog.models import DimEnte, DimLimiteLegal
from app.modules.indicators.models import MartIndicador
from app.modules.ingestion.models import DimEntrega, IbgePib, IbgePopulacao, SilverEnte


def _coorte_from_version(row: DimCoorteVersao) -> DimCoorte:
    return DimCoorte(
        id=row.coorte_id,
        codigo=row.codigo,
        criterio=row.criterio,
        faixa=row.faixa,
        rotulo=row.rotulo,
        unidade_criterio=row.unidade_criterio,
        limite_inferior=row.limite_inferior,
        limite_superior=row.limite_superior,
        inclusivo_superior=row.inclusivo_superior,
        ordem=row.ordem,
        ativo=row.ativo,
        source_ref=row.source_ref,
        atualizado_em=row.valido_desde,
    )


def list_coortes(
    session: Session,
    *,
    somente_ativas: bool = True,
    as_of: datetime | None = None,
) -> list[DimCoorte]:
    if as_of is not None:
        historical = select(DimCoorteVersao).where(
            DimCoorteVersao.valido_desde <= as_of,
            or_(
                DimCoorteVersao.valido_ate.is_(None),
                DimCoorteVersao.valido_ate > as_of,
            ),
        )
        if somente_ativas:
            historical = historical.where(DimCoorteVersao.ativo.is_(True))
        rows = session.scalars(
            historical.order_by(DimCoorteVersao.criterio, DimCoorteVersao.ordem)
        )
        return [_coorte_from_version(row) for row in rows]
    stmt = select(DimCoorte)
    if somente_ativas:
        stmt = stmt.where(DimCoorte.ativo.is_(True))
    return list(session.scalars(stmt.order_by(DimCoorte.criterio, DimCoorte.ordem)))


def get_coorte(
    session: Session, identificador: str, *, as_of: datetime | None = None
) -> DimCoorte | None:
    """Resolve uma coorte por código estável ou UUID textual."""
    if as_of is not None:
        try:
            historical_id = uuid.UUID(identificador)
        except ValueError:
            historical_id = None
        identity_clause = (
            or_(
                DimCoorteVersao.codigo == identificador,
                DimCoorteVersao.coorte_id == historical_id,
            )
            if historical_id is not None
            else DimCoorteVersao.codigo == identificador
        )
        row = session.scalar(
            select(DimCoorteVersao)
            .where(
                identity_clause,
                DimCoorteVersao.valido_desde <= as_of,
                or_(
                    DimCoorteVersao.valido_ate.is_(None),
                    DimCoorteVersao.valido_ate > as_of,
                ),
            )
            .order_by(DimCoorteVersao.valido_desde.desc())
        )
        return _coorte_from_version(row) if row is not None else None
    by_code = session.scalar(select(DimCoorte).where(DimCoorte.codigo == identificador))
    if by_code is not None:
        return by_code
    try:
        coorte_id = uuid.UUID(identificador)
    except ValueError:
        return None
    return session.get(DimCoorte, coorte_id)


def _numeric_membership(column: Any, coorte: DimCoorte) -> list[Any]:
    clauses: list[Any] = [column.is_not(None)]
    if coorte.limite_inferior is not None:
        clauses.append(column >= coorte.limite_inferior)
    if coorte.limite_superior is not None:
        operator = (
            column <= coorte.limite_superior
            if coorte.inclusivo_superior
            else column < coorte.limite_superior
        )
        clauses.append(operator)
    return clauses


def list_entes_da_coorte(
    session: Session, *, coorte: DimCoorte, esfera: str
) -> list[DimEnte]:
    """Entes da mesma esfera que satisfazem a definição persistida da coorte."""
    stmt: Select[tuple[DimEnte]] = select(DimEnte).where(DimEnte.esfera == esfera)
    if coorte.criterio == "porte":
        stmt = stmt.where(*_numeric_membership(DimEnte.populacao, coorte))
    elif coorte.criterio == "pib":
        stmt = stmt.where(*_numeric_membership(DimEnte.pib, coorte))
    else:
        stmt = stmt.where(func.upper(DimEnte.regiao) == coorte.faixa.upper())
    return list(session.scalars(stmt.order_by(DimEnte.cod_ibge)))


def list_entes_da_esfera(session: Session, *, esfera: str) -> list[DimEnte]:
    return list(
        session.scalars(
            select(DimEnte)
            .where(DimEnte.esfera == esfera)
            .order_by(DimEnte.cod_ibge)
        )
    )


def ibge_populacao_as_of(
    session: Session, *, cods_ibge: Iterable[str], as_of: datetime
) -> dict[str, tuple[int, int, str]]:
    cods = list(dict.fromkeys(cods_ibge))
    if not cods:
        return {}
    rows = session.execute(
        select(
            IbgePopulacao.cod_ibge,
            IbgePopulacao.populacao,
            IbgePopulacao.ano_ref,
            IbgePopulacao.versao_entrega,
        )
        .join(
            DimEntrega,
            and_(
                DimEntrega.cod_ibge == IbgePopulacao.cod_ibge,
                DimEntrega.relatorio == "IBGE-POP",
                DimEntrega.periodo == cast(IbgePopulacao.ano_ref, String),
                DimEntrega.versao_entrega == IbgePopulacao.versao_entrega,
            ),
        )
        .where(
            IbgePopulacao.cod_ibge.in_(cods),
            IbgePopulacao.populacao.is_not(None),
            DimEntrega.homologada_em <= as_of,
        )
        .distinct(IbgePopulacao.cod_ibge)
        .order_by(
            IbgePopulacao.cod_ibge,
            IbgePopulacao.ano_ref.desc(),
            DimEntrega.homologada_em.desc(),
        )
    )
    return {
        str(code): (int(value), int(year), str(version))
        for code, value, year, version in rows
    }


def ibge_pib_as_of(
    session: Session, *, cods_ibge: Iterable[str], as_of: datetime
) -> dict[str, tuple[Decimal, int, str]]:
    cods = list(dict.fromkeys(cods_ibge))
    if not cods:
        return {}
    rows = session.execute(
        select(
            IbgePib.cod_ibge,
            IbgePib.pib_nominal,
            IbgePib.ano_ref,
            IbgePib.versao_entrega,
        )
        .join(
            DimEntrega,
            and_(
                DimEntrega.cod_ibge == IbgePib.cod_ibge,
                DimEntrega.relatorio == "IBGE-PIB",
                DimEntrega.periodo == cast(IbgePib.ano_ref, String),
                DimEntrega.versao_entrega == IbgePib.versao_entrega,
            ),
        )
        .where(
            IbgePib.cod_ibge.in_(cods),
            IbgePib.pib_nominal.is_not(None),
            DimEntrega.homologada_em <= as_of,
        )
        .distinct(IbgePib.cod_ibge)
        .order_by(
            IbgePib.cod_ibge,
            IbgePib.ano_ref.desc(),
            DimEntrega.homologada_em.desc(),
        )
    )
    return {
        str(code): (Decimal(value), int(year), str(version))
        for code, value, year, version in rows
    }


def siconfi_entes_version_as_of(session: Session, *, as_of: datetime) -> str | None:
    return session.scalar(
        select(DimEntrega.versao_entrega)
        .where(
            DimEntrega.relatorio == "ENTES",
            DimEntrega.homologada_em <= as_of,
        )
        .order_by(DimEntrega.homologada_em.desc())
        .limit(1)
    )


def siconfi_populacoes(
    session: Session, *, cods_ibge: Iterable[str]
) -> dict[str, tuple[int, str]]:
    cods = list(dict.fromkeys(cods_ibge))
    if not cods:
        return {}
    return {
        str(code): (int(population), str(version))
        for code, population, version in session.execute(
            select(
                SilverEnte.cod_ibge,
                SilverEnte.populacao,
                SilverEnte.versao_entrega,
            ).where(
                SilverEnte.cod_ibge.in_(cods),
                SilverEnte.populacao.is_not(None),
                SilverEnte.versao_entrega.is_not(None),
            )
        )
    }


def latest_periodo(
    session: Session, *, cod_ibge: str, indicador: str | None = None
) -> str | None:
    stmt = select(func.max(MartIndicador.periodo)).where(MartIndicador.cod_ibge == cod_ibge)
    if indicador is not None:
        stmt = stmt.where(MartIndicador.indicador == indicador)
    return session.scalar(stmt)


def list_indicadores_ente_periodo(
    session: Session, *, cod_ibge: str, periodo: str
) -> list[str]:
    return list(
        session.scalars(
            select(MartIndicador.indicador)
            .where(MartIndicador.cod_ibge == cod_ibge, MartIndicador.periodo == periodo)
            .distinct()
            .order_by(MartIndicador.indicador)
        )
    )


def list_marts_ente_periodo(
    session: Session,
    *,
    cod_ibge: str,
    periodo: str,
) -> list[MartIndicador]:
    """Todas as versões dos indicadores do ente/período em uma única consulta."""

    return list(
        session.scalars(
            select(MartIndicador)
            .where(
                MartIndicador.cod_ibge == cod_ibge,
                MartIndicador.periodo == periodo,
            )
            .order_by(MartIndicador.indicador, MartIndicador.versao_entrega)
        )
    )


def list_periodos_indicadores_ente(
    session: Session, *, cod_ibge: str
) -> list[tuple[str, str]]:
    return [
        (str(periodo), str(indicador))
        for periodo, indicador in session.execute(
            select(MartIndicador.periodo, MartIndicador.indicador)
            .where(MartIndicador.cod_ibge == cod_ibge)
            .distinct()
            .order_by(MartIndicador.periodo.desc(), MartIndicador.indicador)
        )
    ]


def list_mart_indicador(
    session: Session,
    *,
    cods_ibge: Iterable[str],
    indicador: str,
    periodo: str,
) -> list[MartIndicador]:
    cods = list(dict.fromkeys(cods_ibge))
    if not cods:
        return []
    return list(
        session.scalars(
            select(MartIndicador)
            .where(
                MartIndicador.cod_ibge.in_(cods),
                MartIndicador.indicador == indicador,
                MartIndicador.periodo == periodo,
            )
            .order_by(MartIndicador.cod_ibge, MartIndicador.versao_entrega)
        )
    )


def list_entregas_relatorio(
    session: Session,
    *,
    cods_ibge: Iterable[str],
    relatorio: str,
    periodo: str,
    as_of: datetime,
) -> list[DimEntrega]:
    cods = list(dict.fromkeys(cods_ibge))
    if not cods:
        return []
    return list(
        session.scalars(
            select(DimEntrega)
            .where(
                DimEntrega.cod_ibge.in_(cods),
                DimEntrega.relatorio == relatorio,
                DimEntrega.periodo == periodo,
                DimEntrega.homologada_em <= as_of,
            )
            .order_by(DimEntrega.cod_ibge, DimEntrega.homologada_em.desc())
        )
    )


def list_entregas_rreo(
    session: Session, *, cods_ibge: Iterable[str], periodo: str, as_of: datetime
) -> list[DimEntrega]:
    return list_entregas_relatorio(
        session,
        cods_ibge=cods_ibge,
        relatorio="RREO",
        periodo=periodo,
        as_of=as_of,
    )


def get_sentido_limite(session: Session, *, indicador: str, esfera: str) -> str | None:
    return session.scalar(
        select(DimLimiteLegal.sentido)
        .where(DimLimiteLegal.indicador == indicador, DimLimiteLegal.esfera == esfera)
        .order_by(DimLimiteLegal.poder)
        .limit(1)
    )


def get_sentidos_limites(
    session: Session,
    *,
    indicadores: Iterable[str],
    esfera: str,
) -> dict[str, str]:
    """Primeiro sentido legal por indicador, seguindo a ordem de poder existente."""

    requested = list(dict.fromkeys(indicadores))
    if not requested:
        return {}
    rows = session.execute(
        select(
            DimLimiteLegal.indicador,
            DimLimiteLegal.sentido,
            DimLimiteLegal.poder,
        )
        .where(
            DimLimiteLegal.indicador.in_(requested),
            DimLimiteLegal.esfera == esfera,
        )
        .order_by(DimLimiteLegal.indicador, DimLimiteLegal.poder)
    )
    result: dict[str, str] = {}
    for indicador, sentido, _poder in rows:
        result.setdefault(str(indicador), str(sentido))
    return result


def upsert_mart_benchmark(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(MartBenchmark).values(**valores)
    # O hash identifica integralmente um snapshot. Uma vez materializado, ele e
    # imutavel: consultas posteriores nao devem reescrever metadados de auditoria.
    stmt = stmt.on_conflict_do_nothing(
        index_elements=[
            "snapshot_hash",
            "coorte_id",
            "indicador",
            "periodo",
            "cod_ibge",
        ]
    )
    session.execute(stmt)


def snapshot_rows(
    session: Session,
    *,
    coorte_id: uuid.UUID,
    indicador: str,
    periodo: str,
    snapshot_hash: str,
) -> list[MartBenchmark]:
    return list(
        session.scalars(
            select(MartBenchmark)
            .where(
                MartBenchmark.coorte_id == coorte_id,
                MartBenchmark.indicador == indicador,
                MartBenchmark.periodo == periodo,
                MartBenchmark.snapshot_hash == snapshot_hash,
            )
            .order_by(MartBenchmark.posicao, MartBenchmark.cod_ibge)
        )
    )


def latest_snapshot_identity(
    session: Session,
    *,
    coorte: str,
    indicador: str,
    periodo: str,
    cod_ibge_ancora: str,
) -> tuple[str, int] | None:
    """Hash e cardinalidade do snapshot materializado mais recente.

    Só considera snapshots que contêm o ente âncora. A leitura permite que os
    endpoints grandes reutilizem por poucos segundos a foto imutável já
    materializada, sem recalcular percentis e linhagem a cada paginação.
    """

    try:
        coorte_id = uuid.UUID(coorte)
    except ValueError:
        coorte_id = None
    coorte_clause = (
        or_(DimCoorte.id == coorte_id, DimCoorte.codigo == coorte)
        if coorte_id is not None
        else DimCoorte.codigo == coorte
    )
    anchor_count = func.count().filter(MartBenchmark.cod_ibge == cod_ibge_ancora)
    row = session.execute(
        select(
            MartBenchmark.snapshot_hash,
            func.count(MartBenchmark.id),
        )
        .join(DimCoorte, DimCoorte.id == MartBenchmark.coorte_id)
        .where(
            coorte_clause,
            MartBenchmark.indicador == indicador,
            MartBenchmark.periodo == periodo,
        )
        .group_by(MartBenchmark.snapshot_hash)
        .having(anchor_count > 0)
        .order_by(
            func.max(MartBenchmark.calculado_em).desc(),
            MartBenchmark.snapshot_hash.desc(),
        )
        .limit(1)
    ).first()
    if row is None:
        return None
    return str(row[0]), int(row[1])
