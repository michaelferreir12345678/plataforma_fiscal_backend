"""Sprint 11 — Saúde & Educação: mínimos RREO e enriquecimento SIOPE.

Revision ID: 0015_sprint11_health_edu
Revises: 0014_sprint10_cash_rap
Create Date: 2026-07-21
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_sprint11_health_edu"
down_revision: str | None = "0014_sprint10_cash_rap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")


def upgrade() -> None:
    # SIOPE é enriquecimento bimestral; a fonte primária continua sendo RREO A8.
    op.execute(
        "CREATE TABLE bronze.raw_payload_siope_educacao "
        "PARTITION OF bronze.raw_payload FOR VALUES IN ('siope_educacao')"
    )
    op.add_column(
        "siops_saude", sa.Column("indicador_descricao", sa.Text(), nullable=True),
        schema="silver",
    )
    op.add_column(
        "siops_saude", sa.Column("unidade", sa.Text(), nullable=True), schema="silver"
    )
    op.create_table(
        "siope_educacao",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("bimestre", sa.Integer(), nullable=True),
        sa.Column("indicador_codigo", sa.Text(), nullable=False),
        sa.Column("indicador_descricao", sa.Text(), nullable=True),
        sa.Column("unidade", sa.Text(), nullable=True),
        sa.Column("valor", sa.Numeric(), nullable=True),
        sa.Column("valid_time", sa.Date(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        schema="silver",
    )
    op.create_index(
        "ix_siope_educacao_chave",
        "siope_educacao",
        ["cod_ibge", "ano", "bimestre", "versao_entrega"],
        schema="silver",
    )

    op.create_table(
        "fato_saude",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("base_impostos_transferencias", sa.Numeric(), nullable=False),
        sa.Column("despesa_bruta", sa.Numeric(), nullable=False),
        sa.Column("deducoes_outras", sa.Numeric(), nullable=False),
        sa.Column("rpnp_sem_lastro", sa.Numeric(), nullable=False),
        sa.Column("despesa_aplicada", sa.Numeric(), nullable=False),
        sa.Column("pct_aplicado", sa.Numeric(), nullable=False),
        sa.Column("minimo_pct", sa.Numeric(), nullable=False),
        sa.Column("valor_minimo", sa.Numeric(), nullable=False),
        sa.Column("abaixo_do_minimo", sa.Boolean(), nullable=False),
        sa.Column("versao_rreo", sa.Text(), nullable=False),
        sa.Column("versao_rgf", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge", "periodo", "versao_rreo", "versao_rgf",
            name="uq_fato_saude_chave",
        ),
        schema="gold",
    )
    op.create_index(
        "ix_fato_saude_ente_periodo", "fato_saude", ["cod_ibge", "periodo"],
        schema="gold",
    )

    op.create_table(
        "fato_saude_subfuncao",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column(
            "funcao_codigo", sa.Text(), sa.ForeignKey("gold.dim_funcao.codigo"), nullable=False
        ),
        sa.Column("empenhado", sa.Numeric(), nullable=True),
        sa.Column("liquidado", sa.Numeric(), nullable=True),
        sa.Column("pago", sa.Numeric(), nullable=True),
        sa.Column("valor_computado", sa.Numeric(), nullable=False),
        sa.Column("versao_rreo", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge", "periodo", "funcao_codigo", "versao_rreo",
            name="uq_fato_saude_subfuncao_chave",
        ),
        schema="gold",
    )
    op.create_index(
        "ix_fato_saude_subfuncao_ente_periodo", "fato_saude_subfuncao",
        ["cod_ibge", "periodo"], schema="gold",
    )

    op.create_table(
        "fato_educacao",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("base_impostos_transferencias", sa.Numeric(), nullable=False),
        sa.Column("despesa_bruta", sa.Numeric(), nullable=False),
        sa.Column("despesa_impostos", sa.Numeric(), nullable=False),
        sa.Column("despesa_fundeb", sa.Numeric(), nullable=False),
        sa.Column("deducoes_outras", sa.Numeric(), nullable=False),
        sa.Column("rpnp_sem_lastro", sa.Numeric(), nullable=False),
        sa.Column("despesa_aplicada", sa.Numeric(), nullable=False),
        sa.Column("pct_aplicado", sa.Numeric(), nullable=False),
        sa.Column("minimo_pct", sa.Numeric(), nullable=False),
        sa.Column("valor_minimo", sa.Numeric(), nullable=False),
        sa.Column("abaixo_do_minimo", sa.Boolean(), nullable=False),
        sa.Column("fundeb_base_profissionais", sa.Numeric(), nullable=False),
        sa.Column("fundeb_aplicado_profissionais", sa.Numeric(), nullable=False),
        sa.Column("fundeb_pct_profissionais", sa.Numeric(), nullable=False),
        sa.Column("fundeb_minimo_pct", sa.Numeric(), nullable=False),
        sa.Column("fundeb_valor_minimo", sa.Numeric(), nullable=False),
        sa.Column("fundeb_abaixo_do_minimo", sa.Boolean(), nullable=False),
        sa.Column("versao_rreo", sa.Text(), nullable=False),
        sa.Column("versao_rgf", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge", "periodo", "versao_rreo", "versao_rgf",
            name="uq_fato_educacao_chave",
        ),
        schema="gold",
    )
    op.create_index(
        "ix_fato_educacao_ente_periodo", "fato_educacao", ["cod_ibge", "periodo"],
        schema="gold",
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA silver TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA bronze TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gold TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_fato_educacao_ente_periodo", table_name="fato_educacao", schema="gold")
    op.drop_table("fato_educacao", schema="gold")
    op.drop_index(
        "ix_fato_saude_subfuncao_ente_periodo",
        table_name="fato_saude_subfuncao", schema="gold",
    )
    op.drop_table("fato_saude_subfuncao", schema="gold")
    op.drop_index("ix_fato_saude_ente_periodo", table_name="fato_saude", schema="gold")
    op.drop_table("fato_saude", schema="gold")
    op.drop_index("ix_siope_educacao_chave", table_name="siope_educacao", schema="silver")
    op.drop_table("siope_educacao", schema="silver")
    # ``IF EXISTS`` também permite atualizar com segurança uma base local que já havia
    # aplicado uma revisão de desenvolvimento anterior desta migration.
    op.execute("ALTER TABLE silver.siops_saude DROP COLUMN IF EXISTS unidade")
    op.execute("ALTER TABLE silver.siops_saude DROP COLUMN IF EXISTS indicador_descricao")
    op.execute("DROP TABLE IF EXISTS bronze.raw_payload_siope_educacao")
