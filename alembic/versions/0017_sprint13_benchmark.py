"""Sprint 13 — Benchmarking (Módulo 12).

Revision ID: 0017_sprint13_benchmark
Revises: 0016_sprint12_accounting
Create Date: 2026-07-22

As coortes são dimensões explícitas e editáveis. ``mart_benchmark`` preserva o
snapshot e a proveniência usados no cálculo para que retificações posteriores não
apaguem a posição que era reproduzível em um determinado ``as_of``.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_sprint13_benchmark"
down_revision: str | None = "0016_sprint12_accounting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")
_NAMESPACE = uuid.UUID("8760a808-391f-54cb-ae20-4b5df08eeb85")


def _id(codigo: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, codigo)


def _source(criterio: str) -> dict[str, str]:
    return {
        "relatorio": "CONFIGURACAO-COORTE",
        "anexo": criterio,
        "versao_entrega": revision,
    }


def upgrade() -> None:
    op.create_table(
        "dim_coorte",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("codigo", sa.Text(), nullable=False),
        sa.Column("criterio", sa.String(length=20), nullable=False),
        sa.Column("faixa", sa.Text(), nullable=False),
        sa.Column("rotulo", sa.Text(), nullable=False),
        sa.Column("unidade_criterio", sa.String(length=20), nullable=True),
        sa.Column("limite_inferior", sa.Numeric(), nullable=True),
        sa.Column("limite_superior", sa.Numeric(), nullable=True),
        sa.Column(
            "inclusivo_superior", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source_ref", postgresql.JSONB(), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "criterio IN ('porte', 'regiao', 'pib')", name="ck_dim_coorte_criterio"
        ),
        sa.CheckConstraint(
            "limite_superior IS NULL OR limite_inferior IS NULL "
            "OR limite_superior >= limite_inferior",
            name="ck_dim_coorte_limites",
        ),
        sa.UniqueConstraint("codigo", name="uq_dim_coorte_codigo"),
        sa.UniqueConstraint("criterio", "faixa", name="uq_dim_coorte_criterio_faixa"),
        schema="gold",
    )
    op.create_index(
        "ix_dim_coorte_criterio_ativo",
        "dim_coorte",
        ["criterio", "ativo", "ordem"],
        schema="gold",
    )

    coorte = sa.table(
        "dim_coorte",
        sa.column("id", sa.Uuid()),
        sa.column("codigo", sa.Text()),
        sa.column("criterio", sa.String()),
        sa.column("faixa", sa.Text()),
        sa.column("rotulo", sa.Text()),
        sa.column("unidade_criterio", sa.String()),
        sa.column("limite_inferior", sa.Numeric()),
        sa.column("limite_superior", sa.Numeric()),
        sa.column("inclusivo_superior", sa.Boolean()),
        sa.column("ordem", sa.Integer()),
        sa.column("ativo", sa.Boolean()),
        sa.column("source_ref", postgresql.JSONB()),
        schema="gold",
    )
    rows: list[dict[str, object]] = []

    def add(
        codigo: str,
        criterio: str,
        faixa: str,
        rotulo: str,
        ordem: int,
        inferior: int | None = None,
        superior: int | None = None,
        unidade_criterio: str | None = None,
    ) -> None:
        rows.append(
            {
                "id": _id(codigo),
                "codigo": codigo,
                "criterio": criterio,
                "faixa": faixa,
                "rotulo": rotulo,
                "unidade_criterio": unidade_criterio,
                "limite_inferior": inferior,
                "limite_superior": superior,
                "inclusivo_superior": False,
                "ordem": ordem,
                "ativo": True,
                "source_ref": _source(criterio),
            }
        )

    add(
        "porte:pequeno",
        "porte",
        "pequeno",
        "Até 50 mil habitantes",
        10,
        0,
        50_000,
        "habitantes",
    )
    add(
        "porte:medio",
        "porte",
        "medio",
        "50 mil a 200 mil habitantes",
        20,
        50_000,
        200_000,
        "habitantes",
    )
    add(
        "porte:grande",
        "porte",
        "grande",
        "200 mil a 1 milhão de habitantes",
        30,
        200_000,
        1_000_000,
        "habitantes",
    )
    add(
        "porte:metropole",
        "porte",
        "metropole",
        "1 milhão de habitantes ou mais",
        40,
        1_000_000,
        unidade_criterio="habitantes",
    )

    for ordem, (faixa, rotulo) in enumerate(
        (
            ("NO", "Região Norte"),
            ("NE", "Região Nordeste"),
            ("CO", "Região Centro-Oeste"),
            ("SE", "Região Sudeste"),
            ("SU", "Região Sul"),
        ),
        start=10,
    ):
        add(f"regiao:{faixa}", "regiao", faixa, rotulo, ordem)

    # A variável 37 do agregado IBGE 5938 é publicada em **mil reais**. Os limites
    # abaixo, portanto, também estão em mil R$ (1 bilhão de R$ = 1.000.000 mil R$).
    # São parâmetros editáveis da coorte; nenhum valor fiscal é semeado aqui.
    add(
        "pib:ate_1bi",
        "pib",
        "ate_1bi",
        "PIB até R$ 1 bilhão",
        10,
        0,
        1_000_000,
        "mil_brl",
    )
    add(
        "pib:1a5bi",
        "pib",
        "1a5bi",
        "PIB de R$ 1 a 5 bilhões",
        20,
        1_000_000,
        5_000_000,
        "mil_brl",
    )
    add(
        "pib:5a20bi",
        "pib",
        "5a20bi",
        "PIB de R$ 5 a 20 bilhões",
        30,
        5_000_000,
        20_000_000,
        "mil_brl",
    )
    add(
        "pib:20a50bi",
        "pib",
        "20a50bi",
        "PIB de R$ 20 a 50 bilhões",
        40,
        20_000_000,
        50_000_000,
        "mil_brl",
    )
    add(
        "pib:50a100bi",
        "pib",
        "50a100bi",
        "PIB de R$ 50 a 100 bilhões",
        50,
        50_000_000,
        100_000_000,
        "mil_brl",
    )
    add(
        "pib:acima_100bi",
        "pib",
        "acima_100bi",
        "PIB de R$ 100 bilhões ou mais",
        60,
        100_000_000,
        unidade_criterio="mil_brl",
    )
    op.bulk_insert(coorte, rows)

    op.create_table(
        "mart_benchmark",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "coorte_id",
            sa.Uuid(),
            sa.ForeignKey("gold.dim_coorte.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("indicador", sa.Text(), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("valor", sa.Numeric(), nullable=False),
        sa.Column("percentil", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column("posicao", sa.Integer(), nullable=False),
        sa.Column("faixa", sa.Text(), nullable=True),
        sa.Column("unidade", sa.String(length=20), nullable=False),
        sa.Column("sentido", sa.String(length=20), nullable=False),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ref", postgresql.JSONB(), nullable=False),
        sa.Column("memoria", postgresql.JSONB(), nullable=True),
        sa.Column(
            "calculado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "percentil >= 0 AND percentil <= 100", name="ck_mart_benchmark_percentil"
        ),
        sa.CheckConstraint("posicao >= 1", name="ck_mart_benchmark_posicao"),
        sa.UniqueConstraint(
            "snapshot_hash",
            "coorte_id",
            "indicador",
            "periodo",
            "cod_ibge",
            name="uq_mart_benchmark_snapshot_ente",
        ),
        schema="gold",
    )
    op.create_index(
        "ix_mart_benchmark_consulta",
        "mart_benchmark",
        ["coorte_id", "indicador", "periodo", "snapshot_hash", "posicao"],
        schema="gold",
    )
    op.create_index(
        "ix_mart_benchmark_ente",
        "mart_benchmark",
        ["cod_ibge", "indicador", "periodo", "as_of"],
        schema="gold",
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gold TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_mart_benchmark_ente", table_name="mart_benchmark", schema="gold")
    op.drop_index("ix_mart_benchmark_consulta", table_name="mart_benchmark", schema="gold")
    op.drop_table("mart_benchmark", schema="gold")
    op.drop_index("ix_dim_coorte_criterio_ativo", table_name="dim_coorte", schema="gold")
    op.drop_table("dim_coorte", schema="gold")
