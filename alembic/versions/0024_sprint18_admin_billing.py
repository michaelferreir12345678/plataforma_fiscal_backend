"""Sprint 18 — Administração, Carteira avançada & Billing (Módulo 17).

Revision ID: 0024_sprint18_admin_billing
Revises: 0023_sprint17_assistant
Create Date: 2026-07-22

- ``op.integracao`` — uma linha por **fonte/integração** das Sprints 1 e 1B (SICONFI,
  SADIPEM, BCB, IBGE, FPM, FUNDEB, CAPAG, SIOPS, SIOPE). Config **global** do plano de
  controle (sem ``org_id``, sem RLS): o toggle ``ativo`` pausa a orquestração dos
  conectores da família. Gate default-on: ausência de linha ⇒ integração ativa.
- ``op.assinatura`` — plano/preço/métrica de cobrança da organização (RLS por ``org_id``).
- ``op.fatura`` — fatura por competência, com ``empenho_ref``/``contrato_ref`` (compra
  pública) e memória de cálculo rastreável (RLS por ``org_id``).
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_sprint18_admin_billing"
down_revision: str | None = "0023_sprint17_assistant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

_RLS_PREDICATE = (
    "coalesce(current_setting('app.is_admin', true), 'off') = 'on' "
    "OR org_id = nullif(current_setting('app.current_org_id', true), '')::uuid"
)


def upgrade() -> None:
    # --- op.integracao (config global do plano de controle; sem RLS) ---
    op.create_table(
        "integracao",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("codigo", sa.String(length=24), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("categoria", sa.String(length=24), nullable=False, server_default=sa.text("'nacional'")),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("fontes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("atualizado_por", sa.Uuid(), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("codigo", name="uq_integracao_codigo"),
        schema="op",
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON op.integracao TO {APP_ROLE}")

    # --- op.assinatura (RLS por org_id) ---
    op.create_table(
        "assinatura",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("plano", sa.Text(), nullable=False, server_default=sa.text("'padrao'")),
        sa.Column("metrica_cobranca", sa.Text(), nullable=False),
        sa.Column("preco_unitario", sa.Numeric(), nullable=False, server_default=sa.text("0")),
        sa.Column("moeda", sa.String(length=3), nullable=False, server_default=sa.text("'BRL'")),
        sa.Column("ciclo", sa.String(length=12), nullable=False, server_default=sa.text("'mensal'")),
        sa.Column("status", sa.String(length=12), nullable=False, server_default=sa.text("'ativa'")),
        sa.Column("inicio_vigencia", sa.Date(), nullable=True),
        sa.Column("fim_vigencia", sa.Date(), nullable=True),
        sa.Column("criada_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("atualizada_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["org_id"], ["op.organizacao.id"], name="fk_assinatura_org", ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('ativa', 'suspensa', 'cancelada')", name="ck_assinatura_status"),
        sa.UniqueConstraint("org_id", name="uq_assinatura_org"),
        schema="op",
    )
    op.create_index("ix_assinatura_org_id", "assinatura", ["org_id"], schema="op")
    op.execute("ALTER TABLE op.assinatura ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE op.assinatura FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY assinatura_tenant_isolation ON op.assinatura "
        f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON op.assinatura TO {APP_ROLE}")

    # --- op.fatura (RLS por org_id; expõe empenho/contrato da compra pública) ---
    op.create_table(
        "fatura",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("assinatura_id", sa.Uuid(), nullable=True),
        sa.Column("competencia", sa.String(length=7), nullable=False),  # YYYY-MM
        sa.Column("metrica_cobranca", sa.Text(), nullable=False),
        sa.Column("quantidade", sa.Numeric(), nullable=False, server_default=sa.text("0")),
        sa.Column("preco_unitario", sa.Numeric(), nullable=False, server_default=sa.text("0")),
        sa.Column("valor_total", sa.Numeric(), nullable=False, server_default=sa.text("0")),
        sa.Column("moeda", sa.String(length=3), nullable=False, server_default=sa.text("'BRL'")),
        sa.Column("status", sa.String(length=12), nullable=False, server_default=sa.text("'aberta'")),
        sa.Column("empenho_ref", sa.Text(), nullable=True),
        sa.Column("contrato_ref", sa.Text(), nullable=True),
        sa.Column("vencimento", sa.Date(), nullable=True),
        sa.Column("memoria", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("emitida_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("criada_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["org_id"], ["op.organizacao.id"], name="fk_fatura_org", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assinatura_id"], ["op.assinatura.id"], name="fk_fatura_assinatura", ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('aberta', 'paga', 'cancelada')", name="ck_fatura_status"),
        sa.UniqueConstraint("org_id", "competencia", name="uq_fatura_org_competencia"),
        schema="op",
    )
    op.create_index("ix_fatura_org_id", "fatura", ["org_id"], schema="op")
    op.create_index("ix_fatura_org_competencia", "fatura", ["org_id", "competencia"], schema="op")
    op.execute("ALTER TABLE op.fatura ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE op.fatura FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY fatura_tenant_isolation ON op.fatura "
        f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON op.fatura TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS fatura_tenant_isolation ON op.fatura")
    op.execute("ALTER TABLE op.fatura DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_fatura_org_competencia", table_name="fatura", schema="op")
    op.drop_index("ix_fatura_org_id", table_name="fatura", schema="op")
    op.drop_table("fatura", schema="op")

    op.execute("DROP POLICY IF EXISTS assinatura_tenant_isolation ON op.assinatura")
    op.execute("ALTER TABLE op.assinatura DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_assinatura_org_id", table_name="assinatura", schema="op")
    op.drop_table("assinatura", schema="op")

    op.drop_table("integracao", schema="op")
