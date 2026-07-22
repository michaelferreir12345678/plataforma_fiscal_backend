"""Sprint 1B — conectores complementares: SADIPEM, BCB/SGS, IBGE (silver + partições bronze).

Revision ID: 0003_sprint1b_conectores
Revises: 0002_sprint1_ingestion
Create Date: 2026-07-04
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_sprint1b_conectores"
down_revision: str | None = "0002_sprint1_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

# Novas fontes (uma partição de bronze por fonte). Espelha os `fonte` dos conectores.
NEW_FONTES = (
    "sadipem_pvl",
    "sadipem_op_contratada",
    "sadipem_cronograma_pgto",
    "sadipem_cdp",
    "bcb",
    "ibge_populacao",
    "ibge_pib",
)

_SILVER_TABLES = (
    "sadipem_pvl",
    "sadipem_op_contratada",
    "sadipem_cronograma_pgto",
    "sadipem_cdp",
    "bcb_indice",
    "ibge_populacao",
    "ibge_pib",
)


def upgrade() -> None:
    # Novas partições de bronze (o dado do lago é público; sem RLS).
    for fonte in NEW_FONTES:
        op.execute(
            f"CREATE TABLE bronze.raw_payload_{fonte} "
            f"PARTITION OF bronze.raw_payload FOR VALUES IN ('{fonte}')"
        )

    # --- SADIPEM ---
    op.create_table(
        "sadipem_pvl",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("id_pvl", sa.Text(), nullable=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("tipo_operacao", sa.Text(), nullable=True),
        sa.Column("valor", sa.Numeric(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("decisao", sa.Text(), nullable=True),
        sa.Column("data_analise", sa.Date(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        schema="silver",
    )
    op.create_index(
        "ix_sadipem_pvl_chave", "sadipem_pvl", ["cod_ibge", "versao_entrega"], schema="silver"
    )

    op.create_table(
        "sadipem_op_contratada",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("id_operacao", sa.Text(), nullable=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("credor", sa.Text(), nullable=True),
        sa.Column("moeda", sa.Text(), nullable=True),
        sa.Column("valor_contratado", sa.Numeric(), nullable=True),
        sa.Column("data_contratacao", sa.Date(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        schema="silver",
    )
    op.create_index(
        "ix_sadipem_op_chave", "sadipem_op_contratada", ["cod_ibge", "versao_entrega"],
        schema="silver",
    )

    op.create_table(
        "sadipem_cronograma_pgto",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("id_operacao", sa.Text(), nullable=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("ano", sa.Integer(), nullable=True),
        sa.Column("mes", sa.Integer(), nullable=True),
        sa.Column("principal", sa.Numeric(), nullable=True),
        sa.Column("juros", sa.Numeric(), nullable=True),
        sa.Column("encargos", sa.Numeric(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        schema="silver",
    )
    op.create_index(
        "ix_sadipem_cronograma_chave", "sadipem_cronograma_pgto", ["cod_ibge", "versao_entrega"],
        schema="silver",
    )

    op.create_table(
        "sadipem_cdp",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("data_ref", sa.Date(), nullable=True),
        sa.Column("situacao", sa.Text(), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        schema="silver",
    )
    op.create_index(
        "ix_sadipem_cdp_chave", "sadipem_cdp", ["cod_ibge", "versao_entrega"], schema="silver"
    )

    # --- BCB/SGS (long format: uma linha por data×série) ---
    op.create_table(
        "bcb_indice",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("codigo_serie", sa.Integer(), nullable=False),
        sa.Column("data_ref", sa.Date(), nullable=False),
        sa.Column("valor", sa.Numeric(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        schema="silver",
    )
    op.create_index(
        "ix_bcb_indice_chave", "bcb_indice", ["codigo_serie", "versao_entrega"], schema="silver"
    )

    # --- IBGE (anual) ---
    op.create_table(
        "ibge_populacao",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("ano_ref", sa.Integer(), nullable=False),
        sa.Column("populacao", sa.BigInteger(), nullable=True),
        sa.Column("fonte", sa.Text(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        schema="silver",
    )
    op.create_index(
        "ix_ibge_populacao_chave", "ibge_populacao", ["cod_ibge", "ano_ref", "versao_entrega"],
        schema="silver",
    )

    op.create_table(
        "ibge_pib",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("ano_ref", sa.Integer(), nullable=False),
        sa.Column("pib_nominal", sa.Numeric(), nullable=True),
        sa.Column("pib_per_capita", sa.Numeric(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        schema="silver",
    )
    op.create_index(
        "ix_ibge_pib_chave", "ibge_pib", ["cod_ibge", "ano_ref", "versao_entrega"], schema="silver"
    )

    # Grants (reforço; ALTER DEFAULT PRIVILEGES da 0002 já cobre novas tabelas).
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA silver TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA bronze TO {APP_ROLE}")


def downgrade() -> None:
    for name in _SILVER_TABLES:
        op.execute(f"DROP TABLE IF EXISTS silver.{name} CASCADE")
    for fonte in NEW_FONTES:
        op.execute(f"DROP TABLE IF EXISTS bronze.raw_payload_{fonte}")
