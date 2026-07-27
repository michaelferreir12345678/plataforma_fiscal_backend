"""Sprint 17 — Assistente de IA (Módulo 15) · Gemini.

Revision ID: 0023_sprint17_assistant
Revises: 0022_sprint16_reports
Create Date: 2026-07-22

- ``gold.norma_chunk`` — corpo normativo (LRF/CF/MDF) fatiado em dispositivos com
  embedding para recuperação semântica (RAG). Dado **público/compartilhado** (sem RLS).
  O embedding é gravado num formato **portável** (``JSONB`` de floats) para rodar em
  qualquer Postgres; em produção a coluna migra para ``vector`` (pgvector) com índice
  ivfflat sem mudar o contrato do recuperador (o backend de similaridade é abstraído em
  ``assistant.retriever``). ``modelo_embedding``/``dim`` guardam a versão do embedder.
- ``op.conversa`` — histórico de conversas do assistente (privado da organização, RLS).
- ``op.conversa_uso`` — telemetria por chamada (tokens/latência/modelo). Alimenta a cota
  "Consultas IA/mês" do plano (Sprint 18) e o ``GET /platform/uso`` (Sprint 19).
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_sprint17_assistant"
down_revision: str | None = "0022_sprint16_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

_RLS_PREDICATE = (
    "coalesce(current_setting('app.is_admin', true), 'off') = 'on' "
    "OR org_id = nullif(current_setting('app.current_org_id', true), '')::uuid"
)


def upgrade() -> None:
    # --- gold.norma_chunk (compartilhado, sem RLS) ---
    op.create_table(
        "norma_chunk",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fonte", sa.String(length=8), nullable=False),
        sa.Column("dispositivo", sa.Text(), nullable=False),
        sa.Column("titulo", sa.Text(), nullable=True),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("indicadores", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("modelo_embedding", sa.Text(), nullable=True),
        sa.Column("dim", sa.Integer(), nullable=True),
        # Embedding portável (pgvector-ready): lista de floats em JSONB.
        sa.Column("embedding", postgresql.JSONB(), nullable=True),
        sa.Column("source_ref", postgresql.JSONB(), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("fonte IN ('LRF', 'CF', 'MDF')", name="ck_norma_chunk_fonte"),
        sa.UniqueConstraint("fonte", "dispositivo", name="uq_norma_chunk_fonte_dispositivo"),
        schema="gold",
    )
    op.create_index("ix_norma_chunk_fonte", "norma_chunk", ["fonte"], schema="gold")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON gold.norma_chunk TO {APP_ROLE}")

    # --- op.conversa (privado do tenant, RLS por org_id) ---
    op.create_table(
        "conversa",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("usuario_id", sa.Uuid(), nullable=True),
        sa.Column("tipo", sa.String(length=24), nullable=False, server_default=sa.text("'pergunta'")),
        sa.Column("cod_ibge", sa.String(length=7), nullable=True),
        sa.Column("periodo", sa.Text(), nullable=True),
        sa.Column("pergunta", sa.Text(), nullable=False),
        sa.Column("resposta", sa.Text(), nullable=False),
        sa.Column("recusa", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("dado_disponivel", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("modelo", sa.Text(), nullable=True),
        sa.Column("fontes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("fatos", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("dados_incompletos", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["op.organizacao.id"], name="fk_conversa_org", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["op.usuario.id"], name="fk_conversa_usuario", ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "tipo IN ('pergunta', 'resumo_executivo')", name="ck_conversa_tipo"
        ),
        schema="op",
    )
    op.create_index("ix_conversa_org_id", "conversa", ["org_id"], schema="op")
    op.create_index(
        "ix_conversa_org_criado", "conversa", ["org_id", "criado_em"], schema="op"
    )
    op.execute("ALTER TABLE op.conversa ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE op.conversa FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY conversa_tenant_isolation ON op.conversa "
        f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON op.conversa TO {APP_ROLE}")

    # --- op.conversa_uso (privado do tenant, RLS por org_id) ---
    op.create_table(
        "conversa_uso",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("conversa_id", sa.Uuid(), nullable=True),
        sa.Column("modelo", sa.Text(), nullable=False),
        sa.Column("tokens_entrada", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_saida", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("latencia_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["org_id"], ["op.organizacao.id"], name="fk_conversa_uso_org", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversa_id"], ["op.conversa.id"], name="fk_conversa_uso_conversa", ondelete="CASCADE"
        ),
        schema="op",
    )
    op.create_index("ix_conversa_uso_org_id", "conversa_uso", ["org_id"], schema="op")
    op.create_index("ix_conversa_uso_org_ts", "conversa_uso", ["org_id", "ts"], schema="op")
    op.execute("ALTER TABLE op.conversa_uso ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE op.conversa_uso FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY conversa_uso_tenant_isolation ON op.conversa_uso "
        f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON op.conversa_uso TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS conversa_uso_tenant_isolation ON op.conversa_uso")
    op.execute("ALTER TABLE op.conversa_uso DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_conversa_uso_org_ts", table_name="conversa_uso", schema="op")
    op.drop_index("ix_conversa_uso_org_id", table_name="conversa_uso", schema="op")
    op.drop_table("conversa_uso", schema="op")

    op.execute("DROP POLICY IF EXISTS conversa_tenant_isolation ON op.conversa")
    op.execute("ALTER TABLE op.conversa DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_conversa_org_criado", table_name="conversa", schema="op")
    op.drop_index("ix_conversa_org_id", table_name="conversa", schema="op")
    op.drop_table("conversa", schema="op")

    op.drop_index("ix_norma_chunk_fonte", table_name="norma_chunk", schema="gold")
    op.drop_table("norma_chunk", schema="gold")
