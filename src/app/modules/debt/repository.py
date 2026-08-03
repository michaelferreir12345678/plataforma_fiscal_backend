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
    SadipemCdp,
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
    """Série **anual** do cronograma. O residual sai por ``read_residual_cronograma``.

    Separar na leitura evita o erro mais provável de todos: a linha "Restante a pagar"
    entrar na série e ser desenhada como se fosse um ano.
    """
    return list(
        session.scalars(
            select(SadipemCronogramaPgto).where(
                SadipemCronogramaPgto.cod_ibge == cod_ibge,
                SadipemCronogramaPgto.valid_time == valid_time,
                SadipemCronogramaPgto.versao_entrega == versao_entrega,
                SadipemCronogramaPgto.residual.is_(False),
            )
        )
    )


def read_residual_cronograma(
    session: Session,
    *,
    cod_ibge: str,
    versao_entrega: str,
    valid_time: date | None = None,
    id_operacao: str | None = None,
) -> SadipemCronogramaPgto | None:
    """A linha "Restante a pagar": o que vence além do horizonte publicado."""
    stmt = select(SadipemCronogramaPgto).where(
        SadipemCronogramaPgto.cod_ibge == cod_ibge,
        SadipemCronogramaPgto.versao_entrega == versao_entrega,
        SadipemCronogramaPgto.residual.is_(True),
    )
    if valid_time is not None:
        stmt = stmt.where(SadipemCronogramaPgto.valid_time == valid_time)
    if id_operacao is not None:
        stmt = stmt.where(SadipemCronogramaPgto.id_operacao == id_operacao)
    return session.scalars(stmt.limit(1)).first()


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
            .order_by(FatoVencimento.ano, FatoVencimento.id_operacao)
        )
    )


def read_pvl_por_pleito(
    session: Session, *, cod_ibge: str, id_pleito: str
) -> SadipemPvl | None:
    """Um pleito específico do ente, na versão vigente. ``None`` se não é dele.

    O filtro por ``cod_ibge`` não é conveniência: sem ele, um identificador de pleito de
    outro município abriria a ficha completa de uma operação fora do escopo do usuário.
    """
    ultima = session.scalar(
        select(func.max(SadipemPvl.versao_entrega)).where(SadipemPvl.cod_ibge == cod_ibge)
    )
    if ultima is None:
        return None
    return session.scalar(
        select(SadipemPvl).where(
            SadipemPvl.cod_ibge == cod_ibge,
            SadipemPvl.versao_entrega == ultima,
            SadipemPvl.id_pvl == id_pleito,
        )
    )


def read_cdp_do_processo(
    session: Session, *, num_pvl: str | None, id_pleito: str
) -> list[SadipemCdp]:
    """Situação cadastral **deste pleito** na base nacional do CDP.

    O CDP não é por ente — ``res-cdp`` devolve o país inteiro (ver
    ``docs/sadipem_granularidade.md``) —, então a ponte é o processo. Mas casar por
    ``num_pvl`` é largo demais: um mesmo número de PVL cobre mais de um pleito (o
    PVL02.000653/2026-16 cobre os pleitos 73695 e 73438), e a ficha mostraria a situação
    do irmão como se fosse desta operação — duas linhas idênticas na tela, que se leem
    como defeito de renderização.

    ``id_pleito`` é a chave exata. ``num_pvl`` fica só como recurso para publicações
    antigas, que não traziam o identificador.
    """
    ultima = session.scalar(select(func.max(SadipemCdp.versao_entrega)))
    if ultima is None:
        return []
    exato = list(
        session.scalars(
            select(SadipemCdp)
            .where(SadipemCdp.versao_entrega == ultima, SadipemCdp.id_pleito == id_pleito)
            .order_by(SadipemCdp.data_ref.desc().nullslast())
        )
    )
    if exato or not num_pvl:
        return exato
    return list(
        session.scalars(
            select(SadipemCdp)
            .where(SadipemCdp.versao_entrega == ultima, SadipemCdp.num_pvl == num_pvl)
            .order_by(SadipemCdp.data_ref.desc().nullslast())
        )
    )


def read_cronograma_do_pleito(
    session: Session, *, cod_ibge: str, id_pleito: str
) -> list[SadipemCronogramaPgto]:
    """Cronograma daquele pleito, na versão vigente, ano a ano."""
    ultima = session.scalar(
        select(func.max(SadipemCronogramaPgto.versao_entrega)).where(
            SadipemCronogramaPgto.cod_ibge == cod_ibge
        )
    )
    if ultima is None:
        return []
    return list(
        session.scalars(
            select(SadipemCronogramaPgto)
            .where(
                SadipemCronogramaPgto.cod_ibge == cod_ibge,
                SadipemCronogramaPgto.versao_entrega == ultima,
                SadipemCronogramaPgto.id_operacao == id_pleito,
                SadipemCronogramaPgto.residual.is_(False),
            )
            .order_by(SadipemCronogramaPgto.ano)
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
