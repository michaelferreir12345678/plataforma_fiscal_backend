"""Sprint 7 — Pessoal: coluna poder no silver, dim_poder_orgao (ltree), fato_pessoal.

Revision ID: 0010_sprint7_pessoal
Revises: 0009_sprint6_despesa
Create Date: 2026-07-21

O RGF segrega a despesa com pessoal por **poder** (``co_poder``: E/L/J/M/D). A Sprint 1
não capturava esse campo no silver; adicionamos ``poder`` aos silver de relatório
(RREO/RGF/DCA, mesma forma). ``dim_poder_orgao`` (Ente→Poder→Órgão→Unidade) é a
hierarquia genérica; ``fato_pessoal`` guarda os estágios do cálculo por poder.
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_sprint7_pessoal"
down_revision: str | None = "0009_sprint6_despesa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")
_SILVER_RELATORIOS = ("siconfi_rreo", "siconfi_rgf", "siconfi_dca")


def upgrade() -> None:
    # --- silver: captura do poder (co_poder) nos relatórios tipados ---
    for nome in _SILVER_RELATORIOS:
        op.add_column(nome, sa.Column("poder", sa.Text(), nullable=True), schema="silver")

    # --- dim_poder_orgao (hierárquica: Ente→Poder→Órgão→Unidade) ---
    op.execute(
        """
        CREATE TABLE gold.dim_poder_orgao (
            codigo text PRIMARY KEY,
            descricao text NOT NULL,
            parent_codigo text REFERENCES gold.dim_poder_orgao(codigo),
            nivel integer NOT NULL,
            path ltree NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_dim_poder_orgao_path ON gold.dim_poder_orgao USING gist (path)"
    )
    op.execute(
        "CREATE INDEX ix_dim_poder_orgao_parent ON gold.dim_poder_orgao (parent_codigo)"
    )

    # --- fato_pessoal (despesa com pessoal por poder; RGF Anexo de Despesa com Pessoal) ---
    op.create_table(
        "fato_pessoal",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column(
            "poder_codigo",
            sa.Text(),
            sa.ForeignKey("gold.dim_poder_orgao.codigo"),
            nullable=False,
        ),
        sa.Column("despesa_bruta", sa.Numeric(), nullable=True),
        sa.Column("exclusoes", sa.Numeric(), nullable=True),
        sa.Column("despesa_liquida", sa.Numeric(), nullable=True),
        sa.Column("pct_rcl", sa.Numeric(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge", "periodo", "poder_codigo", "versao_entrega", name="uq_fato_pessoal_chave"
        ),
        schema="gold",
    )
    op.create_index(
        "ix_fato_pessoal_ente_periodo", "fato_pessoal", ["cod_ibge", "periodo"], schema="gold"
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gold TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_fato_pessoal_ente_periodo", table_name="fato_pessoal", schema="gold")
    op.drop_table("fato_pessoal", schema="gold")
    op.execute("DROP TABLE IF EXISTS gold.dim_poder_orgao CASCADE")
    for nome in _SILVER_RELATORIOS:
        op.drop_column(nome, "poder", schema="silver")
