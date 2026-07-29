"""Acesso a dados dos indicadores (fato_rcl, mart_indicador) e leitura do silver RREO."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.catalog.models import DimEnte
from app.modules.indicators.models import FatoRcl, MartIndicador
from app.modules.ingestion import repository as ingestion_repo
from app.modules.ingestion.models import BcbIndice, IbgePopulacao, SilverRreo

RELATORIO_RREO = "RREO"
SERIE_IPCA = 433  # SGS/BCB — variação mensal do IPCA (%)


def resolve_versao_rreo(
    session: Session, *, cod_ibge: str, periodo: str, as_of: datetime | None = None
) -> str | None:
    """Versão vigente (ou 'as of') do RREO para o ente/período (§6.5)."""
    return ingestion_repo.resolve_versao(
        session, cod_ibge=cod_ibge, relatorio=RELATORIO_RREO, periodo=periodo, as_of=as_of
    )


def read_anexo03(
    session: Session, *, cod_ibge: str, periodo: str, versao_entrega: str
) -> list[SilverRreo]:
    return list(
        session.scalars(
            select(SilverRreo).where(
                SilverRreo.cod_ibge == cod_ibge,
                SilverRreo.periodo == periodo,
                SilverRreo.versao_entrega == versao_entrega,
                SilverRreo.anexo.ilike("%03%"),
            )
        )
    )


def upsert_fato_rcl(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(FatoRcl).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "periodo_ref", "versao_entrega"],
        set_={k: valores[k] for k in ("rcl_12m", "deducoes", "receita_corrente", "memoria")},
    )
    session.execute(stmt)


def get_fato_rcl(
    session: Session, *, cod_ibge: str, periodo_ref: str, versao_entrega: str
) -> FatoRcl | None:
    return session.scalar(
        select(FatoRcl).where(
            FatoRcl.cod_ibge == cod_ibge,
            FatoRcl.periodo_ref == periodo_ref,
            FatoRcl.versao_entrega == versao_entrega,
        )
    )


def upsert_mart_indicador(session: Session, valores: dict[str, Any]) -> None:
    stmt = pg_insert(MartIndicador).values(**valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cod_ibge", "periodo", "indicador", "versao_entrega"],
        set_={
            k: valores[k]
            for k in (
                "valor_rs", "valor_pct_rcl", "faixa", "teto_pct", "source_ref",
                # Sem estes dois no UPDATE, um recálculo manteria a base antiga e o
                # percentual novo — a pior combinação possível numa linha auditável.
                "denominador", "base_valor",
            )
            if k in valores
        },
    )
    session.execute(stmt)


def get_mart_indicador(
    session: Session, *, cod_ibge: str, periodo: str, indicador: str, versao_entrega: str
) -> MartIndicador | None:
    return session.scalar(
        select(MartIndicador).where(
            MartIndicador.cod_ibge == cod_ibge,
            MartIndicador.periodo == periodo,
            MartIndicador.indicador == indicador,
            MartIndicador.versao_entrega == versao_entrega,
        )
    )


def _num(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


# --- insumos de ajuste de série (deflação IPCA + per capita) ---
def ipca_mensal(session: Session) -> dict[tuple[int, int], Decimal]:
    """Variação mensal do IPCA (%) por (ano, mês), na versão vigente de cada data.

    Fonte: ``silver.bcb_indice`` série 433 (SGS/BCB). Meses sem dado simplesmente não
    aparecem — quem defla precisa tratar a lacuna, nunca assumir inflação zero.
    """
    rows = session.execute(
        select(BcbIndice.data_ref, BcbIndice.valor, BcbIndice.versao_entrega)
        .where(BcbIndice.codigo_serie == SERIE_IPCA, BcbIndice.valor.is_not(None))
        .order_by(BcbIndice.data_ref, BcbIndice.versao_entrega)
    ).all()
    # Ordenado por versão: a última leitura de cada data é a vigente.
    return {
        (data.year, data.month): Decimal(str(valor)) for data, valor, _versao in rows
    }


def populacao_por_ano(session: Session, *, cod_ibge: str) -> dict[int, int]:
    """População por ano de referência (``silver.ibge_populacao``, versão vigente)."""
    rows = session.execute(
        select(IbgePopulacao.ano_ref, IbgePopulacao.populacao)
        .where(
            IbgePopulacao.cod_ibge == cod_ibge,
            IbgePopulacao.populacao.is_not(None),
        )
        .order_by(IbgePopulacao.ano_ref, IbgePopulacao.versao_entrega)
    ).all()
    return {int(ano): int(pop) for ano, pop in rows}


def populacao_dim_ente(session: Session, *, cod_ibge: str) -> tuple[int, int | None] | None:
    """População conformada da ``gold.dim_ente`` (fallback) com o ano de referência."""
    row = session.execute(
        select(DimEnte.populacao, DimEnte.pop_ano_ref).where(DimEnte.cod_ibge == cod_ibge)
    ).first()
    if row is None or row[0] is None:
        return None
    return int(row[0]), int(row[1]) if row[1] is not None else None
