"""Sprint 3 — dashboard & limites: gold.dim_providencia_legal (providências por faixa).

Revision ID: 0006_sprint3_providencias
Revises: 0005_sprint2_gold
Create Date: 2026-07-07
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_sprint3_providencias"
down_revision: str | None = "0005_sprint2_gold"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")


def upgrade() -> None:
    op.create_table(
        "dim_providencia_legal",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("indicador", sa.Text(), nullable=False),
        sa.Column("faixa", sa.Text(), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("base_legal", sa.Text(), nullable=True),
        sa.UniqueConstraint("indicador", "faixa", "texto", name="uq_dim_providencia_chave"),
        schema="gold",
    )
    op.create_index(
        "ix_dim_providencia_ind_faixa", "dim_providencia_legal", ["indicador", "faixa"],
        schema="gold",
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gold TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_dim_providencia_ind_faixa", table_name="dim_providencia_legal", schema="gold")
    op.drop_table("dim_providencia_legal", schema="gold")
