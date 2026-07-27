"""Sprint 15 — Alertas & Conformidade (Módulo 14).

Revision ID: 0021_sprint15_alerts
Revises: 0020_sprint14_forecast
Create Date: 2026-07-22

- ``op.alerta`` — fila de alertas **privada da organização** (RLS por ``org_id``).
  Dedup por ``(org_id, chave)``: o motor pode reavaliar sem duplicar; ``status`` do
  gestor (reconhecida/resolvida) é preservado nas reavaliações.
- ``gold.calendario_obrigacao`` — calendário de obrigações **compartilhado**
  (dado público): prazo por porte/esfera e status (entregue/pendente/atrasado).
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_sprint15_alerts"
down_revision: str | None = "0020_sprint14_forecast"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

_RLS_PREDICATE = (
    "coalesce(current_setting('app.is_admin', true), 'off') = 'on' "
    "OR org_id = nullif(current_setting('app.current_org_id', true), '')::uuid"
)


def upgrade() -> None:
    # --- gold.calendario_obrigacao (compartilhado, sem RLS) ---
    op.create_table(
        "calendario_obrigacao",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("relatorio", sa.Text(), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("periodicidade", sa.String(length=20), nullable=False),
        sa.Column("prazo", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'pendente'")),
        sa.Column("entregue_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=True),
        sa.Column("source_ref", postgresql.JSONB(), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('entregue', 'pendente', 'atrasado')",
            name="ck_calendario_obrigacao_status",
        ),
        sa.UniqueConstraint("cod_ibge", "relatorio", "periodo", name="uq_calendario_obrigacao_chave"),
        schema="gold",
    )
    op.create_index(
        "ix_calendario_obrigacao_ente",
        "calendario_obrigacao",
        ["cod_ibge", "relatorio", "periodo"],
        schema="gold",
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON gold.calendario_obrigacao TO {APP_ROLE}")

    # --- op.alerta (privado do tenant, RLS por org_id) ---
    op.create_table(
        "alerta",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("chave", sa.Text(), nullable=False),
        sa.Column("categoria", sa.String(length=24), nullable=False),
        sa.Column("severidade", sa.String(length=16), nullable=False),
        sa.Column("prioridade", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("titulo", sa.Text(), nullable=False),
        sa.Column("motivo_legal", sa.Text(), nullable=False),
        sa.Column("acao_sugerida", sa.Text(), nullable=False),
        sa.Column("prazo", sa.Date(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default=sa.text("'nova'")),
        sa.Column("indicador", sa.Text(), nullable=True),
        sa.Column("periodo", sa.Text(), nullable=True),
        sa.Column("source_ref", postgresql.JSONB(), nullable=True),
        sa.Column("memoria", postgresql.JSONB(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["org_id"], ["op.organizacao.id"], name="fk_alerta_org", ondelete="CASCADE"),
        sa.CheckConstraint(
            "severidade IN ('critico', 'atencao', 'informativo')",
            name="ck_alerta_severidade",
        ),
        sa.CheckConstraint(
            "status IN ('nova', 'reconhecida', 'resolvida', 'descartada')",
            name="ck_alerta_status",
        ),
        sa.UniqueConstraint("org_id", "chave", name="uq_alerta_org_chave"),
        schema="op",
    )
    op.create_index("ix_alerta_org_id", "alerta", ["org_id"], schema="op")
    op.create_index(
        "ix_alerta_fila", "alerta", ["org_id", "status", "prioridade"], schema="op"
    )
    op.create_index("ix_alerta_org_ente", "alerta", ["org_id", "cod_ibge"], schema="op")

    op.execute("ALTER TABLE op.alerta ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE op.alerta FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY alerta_tenant_isolation ON op.alerta "
        f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON op.alerta TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS alerta_tenant_isolation ON op.alerta")
    op.execute("ALTER TABLE op.alerta DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_alerta_org_ente", table_name="alerta", schema="op")
    op.drop_index("ix_alerta_fila", table_name="alerta", schema="op")
    op.drop_index("ix_alerta_org_id", table_name="alerta", schema="op")
    op.drop_table("alerta", schema="op")

    op.drop_index("ix_calendario_obrigacao_ente", table_name="calendario_obrigacao", schema="gold")
    op.drop_table("calendario_obrigacao", schema="gold")
