"""Persistência da dívida: gold materializada e leitura das três fontes silver."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.debt.models import (
    DimCredor,
    DimOrigemDivida,
    FatoCapag,
    FatoDivida,
    FatoVencimento,
)
from app.modules.ingestion.models import (
    SadipemCronogramaPgto,
    SadipemOpContratada,
    SadipemPvl,
    SilverRgf,
    TesouroCapag,
)


def read_rgf_anexo(
    session: Session,
    *,
    cod_ibge: str,
    periodo: str,
    versao_entrega: str,
    numero: str,
) -> list[SilverRgf]:
    return list(
        session.scalars(
            select(SilverRgf).where(
                SilverRgf.cod_ibge == cod_ibge,
                SilverRgf.periodo == periodo,
                SilverRgf.versao_entrega == versao_entrega,
                SilverRgf.anexo.ilike(f"%{numero}%"),
            )
        )
    )


def distinct_periodos_ddcl(session: Session, *, cod_ibge: str) -> list[str]:
    return list(
        session.scalars(
            select(SilverRgf.periodo)
            .where(SilverRgf.cod_ibge == cod_ibge, SilverRgf.anexo.ilike("%02%"))
            .distinct()
            .order_by(SilverRgf.periodo)
        )
    )


def upsert_fato_divida(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(FatoDivida).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "periodo", "versao_entrega"],
        set_={
            campo: valores.get(campo)
            for campo in (
                "dc_bruta",
                "disponibilidades",
                "haveres",
                "dcl",
                "dcl_reportada",
                "diferenca_reconciliacao",
                "rcl_ajustada",
                "pct_rcl",
                "saldo_interno",
                "saldo_externo",
            )
        },
    )
    session.execute(stmt)


def get_fato_divida(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> FatoDivida | None:
    return session.scalar(
        select(FatoDivida).where(
            FatoDivida.cod_ibge == cod_ibge,
            FatoDivida.periodo == periodo,
            FatoDivida.versao_entrega == versao_entrega,
        )
    )


def list_fatos_divida(session: Session, *, cod_ibge: str) -> list[FatoDivida]:
    return list(
        session.scalars(
            select(FatoDivida)
            .where(FatoDivida.cod_ibge == cod_ibge)
            .order_by(FatoDivida.periodo, FatoDivida.versao_entrega)
        )
    )


def upsert_origem(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(DimOrigemDivida).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["codigo"], set_={"descricao": valores["descricao"]}
    )
    session.execute(stmt)


def list_origens(session: Session) -> list[DimOrigemDivida]:
    return list(session.scalars(select(DimOrigemDivida).order_by(DimOrigemDivida.codigo)))


def upsert_credor(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(DimCredor).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["codigo"],
        set_={
            "descricao": valores["descricao"],
            "parent_codigo": valores.get("parent_codigo"),
            "nivel": valores["nivel"],
            "path": valores["path"],
            "origem": valores.get("origem"),
        },
    )
    session.execute(stmt)


def list_credores(session: Session, codigos: Iterable[str] | None = None) -> list[DimCredor]:
    stmt = select(DimCredor)
    if codigos is not None:
        codigos = list(codigos)
        if not codigos:
            return []
        stmt = stmt.where(DimCredor.codigo.in_(codigos))
    return list(session.scalars(stmt.order_by(DimCredor.codigo)))


def read_capag(
    session: Session, *, cod_ibge: str, ano_ref: int, versao_entrega: str
) -> TesouroCapag | None:
    return session.scalar(
        select(TesouroCapag).where(
            TesouroCapag.cod_ibge == cod_ibge,
            TesouroCapag.ano_ref == ano_ref,
            TesouroCapag.versao_entrega == versao_entrega,
        )
    )


def upsert_fato_capag(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(FatoCapag).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "ano_ref", "versao_entrega"],
        set_={
            campo: valores.get(campo)
            for campo in (
                "nota_final",
                "ind_endividamento",
                "ind_poupanca",
                "ind_liquidez",
                "metodologia_versao",
            )
        },
    )
    session.execute(stmt)


def get_fato_capag(
    session: Session, *, cod_ibge: str, ano_ref: int, versao_entrega: str
) -> FatoCapag | None:
    return session.scalar(
        select(FatoCapag).where(
            FatoCapag.cod_ibge == cod_ibge,
            FatoCapag.ano_ref == ano_ref,
            FatoCapag.versao_entrega == versao_entrega,
        )
    )


def read_operacoes(
    session: Session, *, cod_ibge: str, valid_time: date, versao_entrega: str
) -> list[SadipemOpContratada]:
    return list(
        session.scalars(
            select(SadipemOpContratada).where(
                SadipemOpContratada.cod_ibge == cod_ibge,
                SadipemOpContratada.valid_time == valid_time,
                SadipemOpContratada.versao_entrega == versao_entrega,
            )
        )
    )


def read_cronograma(
    session: Session, *, cod_ibge: str, valid_time: date, versao_entrega: str
) -> list[SadipemCronogramaPgto]:
    return list(
        session.scalars(
            select(SadipemCronogramaPgto).where(
                SadipemCronogramaPgto.cod_ibge == cod_ibge,
                SadipemCronogramaPgto.valid_time == valid_time,
                SadipemCronogramaPgto.versao_entrega == versao_entrega,
            )
        )
    )


def replace_vencimentos(
    session: Session,
    *,
    cod_ibge: str,
    periodo_ref: str,
    versao_entrega: str,
    rows: list[dict[str, Any]],
) -> None:
    session.execute(
        delete(FatoVencimento).where(
            FatoVencimento.cod_ibge == cod_ibge,
            FatoVencimento.periodo_ref == periodo_ref,
            FatoVencimento.versao_entrega == versao_entrega,
        )
    )
    if rows:
        session.execute(
            insert(FatoVencimento),
            [{"id": uuid.uuid4(), **row} for row in rows],
        )


def list_vencimentos(
    session: Session, *, cod_ibge: str, periodo_ref: str, versao_entrega: str
) -> list[FatoVencimento]:
    return list(
        session.scalars(
            select(FatoVencimento)
            .where(
                FatoVencimento.cod_ibge == cod_ibge,
                FatoVencimento.periodo_ref == periodo_ref,
                FatoVencimento.versao_entrega == versao_entrega,
            )
            .order_by(FatoVencimento.ano, FatoVencimento.mes, FatoVencimento.id_operacao)
        )
    )


def read_pvl(session: Session, *, cod_ibge: str) -> list[SadipemPvl]:
    """Pedidos de verificação de limites (PVL/CDP) do ente, na versão vigente de cada um.

    Fonte nacional: a versão corrente resolve-se pelo ``max(versao_entrega)`` do próprio
    silver (a ``dim_entrega`` do SADIPEM é por lote 'BR' — ver Sprint 21).
    """
    ultima = session.scalar(
        select(func.max(SadipemPvl.versao_entrega)).where(SadipemPvl.cod_ibge == cod_ibge)
    )
    if ultima is None:
        return []
    return list(
        session.scalars(
            select(SadipemPvl)
            .where(SadipemPvl.cod_ibge == cod_ibge, SadipemPvl.versao_entrega == ultima)
            .order_by(SadipemPvl.data_analise.desc().nullslast(), SadipemPvl.id_pvl)
        )
    )
