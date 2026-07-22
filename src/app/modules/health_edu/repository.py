"""Persistência/materialização dos mínimos e leitura das fontes silver."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.expense.models import DimFuncao
from app.modules.health_edu.models import FatoEducacao, FatoSaude, FatoSaudeSubfuncao
from app.modules.ingestion.models import (
    FndeFundebRepasse,
    SilverRreo,
    SiopeEducacao,
    SiopsSaude,
)


def read_rreo_anexo(
    session: Session,
    *,
    cod_ibge: str,
    periodo: str,
    versao_entrega: str,
    numero: str,
) -> list[SilverRreo]:
    return list(
        session.scalars(
            select(SilverRreo)
            .where(
                SilverRreo.cod_ibge == cod_ibge,
                SilverRreo.periodo == periodo,
                SilverRreo.versao_entrega == versao_entrega,
                SilverRreo.anexo.ilike(f"%{numero}%"),
            )
            .order_by(SilverRreo.linha_seq, SilverRreo.cod_conta, SilverRreo.coluna)
        )
    )


def distinct_periodos_anexo(
    session: Session, *, cod_ibge: str, numero: str
) -> list[str]:
    return list(
        session.scalars(
            select(SilverRreo.periodo)
            .where(SilverRreo.cod_ibge == cod_ibge, SilverRreo.anexo.ilike(f"%{numero}%"))
            .distinct()
            .order_by(SilverRreo.periodo)
        )
    )


def list_funcoes(session: Session, codigos: Iterable[str] | None = None) -> list[DimFuncao]:
    stmt = select(DimFuncao)
    if codigos is not None:
        cods = list(codigos)
        if not cods:
            return []
        stmt = stmt.where(DimFuncao.codigo.in_(cods))
    return list(session.scalars(stmt.order_by(DimFuncao.codigo)))


def upsert_fato_saude(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(FatoSaude).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "periodo", "versao_rreo", "versao_rgf"],
        set_={
            key: valores[key]
            for key in (
                "base_impostos_transferencias", "despesa_bruta", "deducoes_outras",
                "rpnp_sem_lastro", "despesa_aplicada", "pct_aplicado", "minimo_pct",
                "valor_minimo", "abaixo_do_minimo",
            )
        },
    )
    session.execute(stmt)


def get_fato_saude(
    session: Session, *, cod_ibge: str, periodo: str, versao_rreo: str, versao_rgf: str
) -> FatoSaude | None:
    return session.scalar(
        select(FatoSaude).where(
            FatoSaude.cod_ibge == cod_ibge,
            FatoSaude.periodo == periodo,
            FatoSaude.versao_rreo == versao_rreo,
            FatoSaude.versao_rgf == versao_rgf,
        )
    )


def replace_saude_subfuncoes(
    session: Session,
    *,
    cod_ibge: str,
    periodo: str,
    versao_rreo: str,
    rows: list[dict[str, Any]],
) -> None:
    session.execute(
        delete(FatoSaudeSubfuncao).where(
            FatoSaudeSubfuncao.cod_ibge == cod_ibge,
            FatoSaudeSubfuncao.periodo == periodo,
            FatoSaudeSubfuncao.versao_rreo == versao_rreo,
        )
    )
    if rows:
        session.execute(insert(FatoSaudeSubfuncao), [{"id": uuid.uuid4(), **row} for row in rows])


def list_saude_subfuncoes(
    session: Session, *, cod_ibge: str, periodo: str, versao_rreo: str
) -> list[FatoSaudeSubfuncao]:
    return list(
        session.scalars(
            select(FatoSaudeSubfuncao).where(
                FatoSaudeSubfuncao.cod_ibge == cod_ibge,
                FatoSaudeSubfuncao.periodo == periodo,
                FatoSaudeSubfuncao.versao_rreo == versao_rreo,
            )
        )
    )


def upsert_fato_educacao(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(FatoEducacao).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "periodo", "versao_rreo", "versao_rgf"],
        set_={
            key: valores[key]
            for key in (
                "base_impostos_transferencias", "despesa_bruta", "despesa_impostos",
                "despesa_fundeb", "deducoes_outras", "rpnp_sem_lastro",
                "despesa_aplicada", "pct_aplicado", "minimo_pct", "valor_minimo",
                "abaixo_do_minimo", "fundeb_base_profissionais",
                "fundeb_aplicado_profissionais", "fundeb_pct_profissionais",
                "fundeb_minimo_pct", "fundeb_valor_minimo", "fundeb_abaixo_do_minimo",
            )
        },
    )
    session.execute(stmt)


def get_fato_educacao(
    session: Session, *, cod_ibge: str, periodo: str, versao_rreo: str, versao_rgf: str
) -> FatoEducacao | None:
    return session.scalar(
        select(FatoEducacao).where(
            FatoEducacao.cod_ibge == cod_ibge,
            FatoEducacao.periodo == periodo,
            FatoEducacao.versao_rreo == versao_rreo,
            FatoEducacao.versao_rgf == versao_rgf,
        )
    )


def read_siops(
    session: Session, *, cod_ibge: str, ano: int, bimestre_max: int, versao_entrega: str
) -> list[SiopsSaude]:
    return list(
        session.scalars(
            select(SiopsSaude)
            .where(
                SiopsSaude.cod_ibge == cod_ibge,
                SiopsSaude.ano == ano,
                SiopsSaude.bimestre <= bimestre_max,
                SiopsSaude.versao_entrega == versao_entrega,
            )
            .order_by(SiopsSaude.bimestre.desc(), SiopsSaude.indicador_codigo)
        )
    )


def read_fundeb(
    session: Session, *, cod_ibge: str, ano: int, mes_max: int, versao_entrega: str
) -> list[FndeFundebRepasse]:
    return list(
        session.scalars(
            select(FndeFundebRepasse)
            .where(
                FndeFundebRepasse.cod_ibge == cod_ibge,
                FndeFundebRepasse.ano == ano,
                FndeFundebRepasse.mes <= mes_max,
                FndeFundebRepasse.versao_entrega == versao_entrega,
            )
            .order_by(FndeFundebRepasse.mes.desc())
        )
    )


def read_siope(
    session: Session, *, cod_ibge: str, ano: int, bimestre_max: int, versao_entrega: str
) -> list[SiopeEducacao]:
    return list(
        session.scalars(
            select(SiopeEducacao)
            .where(
                SiopeEducacao.cod_ibge == cod_ibge,
                SiopeEducacao.ano == ano,
                SiopeEducacao.bimestre <= bimestre_max,
                SiopeEducacao.versao_entrega == versao_entrega,
            )
            .order_by(SiopeEducacao.bimestre.desc(), SiopeEducacao.indicador_codigo)
        )
    )
