"""Sprint 4 — Painel de Carteira / Visão Estadual (Módulo 2).

Cria ``gold.mart_carteira`` (sumarização por ente/período/indicador com faixa e
conformidade — snapshot da versão vigente, para as visões agregadas) e
``op.carteira_lote_job`` (ações em lote enfileiradas, isoladas por org via RLS).

Revision ID: 0007_sprint4_carteira
Revises: 0006_sprint3_providencias
Create Date: 2026-07-07
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_sprint4_carteira"
down_revision: str | None = "0006_sprint3_providencias"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

LOTE_ACOES = ("relatorio", "alerta")

# Mesmo predicado de isolamento por org_id usado nas demais tabelas do schema op.
_RLS_PREDICATE = (
    "coalesce(current_setting('app.is_admin', true), 'off') = 'on' "
    "OR org_id = nullif(current_setting('app.current_org_id', true), '')::uuid"
)


def _in_list(col: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{col} in ({joined})"


def upgrade() -> None:
    # --- gold.mart_carteira (público/compartilhado — sem RLS) ---
    op.create_table(
        "mart_carteira",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("indicador", sa.Text(), nullable=False),
        sa.Column("faixa", sa.Text(), nullable=True),
        sa.Column("valor_pct", sa.Numeric(), nullable=True),
        sa.Column("conformidade_status", sa.Text(), nullable=False),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "cod_ibge", "periodo", "indicador", name="uq_mart_carteira_chave"
        ),
        schema="gold",
    )
    op.create_index(
        "ix_mart_carteira_periodo_indicador",
        "mart_carteira",
        ["periodo", "indicador"],
        schema="gold",
    )
    op.create_index(
        "ix_mart_carteira_ente_periodo",
        "mart_carteira",
        ["cod_ibge", "periodo"],
        schema="gold",
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gold TO {APP_ROLE}")

    # --- op.carteira_lote_job (operacional — isolado por org via RLS) ---
    op.create_table(
        "carteira_lote_job",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("acao", sa.String(length=20), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'enfileirado'"), nullable=False),
        sa.Column("total_entes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("entes", postgresql.JSONB(), nullable=False),
        sa.Column("filtro", postgresql.JSONB(), nullable=True),
        sa.Column("criado_por", sa.Uuid(), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["op.organizacao.id"], name="fk_carteira_lote_job_org", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["criado_por"], ["op.usuario.id"], name="fk_carteira_lote_job_usuario",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(_in_list("acao", LOTE_ACOES), name="ck_carteira_lote_job_acao"),
        schema="op",
    )
    op.create_index("ix_carteira_lote_job_org_id", "carteira_lote_job", ["org_id"], schema="op")

    op.execute("ALTER TABLE op.carteira_lote_job ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE op.carteira_lote_job FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY carteira_lote_job_tenant_isolation ON op.carteira_lote_job "
        f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA op TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS carteira_lote_job_tenant_isolation ON op.carteira_lote_job")
    op.execute("ALTER TABLE op.carteira_lote_job DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_carteira_lote_job_org_id", table_name="carteira_lote_job", schema="op")
    op.drop_table("carteira_lote_job", schema="op")

    op.drop_index("ix_mart_carteira_ente_periodo", table_name="mart_carteira", schema="gold")
    op.drop_index("ix_mart_carteira_periodo_indicador", table_name="mart_carteira", schema="gold")
    op.drop_table("mart_carteira", schema="gold")
