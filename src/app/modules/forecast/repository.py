"""Acesso a dados da Sprint 14: gold.fato_projecao (upsert) e op.cenario (CRUD)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.forecast.models import Cenario, FatoProjecao

_PROJECAO_UPDATE = (
    "horizonte",
    "valor_previsto",
    "ic_inferior",
    "ic_superior",
    "nivel_confianca",
    "unidade",
    "teto_pct",
    "faixa",
    "cruza_limite",
    "gerado_em",
    "source_ref",
    "memoria",
)


def upsert_projecao(session: Session, valores: dict[str, Any]) -> None:
    """Materializa uma projeção (idempotente por cod_ibge/indicador/modelo/periodo_alvo)."""
    stmt = pg_insert(FatoProjecao).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "indicador", "modelo", "periodo_alvo"],
        set_={k: valores[k] for k in _PROJECAO_UPDATE if k in valores},
    )
    session.execute(stmt)


def list_projecoes(
    session: Session, *, cod_ibge: str, indicador: str, modelo: str
) -> list[FatoProjecao]:
    return list(
        session.scalars(
            select(FatoProjecao).where(
                FatoProjecao.cod_ibge == cod_ibge,
                FatoProjecao.indicador == indicador,
                FatoProjecao.modelo == modelo,
            )
        )
    )


# --- op.cenario ---
def criar_cenario(session: Session, valores: dict[str, Any]) -> Cenario:
    cenario = Cenario(**valores)
    session.add(cenario)
    session.flush()
    return cenario


def get_cenario(session: Session, *, org_id: uuid.UUID, cenario_id: uuid.UUID) -> Cenario | None:
    return session.scalar(
        select(Cenario).where(Cenario.id == cenario_id, Cenario.org_id == org_id)
    )


def list_cenarios(
    session: Session, *, org_id: uuid.UUID, ente: str | None = None
) -> list[Cenario]:
    stmt = select(Cenario).where(Cenario.org_id == org_id)
    if ente is not None:
        stmt = stmt.where(Cenario.ente == ente)
    return list(session.scalars(stmt.order_by(Cenario.criado_em.desc())))
