"""Sprint 1B (arquivos) — transferências (FPM/FUNDEB), CAPAG e SIOPS (planilhas).

Revision ID: 0004_sprint1b_arquivos
Revises: 0003_sprint1b_conectores
Create Date: 2026-07-04
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_sprint1b_arquivos"
down_revision: str | None = "0003_sprint1b_conectores"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

NEW_FONTES = (
    "tesouro_fpm",
    "fnde_fundeb_repasse",
    "transferencia_generica",
    "tesouro_capag",
    "siops_saude",
)

_SILVER_TABLES = (
    "tesouro_fpm",
    "fnde_fundeb_repasse",
    "transferencia_generica",
    "tesouro_capag",
    "siops_saude",
)


def upgrade() -> None:
    for fonte in NEW_FONTES:
        op.execute(
            f"CREATE TABLE bronze.raw_payload_{fonte} "
            f"PARTITION OF bronze.raw_payload FOR VALUES IN ('{fonte}')"
        )

    op.create_table(
        "tesouro_fpm",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("mes", sa.Integer(), nullable=True),
        sa.Column("decendio", sa.Integer(), nullable=True),
        sa.Column("valor_bruto", sa.Numeric(), nullable=True),
        sa.Column("deducoes", sa.Numeric(), nullable=True),
        sa.Column("valor_liquido", sa.Numeric(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        schema="silver",
    )
    op.create_index(
        "ix_tesouro_fpm_chave", "tesouro_fpm", ["cod_ibge", "ano", "mes", "versao_entrega"],
        schema="silver",
    )

    op.create_table(
        "fnde_fundeb_repasse",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("mes", sa.Integer(), nullable=True),
        sa.Column("valor_repassado", sa.Numeric(), nullable=True),
        sa.Column("complementacao_uniao", sa.Numeric(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        schema="silver",
    )
    op.create_index(
        "ix_fnde_fundeb_chave", "fnde_fundeb_repasse", ["cod_ibge", "ano", "mes", "versao_entrega"],
        schema="silver",
    )

    op.create_table(
        "transferencia_generica",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("mes", sa.Integer(), nullable=True),
        sa.Column("valor", sa.Numeric(), nullable=True),
        sa.Column("fonte", sa.Text(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        schema="silver",
    )
    op.create_index(
        "ix_transferencia_generica_chave", "transferencia_generica",
        ["cod_ibge", "tipo", "ano", "mes", "versao_entrega"], schema="silver",
    )

    op.create_table(
        "tesouro_capag",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("ano_ref", sa.Integer(), nullable=False),
        sa.Column("nota_final", sa.String(length=2), nullable=True),
        sa.Column("ind_endividamento", sa.Numeric(), nullable=True),
        sa.Column("ind_poupanca", sa.Numeric(), nullable=True),
        sa.Column("ind_liquidez", sa.Numeric(), nullable=True),
        sa.Column("metodologia_versao", sa.Text(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        schema="silver",
    )
    op.create_index(
        "ix_tesouro_capag_chave", "tesouro_capag", ["cod_ibge", "ano_ref", "versao_entrega"],
        schema="silver",
    )

    op.create_table(
        "siops_saude",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("bimestre", sa.Integer(), nullable=True),
        sa.Column("indicador_codigo", sa.Text(), nullable=False),
        sa.Column("valor", sa.Numeric(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        schema="silver",
    )
    op.create_index(
        "ix_siops_saude_chave", "siops_saude",
        ["cod_ibge", "ano", "bimestre", "versao_entrega"], schema="silver",
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA silver TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA bronze TO {APP_ROLE}")


def downgrade() -> None:
    for name in _SILVER_TABLES:
        op.execute(f"DROP TABLE IF EXISTS silver.{name} CASCADE")
    for fonte in NEW_FONTES:
        op.execute(f"DROP TABLE IF EXISTS bronze.raw_payload_{fonte}")
