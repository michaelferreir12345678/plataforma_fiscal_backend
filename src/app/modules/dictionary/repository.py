"""Acesso a dados do dicionário semântico (Sprint IA-2).

Duas responsabilidades: persistir os verbetes/campos/junções de forma idempotente e
**ler o esquema real** do banco (``information_schema``). A segunda é o que dá dente à
catraca: sem confrontar a descrição com as colunas que existem de fato, o dicionário
poderia descrever uma coluna removida há três sprints e continuar verde.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.dictionary.models import (
    DicionarioCampo,
    DicionarioIndicador,
    DicionarioJuncao,
)


# --------------------------------------------------------------------------- #
# Escrita idempotente (mesmo padrão do seed de linhagem da Sprint 26)
# --------------------------------------------------------------------------- #
def upsert_verbete(session: Session, valores: dict[str, object]) -> None:
    stmt = pg_insert(DicionarioIndicador).values(**valores)
    atualizaveis = {k: stmt.excluded[k] for k in valores if k != "codigo"}
    session.execute(
        stmt.on_conflict_do_update(index_elements=["codigo"], set_=atualizaveis)
    )


def upsert_campo(session: Session, valores: dict[str, object]) -> None:
    chave = ("schema_nome", "tabela", "coluna")
    stmt = pg_insert(DicionarioCampo).values(**valores)
    atualizaveis = {k: stmt.excluded[k] for k in valores if k not in chave}
    session.execute(
        stmt.on_conflict_do_update(index_elements=list(chave), set_=atualizaveis)
    )


def upsert_juncao(session: Session, valores: dict[str, object]) -> None:
    chave = ("origem_tabela", "destino_tabela")
    stmt = pg_insert(DicionarioJuncao).values(**valores)
    atualizaveis = {k: stmt.excluded[k] for k in valores if k not in chave}
    session.execute(
        stmt.on_conflict_do_update(index_elements=list(chave), set_=atualizaveis)
    )


# --------------------------------------------------------------------------- #
# Leitura
# --------------------------------------------------------------------------- #
def listar_verbetes(session: Session) -> Sequence[DicionarioIndicador]:
    return list(
        session.scalars(select(DicionarioIndicador).order_by(DicionarioIndicador.codigo))
    )


def get_verbete(session: Session, codigo: str) -> DicionarioIndicador | None:
    return session.get(DicionarioIndicador, codigo)


def listar_campos(session: Session) -> Sequence[DicionarioCampo]:
    return list(
        session.scalars(
            select(DicionarioCampo).order_by(
                DicionarioCampo.schema_nome, DicionarioCampo.tabela, DicionarioCampo.coluna
            )
        )
    )


def listar_juncoes(session: Session) -> Sequence[DicionarioJuncao]:
    return list(
        session.scalars(
            select(DicionarioJuncao).order_by(
                DicionarioJuncao.origem_tabela, DicionarioJuncao.destino_tabela
            )
        )
    )


def contar_verbetes(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(DicionarioIndicador)) or 0)


def contar_campos(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(DicionarioCampo)) or 0)


def contar_juncoes(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(DicionarioJuncao)) or 0)


# --------------------------------------------------------------------------- #
# O esquema real — a régua contra a qual a catraca mede
# --------------------------------------------------------------------------- #
def colunas_reais(session: Session, tabelas: Sequence[str]) -> dict[str, set[str]]:
    """Colunas existentes de cada ``schema.tabela`` pedida, segundo o ``information_schema``.

    Tabela inexistente devolve conjunto vazio (e não ausência da chave): a catraca precisa
    distinguir "não descrevi nenhuma coluna" de "a tabela sumiu", e as duas são falha.
    """
    resultado: dict[str, set[str]] = {t: set() for t in tabelas}
    if not tabelas:
        return resultado
    pares = [tuple(t.split(".", 1)) for t in tabelas]
    linhas = session.execute(
        text(
            """
            SELECT table_schema, table_name, column_name
            FROM information_schema.columns
            WHERE (table_schema, table_name) IN (
                SELECT * FROM unnest(CAST(:schemas AS text[]), CAST(:tabelas AS text[]))
            )
            """
        ).bindparams(
            schemas=[p[0] for p in pares],
            tabelas=[p[1] for p in pares],
        )
    ).all()
    for schema_nome, tabela, coluna in linhas:
        resultado[f"{schema_nome}.{tabela}"].add(str(coluna))
    return resultado


def indicadores_no_mart(session: Session) -> set[str]:
    """Códigos distintos efetivamente materializados em ``gold.mart_indicador``."""
    linhas = session.execute(
        text("SELECT DISTINCT indicador FROM gold.mart_indicador")
    ).scalars()
    return {str(codigo) for codigo in linhas}


def denominadores_no_mart(session: Session) -> dict[str, set[str]]:
    """``indicador → denominadores`` observados no mart (a realidade, não a intenção)."""
    linhas = session.execute(
        text("SELECT DISTINCT indicador, denominador FROM gold.mart_indicador")
    ).all()
    saida: dict[str, set[str]] = {}
    for indicador, denominador in linhas:
        saida.setdefault(str(indicador), set()).add(str(denominador))
    return saida
