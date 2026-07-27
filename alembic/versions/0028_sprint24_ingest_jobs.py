"""Sprint 24 — Central de Dados: fila de jobs de ingestão (assíncrona) + lineage.

Torna a operação da ingestão (run/backfill/replay) um **job assíncrono** rastreável, em vez
de uma chamada síncrona que estoura timeout. Cria ``op.ingest_job`` (privado do tenant, RLS
por ``org_id``) com progresso, tentativas, erro, log e resultado; e liga a execução ao
resultado dando a ``gold.ingestion_log`` a coluna ``job_id`` (lineage execução→dado).

Revision ID: 0028_sprint24_ingest_jobs
Revises: 0027_sprint23_estadual
Create Date: 2026-07-26
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_sprint24_ingest_jobs"
down_revision: str | None = "0027_sprint23_estadual"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

_RLS_PREDICATE = (
    "coalesce(current_setting('app.is_admin', true), 'off') = 'on' "
    "OR org_id = nullif(current_setting('app.current_org_id', true), '')::uuid"
)


def upgrade() -> None:
    # --- op.ingest_job: fila de execução da ingestão (RLS por org) ---
    op.create_table(
        "ingest_job",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("criado_por", sa.Uuid(), nullable=True),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("tipo", sa.String(length=16), nullable=False),
        sa.Column(
            "entes",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "periodos",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("parametros", postgresql.JSONB(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'na_fila'")
        ),
        sa.Column("progresso_pct", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("itens_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("itens_ok", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("itens_erro", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("erro_resumo", sa.Text(), nullable=True),
        sa.Column("log_ref", sa.Text(), nullable=True),
        sa.Column("resultado", postgresql.JSONB(), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminado_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"], ["op.organizacao.id"], name="fk_ingest_job_org", ondelete="CASCADE"
        ),
        sa.CheckConstraint("tipo IN ('run', 'backfill', 'replay')", name="ck_ingest_job_tipo"),
        sa.CheckConstraint(
            "status IN ('na_fila', 'executando', 'concluido', 'falhou', 'cancelado')",
            name="ck_ingest_job_status",
        ),
        schema="op",
    )
    op.create_index("ix_ingest_job_org_id", "ingest_job", ["org_id"], schema="op")
    op.create_index(
        "ix_ingest_job_fila", "ingest_job", ["org_id", "status", "criado_em"], schema="op"
    )

    op.execute("ALTER TABLE op.ingest_job ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE op.ingest_job FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY ingest_job_tenant_isolation ON op.ingest_job "
        f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON op.ingest_job TO {APP_ROLE}")

    # --- lineage: cada linha de ingestion_log aponta para o job que a produziu ---
    op.add_column(
        "ingestion_log",
        sa.Column("job_id", sa.Uuid(), nullable=True),
        schema="gold",
    )
    op.create_index("ix_ingestion_log_job", "ingestion_log", ["job_id"], schema="gold")


def downgrade() -> None:
    op.drop_index("ix_ingestion_log_job", table_name="ingestion_log", schema="gold")
    op.drop_column("ingestion_log", "job_id", schema="gold")

    op.execute("DROP POLICY IF EXISTS ingest_job_tenant_isolation ON op.ingest_job")
    op.execute("ALTER TABLE op.ingest_job DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_ingest_job_fila", table_name="ingest_job", schema="op")
    op.drop_index("ix_ingest_job_org_id", table_name="ingest_job", schema="op")
    op.drop_table("ingest_job", schema="op")
