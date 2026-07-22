"""Sprint 0 — fundação multi-tenant (schema op, RLS por org_id).

Revision ID: 0001_sprint0_tenancy
Revises:
Create Date: 2026-07-03
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_sprint0_tenancy"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Role de aplicação (não-superuser) que sofrerá as policies de RLS.
APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

# Tabelas do plano de dados isoladas por org_id.
TENANT_TABLES = ("papel", "membership", "carteira_ente", "audit_log")

TIPOS_CONTA = ("prefeitura", "estado", "consultoria")
CAPACIDADES = ("ver", "exportar", "config_alerta", "gerar_relatorio", "usar_ia", "administrar")

_RLS_PREDICATE = (
    "coalesce(current_setting('app.is_admin', true), 'off') = 'on' "
    "OR org_id = nullif(current_setting('app.current_org_id', true), '')::uuid"
)


def _in_list(col: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{col} in ({joined})"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS ltree")
    op.execute("CREATE SCHEMA IF NOT EXISTS op")

    op.create_table(
        "organizacao",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("tipo_conta", sa.String(length=20), nullable=False),
        sa.Column("metrica_cobranca", sa.Text(), nullable=True),
        sa.Column("criada_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_in_list("tipo_conta", TIPOS_CONTA), name="ck_organizacao_tipo_conta"),
        schema="op",
    )

    op.create_table(
        "usuario",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("senha_hash", sa.Text(), nullable=False),
        sa.Column("mfa_ativo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.UniqueConstraint("email", name="uq_usuario_email"),
        schema="op",
    )

    op.create_table(
        "papel",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["op.organizacao.id"], name="fk_papel_org", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("org_id", "nome", name="uq_papel_org_nome"),
        schema="op",
    )
    op.create_index("ix_papel_org_id", "papel", ["org_id"], schema="op")

    op.create_table(
        "papel_permissao",
        sa.Column("papel_id", sa.Uuid(), primary_key=True),
        sa.Column("capacidade", sa.String(length=20), primary_key=True),
        sa.ForeignKeyConstraint(
            ["papel_id"], ["op.papel.id"], name="fk_papel_permissao_papel", ondelete="CASCADE"
        ),
        sa.CheckConstraint(_in_list("capacidade", CAPACIDADES), name="ck_papel_permissao_capacidade"),
        schema="op",
    )

    op.create_table(
        "membership",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("usuario_id", sa.Uuid(), nullable=False),
        sa.Column("papel_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["op.organizacao.id"], name="fk_membership_org", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["op.usuario.id"], name="fk_membership_usuario", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["papel_id"], ["op.papel.id"], name="fk_membership_papel", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("org_id", "usuario_id", name="uq_membership_org_usuario"),
        schema="op",
    )
    op.create_index("ix_membership_org_id", "membership", ["org_id"], schema="op")
    op.create_index("ix_membership_usuario_id", "membership", ["usuario_id"], schema="op")

    op.create_table(
        "membership_escopo",
        sa.Column("membership_id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), primary_key=True),
        sa.ForeignKeyConstraint(
            ["membership_id"], ["op.membership.id"], name="fk_membership_escopo_membership",
            ondelete="CASCADE",
        ),
        schema="op",
    )

    op.create_table(
        "carteira_ente",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("grupo", sa.Text(), nullable=True),
        sa.Column("tag", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"], ["op.organizacao.id"], name="fk_carteira_ente_org", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("org_id", "cod_ibge", name="uq_carteira_ente_org_ente"),
        schema="op",
    )
    op.create_index("ix_carteira_ente_org_id", "carteira_ente", ["org_id"], schema="op")

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("usuario_id", sa.Uuid(), nullable=True),
        sa.Column("acao", sa.Text(), nullable=False),
        sa.Column("recurso", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["op.organizacao.id"], name="fk_audit_log_org", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["op.usuario.id"], name="fk_audit_log_usuario", ondelete="SET NULL"
        ),
        schema="op",
    )
    op.create_index("ix_audit_log_org_id", "audit_log", ["org_id"], schema="op")

    # --- Row-Level Security por org_id (plano de dados) ---
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE op.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE op.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON op.{table} "
            f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
        )

    # --- Grants para a role de aplicação (sujeita a RLS) ---
    op.execute(f"GRANT USAGE ON SCHEMA op TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA op TO {APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA op "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON op.{table}")
        op.execute(f"ALTER TABLE op.{table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_audit_log_org_id", table_name="audit_log", schema="op")
    op.drop_table("audit_log", schema="op")
    op.drop_index("ix_carteira_ente_org_id", table_name="carteira_ente", schema="op")
    op.drop_table("carteira_ente", schema="op")
    op.drop_table("membership_escopo", schema="op")
    op.drop_index("ix_membership_usuario_id", table_name="membership", schema="op")
    op.drop_index("ix_membership_org_id", table_name="membership", schema="op")
    op.drop_table("membership", schema="op")
    op.drop_table("papel_permissao", schema="op")
    op.drop_index("ix_papel_org_id", table_name="papel", schema="op")
    op.drop_table("papel", schema="op")
    op.drop_table("usuario", schema="op")
    op.drop_table("organizacao", schema="op")
