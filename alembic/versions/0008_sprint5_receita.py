"""Sprint 5 — gold: dim_origem_receita (hierarquia ltree) e fato_receita.

Revision ID: 0008_sprint5_receita
Revises: 0007_sprint4_carteira
Create Date: 2026-07-21
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_sprint5_receita"
down_revision: str | None = "0007_sprint4_carteira"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")


def upgrade() -> None:
    # --- dim_origem_receita (hierárquica: Categoria→Origem→Espécie→Rubrica→Alínea) ---
    op.execute(
        """
        CREATE TABLE gold.dim_origem_receita (
            codigo text PRIMARY KEY,
            descricao text NOT NULL,
            parent_codigo text REFERENCES gold.dim_origem_receita(codigo),
            nivel integer NOT NULL,
            path ltree NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_dim_origem_receita_path ON gold.dim_origem_receita USING gist (path)"
    )
    op.execute(
        "CREATE INDEX ix_dim_origem_receita_parent ON gold.dim_origem_receita (parent_codigo)"
    )

    # --- fato_receita (RREO Anexo 01, por natureza da receita, versionado) ---
    op.create_table(
        "fato_receita",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column(
            "origem_codigo",
            sa.Text(),
            sa.ForeignKey("gold.dim_origem_receita.codigo"),
            nullable=False,
        ),
        sa.Column("previsto_inicial", sa.Numeric(), nullable=True),
        sa.Column("previsto_atualizado", sa.Numeric(), nullable=True),
        sa.Column("arrecadado_bimestre", sa.Numeric(), nullable=True),
        sa.Column("arrecadado_acum", sa.Numeric(), nullable=True),
        sa.Column("deducoes", sa.Numeric(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge", "periodo", "origem_codigo", "versao_entrega", name="uq_fato_receita_chave"
        ),
        schema="gold",
    )
    op.create_index(
        "ix_fato_receita_ente_periodo", "fato_receita", ["cod_ibge", "periodo"], schema="gold"
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gold TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_fato_receita_ente_periodo", table_name="fato_receita", schema="gold")
    op.drop_table("fato_receita", schema="gold")
    op.execute("DROP TABLE IF EXISTS gold.dim_origem_receita CASCADE")
