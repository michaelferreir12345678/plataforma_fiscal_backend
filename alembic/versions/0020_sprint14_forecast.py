"""Sprint 14 — Previsões & Cenários (Módulo 13).

Revision ID: 0020_sprint14_forecast
Revises: 0019_sprint13_cohort_bitemporal
Create Date: 2026-07-22

Duas tabelas com naturezas opostas:

- ``gold.fato_projecao`` — dado analítico **público/compartilhado** (sem RLS): a
  projeção de um indicador de um ente é reproduzível e não pertence a um tenant.
  Toda projeção carrega **IC** (``ic_inferior``/``ic_superior``) — nunca número
  único — garantido por *check constraint* no banco.
- ``op.cenario`` — simulação **privada da organização** (com RLS por ``org_id``):
  só existe quando o gestor **salva** explicitamente. A rota de simular não grava.
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_sprint14_forecast"
down_revision: str | None = "0019_sprint13_cohort_bitemporal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

_RLS_PREDICATE = (
    "coalesce(current_setting('app.is_admin', true), 'off') = 'on' "
    "OR org_id = nullif(current_setting('app.current_org_id', true), '')::uuid"
)


def upgrade() -> None:
    # --- gold.fato_projecao (analítico compartilhado, sem RLS) ---
    op.create_table(
        "fato_projecao",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("indicador", sa.Text(), nullable=False),
        sa.Column("periodo_alvo", sa.Text(), nullable=False),
        sa.Column("horizonte", sa.Integer(), nullable=False),
        sa.Column("valor_previsto", sa.Numeric(), nullable=False),
        sa.Column("ic_inferior", sa.Numeric(), nullable=False),
        sa.Column("ic_superior", sa.Numeric(), nullable=False),
        sa.Column("nivel_confianca", sa.Numeric(), nullable=False, server_default=sa.text("95")),
        sa.Column("unidade", sa.String(length=20), nullable=False),
        sa.Column("modelo", sa.Text(), nullable=False),
        sa.Column("teto_pct", sa.Numeric(), nullable=True),
        sa.Column("faixa", sa.Text(), nullable=True),
        sa.Column("cruza_limite", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("gerado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("source_ref", postgresql.JSONB(), nullable=True),
        sa.Column("memoria", postgresql.JSONB(), nullable=True),
        # IC honesto: banda válida, com o previsto sempre dentro dela (nunca número único).
        sa.CheckConstraint(
            "ic_inferior <= valor_previsto AND valor_previsto <= ic_superior",
            name="ck_fato_projecao_ic",
        ),
        sa.UniqueConstraint(
            "cod_ibge", "indicador", "modelo", "periodo_alvo",
            name="uq_fato_projecao_chave",
        ),
        schema="gold",
    )
    op.create_index(
        "ix_fato_projecao_consulta",
        "fato_projecao",
        ["cod_ibge", "indicador", "modelo", "periodo_alvo"],
        schema="gold",
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON gold.fato_projecao TO {APP_ROLE}")

    # --- op.cenario (privado do tenant, RLS por org_id) ---
    op.create_table(
        "cenario",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("ente", sa.String(length=7), nullable=False),
        sa.Column("indicador", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("parametros", postgresql.JSONB(), nullable=False),
        sa.Column("resultado", postgresql.JSONB(), nullable=True),
        sa.Column("criado_por", sa.Uuid(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["org_id"], ["op.organizacao.id"], name="fk_cenario_org", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["criado_por"], ["op.usuario.id"], name="fk_cenario_usuario", ondelete="SET NULL"
        ),
        schema="op",
    )
    op.create_index("ix_cenario_org_id", "cenario", ["org_id"], schema="op")
    op.create_index("ix_cenario_org_ente", "cenario", ["org_id", "ente"], schema="op")

    op.execute("ALTER TABLE op.cenario ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE op.cenario FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY cenario_tenant_isolation ON op.cenario "
        f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
    )
    # Grants já cobertos por ALTER DEFAULT PRIVILEGES (migration 0001); explícito por segurança.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON op.cenario TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS cenario_tenant_isolation ON op.cenario")
    op.execute("ALTER TABLE op.cenario DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_cenario_org_ente", table_name="cenario", schema="op")
    op.drop_index("ix_cenario_org_id", table_name="cenario", schema="op")
    op.drop_table("cenario", schema="op")

    op.drop_index("ix_fato_projecao_consulta", table_name="fato_projecao", schema="gold")
    op.drop_table("fato_projecao", schema="gold")
