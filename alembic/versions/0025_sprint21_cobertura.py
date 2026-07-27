"""Sprint 21 — instrumento de cobertura (medição do backfill) + índices de volume.

Cria a gold materializada que torna a cobertura de dados **consultável** (não documento):
``gold.mart_cobertura_fonte`` (uma linha por fonte×ente×período, com a versão vigente,
a contagem de registros silver e a defasagem em períodos pela cadência da fonte) e
``gold.catalogo_fonte`` (metadado por fonte: família, cadência, órgão — DADO). Ambas
alimentam ``GET /admin/ingestion/cobertura`` e ``/fontes``, o instrumento pelo qual a
Sprint 21 é medida. Índices adicionais absorvem o volume novo do backfill (CE completo).

Revision ID: 0025_sprint21_cobertura
Revises: 0024_sprint18_admin_billing
Create Date: 2026-07-23
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_sprint21_cobertura"
down_revision: str | None = "0024_sprint18_admin_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")


def upgrade() -> None:
    # --- gold.catalogo_fonte: metadado por fonte (DADO, semeado do CONNECTOR_REGISTRY) ---
    op.create_table(
        "catalogo_fonte",
        sa.Column("fonte", sa.Text(), primary_key=True),
        sa.Column("familia", sa.Text(), nullable=False),
        sa.Column("relatorio", sa.Text(), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("cadencia", sa.Text(), nullable=False),
        sa.Column("orgao", sa.Text(), nullable=True),
        sa.Column("url_origem", sa.Text(), nullable=True),
        sa.Column("escopo", sa.Text(), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="gold",
    )

    # --- gold.mart_cobertura_fonte: matriz de cobertura materializada ---
    op.create_table(
        "mart_cobertura_fonte",
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("uf", sa.String(length=2), nullable=True),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("n_registros", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("versao_entrega_vigente", sa.Text(), nullable=True),
        sa.Column("ingerido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("defasagem_periodos", sa.Integer(), nullable=True),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("fonte", "cod_ibge", "periodo", name="pk_mart_cobertura_fonte"),
        schema="gold",
    )
    op.create_index(
        "ix_mart_cobertura_fonte_filtro",
        "mart_cobertura_fonte",
        ["fonte", "uf", "ano"],
        schema="gold",
    )

    # --- Índices de volume (o backfill CE multiplica o número de linhas por ~200x) ---
    # Séries longas do BCB são varridas por (série, data); o backfill 2019→hoje as engorda.
    op.create_index(
        "ix_bcb_indice_serie_data",
        "bcb_indice",
        ["codigo_serie", "data_ref"],
        schema="silver",
    )
    # A resolução de versão as_of/vigente é o caminho mais quente sob multi-período.
    # (Leituras dos marts por (cod_ibge, periodo) já usam ix_mart_indicador_ente_periodo,
    # criado na Sprint 2.)
    op.create_index(
        "ix_dim_entrega_vigente",
        "dim_entrega",
        ["relatorio", "periodo", "vigente"],
        schema="gold",
    )

    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gold TO {APP_ROLE}"
    )


def downgrade() -> None:
    op.drop_index("ix_dim_entrega_vigente", table_name="dim_entrega", schema="gold")
    op.drop_index("ix_bcb_indice_serie_data", table_name="bcb_indice", schema="silver")
    op.drop_index(
        "ix_mart_cobertura_fonte_filtro", table_name="mart_cobertura_fonte", schema="gold"
    )
    op.drop_table("mart_cobertura_fonte", schema="gold")
    op.drop_table("catalogo_fonte", schema="gold")
