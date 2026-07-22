"""Sprint 9 — Resultado Fiscal: fato_resultado e fato_ajuste_metodologico (RREO Anexo 6).

Revision ID: 0013_sprint9_resultado
Revises: 0012_sprint8_divida
Create Date: 2026-07-21

Materializa o Demonstrativo do Resultado Primário e Nominal (RREO Anexo 6). Resultado
primário e nominal "acima da linha" (receitas−despesas primárias; primário−juros), a DCL
"abaixo da linha" (início×fim do exercício) e os ajustes metodológicos que reconciliam as
duas apurações. Tudo em reais, versionado por entrega (bitemporal).
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_sprint9_resultado"
down_revision: str | None = "0012_sprint8_divida"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")


def upgrade() -> None:
    op.create_table(
        "fato_resultado",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        # Acima da linha (orçamentário).
        sa.Column("receita_primaria", sa.Numeric(), nullable=True),
        sa.Column("despesa_primaria", sa.Numeric(), nullable=True),
        sa.Column("resultado_primario", sa.Numeric(), nullable=True),
        sa.Column("juros_ativos", sa.Numeric(), nullable=True),
        sa.Column("juros_passivos", sa.Numeric(), nullable=True),
        sa.Column("resultado_nominal", sa.Numeric(), nullable=True),
        # Abaixo da linha (variação da dívida).
        sa.Column("dcl_inicio", sa.Numeric(), nullable=True),
        sa.Column("dcl_fim", sa.Numeric(), nullable=True),
        sa.Column("resultado_nominal_abaixo", sa.Numeric(), nullable=True),
        sa.Column("resultado_nominal_abaixo_ajustado", sa.Numeric(), nullable=True),
        sa.Column("resultado_primario_abaixo", sa.Numeric(), nullable=True),
        # Metas fiscais (LDO).
        sa.Column("meta_primario", sa.Numeric(), nullable=True),
        sa.Column("meta_nominal", sa.Numeric(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge", "periodo", "versao_entrega", name="uq_fato_resultado_chave"
        ),
        schema="gold",
    )
    op.create_index(
        "ix_fato_resultado_ente_periodo", "fato_resultado", ["cod_ibge", "periodo"], schema="gold"
    )

    op.create_table(
        "fato_ajuste_metodologico",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("ajuste_codigo", sa.Text(), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("valor", sa.Numeric(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge", "periodo", "ajuste_codigo", "versao_entrega",
            name="uq_fato_ajuste_metodologico_chave",
        ),
        schema="gold",
    )
    op.create_index(
        "ix_fato_ajuste_metodologico_ente_periodo", "fato_ajuste_metodologico",
        ["cod_ibge", "periodo"], schema="gold",
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gold TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index(
        "ix_fato_ajuste_metodologico_ente_periodo", table_name="fato_ajuste_metodologico",
        schema="gold",
    )
    op.drop_table("fato_ajuste_metodologico", schema="gold")
    op.drop_index("ix_fato_resultado_ente_periodo", table_name="fato_resultado", schema="gold")
    op.drop_table("fato_resultado", schema="gold")
