"""Sprint 1 — ingestão SICONFI: medallion (bronze/silver/gold) bitemporal.

Revision ID: 0002_sprint1_ingestion
Revises: 0001_sprint0_tenancy
Create Date: 2026-07-04
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_sprint1_ingestion"
down_revision: str | None = "0001_sprint0_tenancy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

# Fontes SICONFI (uma partição de bronze por fonte). Espelha FONTES em app.modules.ingestion.
FONTES = (
    "siconfi_rreo",
    "siconfi_rgf",
    "siconfi_dca",
    "siconfi_msc",
    "siconfi_extratos",
    "siconfi_entes",
)

def _silver_relatorio_columns() -> list[sa.Column]:
    """Colunas de um silver tipado por relatório (RREO/RGF/DCA compartilham a forma)."""
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("anexo", sa.Text(), nullable=True),
        sa.Column("conta", sa.Text(), nullable=True),
        sa.Column("coluna", sa.Text(), nullable=True),
        sa.Column("valor", sa.Numeric(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
    ]


def _silver_relatorio_table(name: str) -> None:
    op.create_table(name, *_silver_relatorio_columns(), schema="silver")
    op.create_index(
        f"ix_{name}_chave", name, ["cod_ibge", "periodo", "versao_entrega"], schema="silver"
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    op.execute("CREATE SCHEMA IF NOT EXISTS silver")
    op.execute("CREATE SCHEMA IF NOT EXISTS gold")

    # --- bronze.raw_payload: imutável, JSONB, particionada por LIST(fonte) ---
    op.execute(
        """
        CREATE TABLE bronze.raw_payload (
            fonte text NOT NULL,
            ano integer NOT NULL,
            periodo text NOT NULL,
            cod_ibge text NOT NULL,
            versao text NOT NULL,
            ingerido_em timestamptz NOT NULL DEFAULT now(),
            hash_payload text NOT NULL,
            payload jsonb NOT NULL,
            CONSTRAINT pk_raw_payload PRIMARY KEY (fonte, cod_ibge, periodo, versao)
        ) PARTITION BY LIST (fonte)
        """
    )
    op.execute("CREATE TABLE bronze.raw_payload_default PARTITION OF bronze.raw_payload DEFAULT")
    for fonte in FONTES:
        op.execute(
            f"CREATE TABLE bronze.raw_payload_{fonte} "
            f"PARTITION OF bronze.raw_payload FOR VALUES IN ('{fonte}')"
        )
    op.execute("CREATE INDEX ix_raw_payload_fonte_ano ON bronze.raw_payload (fonte, ano)")

    # --- gold.dim_entrega: controle de versões/retificação (bitemporal) ---
    op.create_table(
        "dim_entrega",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("relatorio", sa.Text(), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.Column("homologada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vigente", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("hash_payload", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "cod_ibge", "relatorio", "periodo", "versao_entrega", name="uq_dim_entrega_chave"
        ),
        schema="gold",
    )
    op.create_index(
        "ix_dim_entrega_periodo", "dim_entrega", ["cod_ibge", "relatorio", "periodo"], schema="gold"
    )

    # --- silver tipado por relatório (RREO/RGF/DCA compartilham a forma) ---
    _silver_relatorio_table("siconfi_rreo")
    _silver_relatorio_table("siconfi_rgf")
    _silver_relatorio_table("siconfi_dca")

    # --- silver MSC: granularidade máxima (conta PCASP) ---
    op.create_table(
        "siconfi_msc",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("cod_conta_pcasp", sa.Text(), nullable=False),
        sa.Column("saldo_inicial", sa.Numeric(), nullable=True),
        sa.Column("mov_devedor", sa.Numeric(), nullable=True),
        sa.Column("mov_credor", sa.Numeric(), nullable=True),
        sa.Column("saldo_final", sa.Numeric(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        schema="silver",
    )
    op.create_index(
        "ix_siconfi_msc_chave", "siconfi_msc", ["cod_ibge", "periodo", "versao_entrega"],
        schema="silver",
    )

    # --- silver entes (cadastro) ---
    op.create_table(
        "siconfi_entes",
        sa.Column("cod_ibge", sa.String(length=7), primary_key=True),
        sa.Column("nome", sa.Text(), nullable=True),
        sa.Column("uf", sa.String(length=2), nullable=True),
        sa.Column("esfera", sa.String(length=1), nullable=True),
        sa.Column("populacao", sa.Integer(), nullable=True),
        sa.Column("regiao", sa.Text(), nullable=True),
        sa.Column("capital", sa.Boolean(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=True),
        sa.Column("carregado_em", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="silver",
    )

    # --- silver extratos (dispara reprocessamento) ---
    op.create_table(
        "siconfi_extratos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("relatorio", sa.Text(), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("homologada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("versao", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge", "relatorio", "periodo", "versao", name="uq_siconfi_extratos_chave"
        ),
        schema="silver",
    )
    op.create_index(
        "ix_siconfi_extratos_chave", "siconfi_extratos", ["cod_ibge", "relatorio", "periodo"],
        schema="silver",
    )

    # --- gold.ingestion_log: observabilidade dos jobs ---
    op.create_table(
        "ingestion_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("cod_ibge", sa.String(length=7), nullable=True),
        sa.Column("periodo", sa.Text(), nullable=True),
        sa.Column("versao", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("mensagem", sa.Text(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="gold",
    )
    op.create_index("ix_ingestion_log_fonte", "ingestion_log", ["fonte"], schema="gold")

    # --- Grants para a role de aplicação (lago é público/compartilhado; sem RLS) ---
    for schema in ("bronze", "silver", "gold"):
        op.execute(f"GRANT USAGE ON SCHEMA {schema} TO {APP_ROLE}")
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {APP_ROLE}"
        )
        op.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
        )


def downgrade() -> None:
    op.drop_index("ix_ingestion_log_fonte", table_name="ingestion_log", schema="gold")
    op.drop_table("ingestion_log", schema="gold")
    op.drop_index("ix_siconfi_extratos_chave", table_name="siconfi_extratos", schema="silver")
    op.drop_table("siconfi_extratos", schema="silver")
    op.drop_table("siconfi_entes", schema="silver")
    op.drop_index("ix_siconfi_msc_chave", table_name="siconfi_msc", schema="silver")
    op.drop_table("siconfi_msc", schema="silver")
    for name in ("siconfi_dca", "siconfi_rgf", "siconfi_rreo"):
        op.drop_index(f"ix_{name}_chave", table_name=name, schema="silver")
        op.drop_table(name, schema="silver")
    op.drop_index("ix_dim_entrega_periodo", table_name="dim_entrega", schema="gold")
    op.drop_table("dim_entrega", schema="gold")
    op.execute("DROP TABLE IF EXISTS bronze.raw_payload CASCADE")
    op.execute("DROP SCHEMA IF EXISTS gold CASCADE")
    op.execute("DROP SCHEMA IF EXISTS silver CASCADE")
    op.execute("DROP SCHEMA IF EXISTS bronze CASCADE")
