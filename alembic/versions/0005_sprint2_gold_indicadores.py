"""Sprint 2 — gold: dimensões conformadas, dim_limite_legal, fato_rcl, mart_indicador.

Revision ID: 0005_sprint2_gold
Revises: 0004_sprint1b_arquivos
Create Date: 2026-07-04
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_sprint2_gold"
down_revision: str | None = "0004_sprint1b_arquivos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")


def upgrade() -> None:
    # --- dim_ente (conformada; populada por SICONFI + IBGE) ---
    op.create_table(
        "dim_ente",
        sa.Column("cod_ibge", sa.String(length=7), primary_key=True),
        sa.Column("nome", sa.Text(), nullable=True),
        sa.Column("esfera", sa.String(length=10), nullable=True),  # municipal | estadual
        sa.Column("populacao", sa.BigInteger(), nullable=True),
        sa.Column("rpps", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("possui_tcm", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("uf", sa.String(length=2), nullable=True),
        sa.Column("regiao", sa.Text(), nullable=True),
        sa.Column("pib", sa.Numeric(), nullable=True),
        sa.Column("pop_ano_ref", sa.Integer(), nullable=True),
        sa.Column("pib_ano_ref", sa.Integer(), nullable=True),
        schema="gold",
    )

    # --- dim_periodo (hierárquica ltree: ano → bim/quad → mês) ---
    op.execute(
        """
        CREATE TABLE gold.dim_periodo (
            codigo text PRIMARY KEY,
            descricao text NOT NULL,
            parent_codigo text REFERENCES gold.dim_periodo(codigo),
            nivel integer NOT NULL,
            path ltree NOT NULL,
            ano integer NOT NULL,
            mes integer,
            bimestre integer,
            quadrimestre integer
        )
        """
    )
    op.execute("CREATE INDEX ix_dim_periodo_path ON gold.dim_periodo USING gist (path)")
    op.execute("CREATE INDEX ix_dim_periodo_parent ON gold.dim_periodo (parent_codigo)")

    # --- dim_limite_legal (DADO, não código) ---
    op.create_table(
        "dim_limite_legal",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("indicador", sa.Text(), nullable=False),
        sa.Column("esfera", sa.String(length=10), nullable=False),
        sa.Column("poder", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("sentido", sa.String(length=5), nullable=False, server_default=sa.text("'teto'")),
        sa.Column("teto_pct", sa.Numeric(), nullable=False),
        sa.Column("alerta_pct", sa.Numeric(), nullable=True),
        sa.Column("prudencial_pct", sa.Numeric(), nullable=True),
        sa.UniqueConstraint("indicador", "esfera", "poder", name="uq_dim_limite_legal_chave"),
        schema="gold",
    )

    # --- fato_rcl (RCL 12 meses móveis, versionada) ---
    op.create_table(
        "fato_rcl",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo_ref", sa.Text(), nullable=False),
        sa.Column("rcl_12m", sa.Numeric(), nullable=False),
        sa.Column("deducoes", sa.Numeric(), nullable=True),
        sa.Column("receita_corrente", sa.Numeric(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.Column("memoria", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint(
            "cod_ibge", "periodo_ref", "versao_entrega", name="uq_fato_rcl_chave"
        ),
        schema="gold",
    )
    op.create_index("ix_fato_rcl_ente_periodo", "fato_rcl", ["cod_ibge", "periodo_ref"], schema="gold")

    # --- mart_indicador (fato × dim_limite_legal → faixa) ---
    op.create_table(
        "mart_indicador",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("indicador", sa.Text(), nullable=False),
        sa.Column("valor_rs", sa.Numeric(), nullable=True),
        sa.Column("valor_pct_rcl", sa.Numeric(), nullable=True),
        sa.Column("faixa", sa.Text(), nullable=True),
        sa.Column("teto_pct", sa.Numeric(), nullable=True),
        sa.Column("source_ref", postgresql.JSONB(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge", "periodo", "indicador", "versao_entrega", name="uq_mart_indicador_chave"
        ),
        schema="gold",
    )
    op.create_index(
        "ix_mart_indicador_ente_periodo", "mart_indicador", ["cod_ibge", "periodo"], schema="gold"
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gold TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_mart_indicador_ente_periodo", table_name="mart_indicador", schema="gold")
    op.drop_table("mart_indicador", schema="gold")
    op.drop_index("ix_fato_rcl_ente_periodo", table_name="fato_rcl", schema="gold")
    op.drop_table("fato_rcl", schema="gold")
    op.drop_table("dim_limite_legal", schema="gold")
    op.execute("DROP TABLE IF EXISTS gold.dim_periodo CASCADE")
    op.drop_table("dim_ente", schema="gold")
