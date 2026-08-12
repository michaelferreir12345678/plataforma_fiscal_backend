"""Persistência de Patrimônio & MSC: leitura do silver (MSC/DCA) + gold materializada.

SQL/ORM só aqui (§7). O ``service`` orquestra a materialização e as regras; este módulo faz
leituras bitemporais no silver e escreve/lê a gold (``dim_conta_pcasp``, ``fato_msc_saldo``,
``mart_msc_rollup``, ``fato_balanco``) de forma idempotente por ``versao_entrega``.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import Table, bindparam, delete, exists, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.accounting.models import (
    DimContaPcasp,
    FatoBalanco,
    FatoMscSaldo,
    MartMscRollup,
)
from app.modules.ingestion.models import SilverDca, SilverMsc


# --- silver: MSC (mensal) ---
def read_msc_month(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[SilverMsc]:
    return list(
        session.scalars(
            select(SilverMsc).where(
                SilverMsc.cod_ibge == cod_ibge,
                SilverMsc.periodo == periodo,
                SilverMsc.versao_entrega == versao_entrega,
            )
        )
    )


def distinct_msc_periodos(session: Session, *, cod_ibge: str, ano: int) -> list[str]:
    """Meses (``AAAA-Mnn``) com MSC no silver para o ente/ano."""
    return list(
        session.scalars(
            select(SilverMsc.periodo)
            .where(SilverMsc.cod_ibge == cod_ibge, SilverMsc.periodo.like(f"{ano}-M%"))
            .distinct()
            .order_by(SilverMsc.periodo)
        )
    )


def anos_com_msc(session: Session, *, cod_ibge: str) -> list[int]:
    periodos = session.scalars(
        select(SilverMsc.periodo).where(SilverMsc.cod_ibge == cod_ibge).distinct()
    )
    anos = {int(p[:4]) for p in periodos if p[:4].isdigit()}
    return sorted(anos)


# --- silver: DCA (anual) ---
def read_dca_year(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[SilverDca]:
    return list(
        session.scalars(
            select(SilverDca)
            .where(
                SilverDca.cod_ibge == cod_ibge,
                SilverDca.periodo == periodo,
                SilverDca.versao_entrega == versao_entrega,
            )
            .order_by(SilverDca.linha_seq)
        )
    )


def anos_com_dca(session: Session, *, cod_ibge: str) -> list[int]:
    periodos = session.scalars(
        select(SilverDca.periodo).where(SilverDca.cod_ibge == cod_ibge).distinct()
    )
    anos = {int(p[:4]) for p in periodos if p[:4].isdigit()}
    return sorted(anos)


# --- gold.dim_conta_pcasp ---
def upsert_conta(session: Session, valores: dict[str, Any]) -> None:
    """Upsert de um nó PCASP. Descrição da DCA (com nome) supera o fallback da MSC."""
    # ``descricao_autoritativa`` é um controle (não é coluna): governa se a nova descrição
    # deve sobrescrever a existente. Extrai antes de montar o INSERT.
    autoritativa = bool(valores.pop("descricao_autoritativa", False))
    stmt = pg_insert(DimContaPcasp).values(**valores)
    set_: dict[str, Any] = {
        "nivel": valores["nivel"],
        "path": valores["path"],
        "classe": valores["classe"],
        "natureza": valores["natureza"],
        "parent_codigo": valores.get("parent_codigo"),
    }
    # Só sobrescreve a descrição quando a nova é "boa" (não é o fallback do próprio código).
    if autoritativa:
        set_["descricao"] = valores["descricao"]
    if valores.get("fonte_dado") is not None:
        set_["fonte_dado"] = valores["fonte_dado"]
    stmt = stmt.on_conflict_do_update(index_elements=["codigo"], set_=set_)
    session.execute(stmt)


def list_contas(session: Session, codigos: list[str]) -> list[DimContaPcasp]:
    if not codigos:
        return []
    return list(
        session.scalars(select(DimContaPcasp).where(DimContaPcasp.codigo.in_(codigos)))
    )


def descricoes_por_codigo(session: Session, codigos: list[str]) -> dict[str, DimContaPcasp]:
    return {c.codigo: c for c in list_contas(session, codigos)}


def atualizar_descricoes_fallback(
    session: Session, atualizacoes: list[dict[str, str]]
) -> int:
    """Substitui o rótulo genérico "código · Subitem" pelo nome oficial do glossário.

    Backfill de metadado puro (Sprint D1): não toca ``fato_msc_saldo`` (nenhum saldo é
    reprocessado), só o texto de exibição em ``dim_conta_pcasp``/``mart_msc_rollup`` — e só
    quando o valor atual é **exatamente** o fallback genérico daquele código (nunca
    sobrescreve uma descrição já vinda da DCA). ``atualizacoes`` é uma lista de
    ``{"codigo": ..., "fallback": ..., "novo": ...}``.
    """
    if not atualizacoes:
        return 0
    # bindparam com o mesmo nome de uma coluna de ``UPDATE`` é ambíguo para o SQLAlchemy
    # (``codigo``/``descricao`` existem nas duas tabelas) — por isso os nomes ``b_*``.
    params = [
        {"b_codigo": a["codigo"], "b_fallback": a["fallback"], "b_novo": a["novo"]}
        for a in atualizacoes
    ]
    # UPDATE Core puro (via ``.__table__``, não a entidade ORM): o alvo é um bulk
    # executemany por bindparam, sem objetos ORM na sessão para sincronizar — o
    # ``update(Model)`` ORM-enabled tenta interpretar isso como "bulk UPDATE por PK" e
    # exige a PK nos parâmetros, o que não é o que este backfill faz.
    r1 = session.execute(
        update(cast(Table, DimContaPcasp.__table__))
        .where(
            DimContaPcasp.codigo == bindparam("b_codigo"),
            DimContaPcasp.descricao == bindparam("b_fallback"),
        )
        .values(descricao=bindparam("b_novo")),
        params,
    )
    r2 = session.execute(
        update(cast(Table, MartMscRollup.__table__))
        .where(
            MartMscRollup.cod_conta == bindparam("b_codigo"),
            MartMscRollup.descricao == bindparam("b_fallback"),
        )
        .values(descricao=bindparam("b_novo")),
        params,
    )
    n1 = r1.rowcount if isinstance(r1.rowcount, int) else 0
    n2 = r2.rowcount if isinstance(r2.rowcount, int) else 0
    return n1 + n2


# --- gold.fato_msc_saldo (particionada por uf/ano) ---
def replace_msc_saldo(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str,
    rows: list[dict[str, Any]],
) -> None:
    session.execute(
        delete(FatoMscSaldo).where(
            FatoMscSaldo.cod_ibge == cod_ibge,
            FatoMscSaldo.periodo == periodo,
            FatoMscSaldo.versao_entrega == versao_entrega,
        )
    )
    if rows:
        session.execute(pg_insert(FatoMscSaldo), rows)


def msc_saldo_present(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    FatoMscSaldo.cod_ibge == cod_ibge,
                    FatoMscSaldo.periodo == periodo,
                    FatoMscSaldo.versao_entrega == versao_entrega,
                )
            )
        )
    )


# --- gold.mart_msc_rollup ---
def replace_rollup_month(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str,
    rows: list[dict[str, Any]],
) -> None:
    session.execute(
        delete(MartMscRollup).where(
            MartMscRollup.cod_ibge == cod_ibge,
            MartMscRollup.periodo == periodo,
            MartMscRollup.versao_entrega == versao_entrega,
        )
    )
    if rows:
        session.execute(pg_insert(MartMscRollup), rows)


def rollup_present(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    MartMscRollup.cod_ibge == cod_ibge,
                    MartMscRollup.periodo == periodo,
                    MartMscRollup.versao_entrega == versao_entrega,
                )
            )
        )
    )


def rollup_present_pairs(
    session: Session,
    *,
    cod_ibge: str,
    periodo_versao: list[tuple[str, str]],
) -> set[tuple[str, str]]:
    """Pares mês×versão já materializados, resolvidos em uma única consulta."""

    if not periodo_versao:
        return set()
    return {
        (str(periodo), str(versao))
        for periodo, versao in session.execute(
            select(MartMscRollup.periodo, MartMscRollup.versao_entrega)
            .where(
                MartMscRollup.cod_ibge == cod_ibge,
                tuple_(
                    MartMscRollup.periodo,
                    MartMscRollup.versao_entrega,
                ).in_(periodo_versao),
            )
            .distinct()
        )
    }


def rollup_children(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str,
    parent: str | None,
) -> list[MartMscRollup]:
    """Filhos diretos de ``parent`` (ou raízes se ``None``) num mês — drill *lazy*."""
    cond = (
        MartMscRollup.parent_conta.is_(None)
        if parent is None
        else MartMscRollup.parent_conta == parent
    )
    return list(
        session.scalars(
            select(MartMscRollup)
            .where(
                MartMscRollup.cod_ibge == cod_ibge,
                MartMscRollup.periodo == periodo,
                MartMscRollup.versao_entrega == versao_entrega,
                cond,
            )
            .order_by(MartMscRollup.cod_conta)
        )
    )


def rollup_node(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str, cod_conta: str
) -> MartMscRollup | None:
    return session.scalar(
        select(MartMscRollup).where(
            MartMscRollup.cod_ibge == cod_ibge,
            MartMscRollup.periodo == periodo,
            MartMscRollup.versao_entrega == versao_entrega,
            MartMscRollup.cod_conta == cod_conta,
        )
    )


def rollup_matriz(
    session: Session, *, cod_ibge: str, cod_conta: str,
    periodo_versao: list[tuple[str, str]],
) -> list[MartMscRollup]:
    """Linhas de rollup de uma conta nos meses resolvidos (cada mês na sua versão)."""
    if not periodo_versao:
        return []
    pares = or_(
        *[
            (MartMscRollup.periodo == p) & (MartMscRollup.versao_entrega == v)
            for p, v in periodo_versao
        ]
    )
    return list(
        session.scalars(
            select(MartMscRollup)
            .where(
                MartMscRollup.cod_ibge == cod_ibge,
                MartMscRollup.cod_conta == cod_conta,
                pares,
            )
            .order_by(MartMscRollup.mes)
        )
    )


# --- gold.fato_balanco ---
def replace_balanco_year(
    session: Session, *, cod_ibge: str, ano: int, versao_entrega: str,
    rows: list[dict[str, Any]],
) -> None:
    session.execute(
        delete(FatoBalanco).where(
            FatoBalanco.cod_ibge == cod_ibge,
            FatoBalanco.ano == ano,
            FatoBalanco.versao_entrega == versao_entrega,
        )
    )
    if rows:
        session.execute(pg_insert(FatoBalanco), rows)


def balanco_present(
    session: Session, *, cod_ibge: str, ano: int, versao_entrega: str
) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    FatoBalanco.cod_ibge == cod_ibge,
                    FatoBalanco.ano == ano,
                    FatoBalanco.versao_entrega == versao_entrega,
                )
            )
        )
    )


def read_balanco(
    session: Session, *, cod_ibge: str, ano: int, tipo: str, versao_entrega: str
) -> list[FatoBalanco]:
    return list(
        session.scalars(
            select(FatoBalanco)
            .where(
                FatoBalanco.cod_ibge == cod_ibge,
                FatoBalanco.ano == ano,
                FatoBalanco.tipo == tipo,
                FatoBalanco.versao_entrega == versao_entrega,
            )
            .order_by(FatoBalanco.cod_conta, FatoBalanco.coluna)
        )
    )


def balanco_children(
    session: Session, *, cod_ibge: str, ano: int, tipo: str, versao_entrega: str,
    parent: str | None,
) -> list[FatoBalanco]:
    cond = (
        FatoBalanco.parent_conta.is_(None)
        if parent is None
        else FatoBalanco.parent_conta == parent
    )
    return list(
        session.scalars(
            select(FatoBalanco)
            .where(
                FatoBalanco.cod_ibge == cod_ibge,
                FatoBalanco.ano == ano,
                FatoBalanco.tipo == tipo,
                FatoBalanco.versao_entrega == versao_entrega,
                cond,
            )
            .order_by(FatoBalanco.cod_conta)
        )
    )


def balanco_valor(
    session: Session, *, cod_ibge: str, ano: int, tipo: str, versao_entrega: str,
    cod_conta: str, coluna: str | None = None,
) -> Any:
    stmt = select(FatoBalanco.valor).where(
        FatoBalanco.cod_ibge == cod_ibge,
        FatoBalanco.ano == ano,
        FatoBalanco.tipo == tipo,
        FatoBalanco.versao_entrega == versao_entrega,
        FatoBalanco.cod_conta == cod_conta,
    )
    if coluna is not None:
        stmt = stmt.where(FatoBalanco.coluna == coluna)
    return session.scalar(stmt.limit(1))


def distinct_balanco_tipos(
    session: Session, *, cod_ibge: str, ano: int, versao_entrega: str
) -> list[str]:
    return list(
        session.scalars(
            select(FatoBalanco.tipo)
            .where(
                FatoBalanco.cod_ibge == cod_ibge,
                FatoBalanco.ano == ano,
                FatoBalanco.versao_entrega == versao_entrega,
            )
            .distinct()
        )
    )


__all__ = [
    "anos_com_dca",
    "anos_com_msc",
    "balanco_children",
    "balanco_present",
    "balanco_valor",
    "descricoes_por_codigo",
    "distinct_balanco_tipos",
    "distinct_msc_periodos",
    "list_contas",
    "msc_saldo_present",
    "read_balanco",
    "read_dca_year",
    "read_msc_month",
    "replace_balanco_year",
    "replace_msc_saldo",
    "replace_rollup_month",
    "rollup_children",
    "rollup_matriz",
    "rollup_node",
    "rollup_present",
    "rollup_present_pairs",
    "upsert_conta",
]
