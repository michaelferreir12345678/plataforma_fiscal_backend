"""Acesso a dados do Resultado Fiscal: gold (fato + ajustes) e leitura do silver RREO Anexo 6."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.ingestion.models import SilverRreo
from app.modules.result.models import FatoAjusteMetodologico, FatoResultado, MetaFiscal
from app.modules.result.resultado import MEDIDAS

_ANEXO_MARCA = "06"


# --- fato_resultado ---
def upsert_fato(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(FatoResultado).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "periodo", "versao_entrega"],
        set_={m: valores.get(m) for m in MEDIDAS},
    )
    session.execute(stmt)


def get_fato(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> FatoResultado | None:
    return session.scalar(
        select(FatoResultado).where(
            FatoResultado.cod_ibge == cod_ibge,
            FatoResultado.periodo == periodo,
            FatoResultado.versao_entrega == versao_entrega,
        )
    )


def distinct_periodos_fato(session: Session, *, cod_ibge: str) -> list[str]:
    return list(
        session.scalars(
            select(FatoResultado.periodo)
            .where(FatoResultado.cod_ibge == cod_ibge)
            .distinct()
            .order_by(FatoResultado.periodo)
        )
    )


# --- fato_ajuste_metodologico ---
def replace_ajustes(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str,
    ajustes: list[dict[str, Any]],
) -> None:
    session.execute(
        delete(FatoAjusteMetodologico).where(
            FatoAjusteMetodologico.cod_ibge == cod_ibge,
            FatoAjusteMetodologico.periodo == periodo,
            FatoAjusteMetodologico.versao_entrega == versao_entrega,
        )
    )
    for a in ajustes:
        session.execute(pg_insert(FatoAjusteMetodologico).values(**a))


def list_ajustes(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[FatoAjusteMetodologico]:
    return list(
        session.scalars(
            select(FatoAjusteMetodologico).where(
                FatoAjusteMetodologico.cod_ibge == cod_ibge,
                FatoAjusteMetodologico.periodo == periodo,
                FatoAjusteMetodologico.versao_entrega == versao_entrega,
            )
        )
    )


# --- silver: RREO Anexo 6 ---
def read_anexo6(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[SilverRreo]:
    return list(
        session.scalars(
            select(SilverRreo)
            .where(
                SilverRreo.cod_ibge == cod_ibge,
                SilverRreo.periodo == periodo,
                SilverRreo.versao_entrega == versao_entrega,
                SilverRreo.anexo.ilike(f"%{_ANEXO_MARCA}%"),
            )
            .order_by(SilverRreo.linha_seq)
        )
    )


# --- op.meta_fiscal (meta da LDO declarada pela organização — Sprint 25B) ---
def list_metas_fiscais(
    session: Session, *, org_id: uuid.UUID, cod_ibge: str, exercicio: int | None = None
) -> list[MetaFiscal]:
    stmt = select(MetaFiscal).where(
        MetaFiscal.org_id == org_id, MetaFiscal.cod_ibge == cod_ibge
    )
    if exercicio is not None:
        stmt = stmt.where(MetaFiscal.exercicio == exercicio)
    return list(session.scalars(stmt.order_by(MetaFiscal.exercicio.desc(), MetaFiscal.indicador)))


def get_meta_fiscal(
    session: Session, *, org_id: uuid.UUID, cod_ibge: str, exercicio: int, indicador: str
) -> MetaFiscal | None:
    return session.scalar(
        select(MetaFiscal).where(
            MetaFiscal.org_id == org_id,
            MetaFiscal.cod_ibge == cod_ibge,
            MetaFiscal.exercicio == exercicio,
            MetaFiscal.indicador == indicador,
        )
    )


def upsert_meta_fiscal(session: Session, valores: dict[str, Any]) -> MetaFiscal:
    """Grava a meta do exercício/indicador (uma por chave), preservando quem criou."""
    atual = get_meta_fiscal(
        session,
        org_id=valores["org_id"],
        cod_ibge=valores["cod_ibge"],
        exercicio=valores["exercicio"],
        indicador=valores["indicador"],
    )
    if atual is None:
        atual = MetaFiscal(**valores, criado_por=valores.get("atualizado_por"))
        session.add(atual)
    else:
        atual.valor = valores["valor"]
        atual.fonte_declarada = valores["fonte_declarada"]
        atual.observacao = valores.get("observacao")
        atual.atualizado_por = valores.get("atualizado_por")
        atual.atualizado_em = datetime.now(UTC)
    session.flush()
    return atual


def delete_meta_fiscal(session: Session, *, org_id: uuid.UUID, meta_id: uuid.UUID) -> bool:
    row = session.scalar(
        select(MetaFiscal).where(MetaFiscal.id == meta_id, MetaFiscal.org_id == org_id)
    )
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True
