"""Acesso a dados do catálogo (gold dims). Lê silver para conformar dim_ente."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from sqlalchemy import String, and_, cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.catalog.models import DimEnte, DimLimiteLegal, DimPeriodo
from app.modules.ingestion.models import DimEntrega, IbgePib, IbgePopulacao, SilverEnte


# --- dim_ente ---
def get_dim_ente(session: Session, cod_ibge: str) -> DimEnte | None:
    return session.get(DimEnte, cod_ibge)


def list_dim_entes(session: Session, cods_ibge: Iterable[str]) -> list[DimEnte]:
    """Cadastros conformados para um conjunto de códigos IBGE (uma consulta)."""
    cods = list(cods_ibge)
    if not cods:
        return []
    return list(session.scalars(select(DimEnte).where(DimEnte.cod_ibge.in_(cods))))


def list_ibges_by_prefixes(session: Session, prefixes: Iterable[str]) -> list[str]:
    """Códigos IBGE de municípios (7 dígitos) cujos 2 primeiros dígitos ∈ ``prefixes``.

    Os 2 primeiros dígitos do código IBGE de um município identificam a UF (o mesmo
    código do ente estadual). Serve à visão estadual: todos os municípios da UF.
    """
    prefs = [p for p in dict.fromkeys(prefixes) if p]
    if not prefs:
        return []
    return list(
        session.scalars(
            select(DimEnte.cod_ibge).where(
                func.length(DimEnte.cod_ibge) == 7,
                func.substr(DimEnte.cod_ibge, 1, 2).in_(prefs),
            )
        )
    )


def upsert_dim_ente(session: Session, *, cod_ibge: str, valores: dict[str, Any]) -> None:
    stmt = pg_insert(DimEnte).values(cod_ibge=cod_ibge, **valores)
    stmt = stmt.on_conflict_do_update(index_elements=["cod_ibge"], set_=valores)
    session.execute(stmt)


def get_silver_ente(session: Session, cod_ibge: str) -> SilverEnte | None:
    return session.get(SilverEnte, cod_ibge)


def latest_ibge_populacao(session: Session, cod_ibge: str) -> tuple[int, int, str] | None:
    """(populacao, ano_ref, versao) mais recente do IBGE para o ente."""
    row = session.execute(
        select(
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
            IbgePopulacao.cod_ibge == cod_ibge,
            IbgePopulacao.populacao.is_not(None),
            DimEntrega.vigente.is_(True),
        )
        .order_by(IbgePopulacao.ano_ref.desc(), DimEntrega.homologada_em.desc())
        .limit(1)
    ).first()
    if row is None:
        row = session.execute(
            select(
                IbgePopulacao.populacao,
                IbgePopulacao.ano_ref,
                IbgePopulacao.versao_entrega,
            )
            .where(
                IbgePopulacao.cod_ibge == cod_ibge,
                IbgePopulacao.populacao.is_not(None),
            )
            .order_by(
                IbgePopulacao.ano_ref.desc(), IbgePopulacao.versao_entrega.desc()
            )
            .limit(1)
        ).first()
    if row is None or row[0] is None:
        return None
    return int(row[0]), int(row[1]), str(row[2])


def latest_ibge_pib(session: Session, cod_ibge: str) -> tuple[Decimal, int, str] | None:
    row = session.execute(
        select(IbgePib.pib_nominal, IbgePib.ano_ref, IbgePib.versao_entrega)
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
            IbgePib.cod_ibge == cod_ibge,
            IbgePib.pib_nominal.is_not(None),
            DimEntrega.vigente.is_(True),
        )
        .order_by(IbgePib.ano_ref.desc(), DimEntrega.homologada_em.desc())
        .limit(1)
    ).first()
    if row is None:
        row = session.execute(
            select(IbgePib.pib_nominal, IbgePib.ano_ref, IbgePib.versao_entrega)
            .where(
                IbgePib.cod_ibge == cod_ibge,
                IbgePib.pib_nominal.is_not(None),
            )
            .order_by(IbgePib.ano_ref.desc(), IbgePib.versao_entrega.desc())
            .limit(1)
        ).first()
    if row is None or row[0] is None:
        return None
    return Decimal(row[0]), int(row[1]), str(row[2])


# --- dim_limite_legal ---
def get_limite(
    session: Session, *, indicador: str, esfera: str, poder: str = ""
) -> DimLimiteLegal | None:
    return session.scalar(
        select(DimLimiteLegal).where(
            DimLimiteLegal.indicador == indicador,
            DimLimiteLegal.esfera == esfera,
            DimLimiteLegal.poder == poder,
        )
    )


def upsert_limite(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(DimLimiteLegal).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["indicador", "esfera", "poder"],
        set_={k: valores[k] for k in ("sentido", "teto_pct", "alerta_pct", "prudencial_pct")},
    )
    session.execute(stmt)


def list_limites(session: Session) -> list[DimLimiteLegal]:
    return list(session.scalars(select(DimLimiteLegal).order_by(DimLimiteLegal.indicador)))


# --- dim_periodo ---
def list_periodos(session: Session) -> list[DimPeriodo]:
    return list(session.scalars(select(DimPeriodo)))


def upsert_periodo(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(DimPeriodo).values(**valores)
    stmt = stmt.on_conflict_do_nothing(index_elements=["codigo"])
    session.execute(stmt)
