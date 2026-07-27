"""Sprint 16 — Relatórios & Exportação (Módulo 16).

Revision ID: 0022_sprint16_reports
Revises: 0021_sprint15_alerts
Create Date: 2026-07-22

Os relatórios e agendamentos são dados privados do tenant. Ambos usam RLS por
``org_id``; os artefatos ficam fora do banco, enquanto proveniência, memória de
cálculo, hash e avisos de incompletude permanecem persistidos e auditáveis.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_sprint16_reports"
down_revision: str | None = "0021_sprint15_alerts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

_RLS_PREDICATE = (
    "coalesce(current_setting('app.is_admin', true), 'off') = 'on' "
    "OR org_id = nullif(current_setting('app.current_org_id', true), '')::uuid"
)


def upgrade() -> None:
    op.create_table(
        "relatorio",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("lote_id", sa.Uuid(), nullable=False),
        sa.Column("modelo", sa.String(length=32), nullable=False),
        sa.Column("modelo_versao", sa.String(length=16), nullable=False, server_default="v1"),
        sa.Column("formato", sa.String(length=8), nullable=False),
        sa.Column("escopo", sa.String(length=12), nullable=False),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="enfileirado"),
        sa.Column("progresso", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "parametros", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "cabecalho", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "source_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "memoria", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "dados_incompletos",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("arquivo_nome", sa.Text(), nullable=True),
        sa.Column("arquivo_path", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("tamanho_bytes", sa.BigInteger(), nullable=True),
        sa.Column("conteudo_hash", sa.String(length=64), nullable=True),
        sa.Column("gerado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("criado_por", sa.Uuid(), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["op.organizacao.id"], name="fk_relatorio_org", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["criado_por"], ["op.usuario.id"], name="fk_relatorio_usuario", ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "modelo IN ('executivo', 'limites', 'comparativo', 'conformidade', 'boletim')",
            name="ck_relatorio_modelo",
        ),
        sa.CheckConstraint("formato IN ('pdf', 'xlsx', 'pptx')", name="ck_relatorio_formato"),
        sa.CheckConstraint("escopo IN ('ente', 'lote', 'estadual')", name="ck_relatorio_escopo"),
        sa.CheckConstraint(
            "status IN ('enfileirado', 'processando', 'gerado', 'parcial', 'falhou')",
            name="ck_relatorio_status",
        ),
        sa.CheckConstraint("progresso BETWEEN 0 AND 100", name="ck_relatorio_progresso"),
        sa.UniqueConstraint("lote_id", "cod_ibge", name="uq_relatorio_lote_ente"),
        schema="op",
    )
    op.create_index("ix_relatorio_org_id", "relatorio", ["org_id"], schema="op")
    op.create_index("ix_relatorio_lote", "relatorio", ["org_id", "lote_id"], schema="op")
    op.create_index("ix_relatorio_fila", "relatorio", ["status", "criado_em"], schema="op")

    op.create_table(
        "relatorio_agendamento",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("modelo", sa.String(length=32), nullable=False),
        sa.Column("formato", sa.String(length=8), nullable=False),
        sa.Column("escopo", sa.String(length=12), nullable=False),
        sa.Column(
            "entes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("periodicidade", sa.String(length=16), nullable=False),
        sa.Column(
            "parametros", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("proxima_execucao", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ultima_execucao", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("criado_por", sa.Uuid(), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["op.organizacao.id"],
            name="fk_relatorio_agendamento_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["criado_por"],
            ["op.usuario.id"],
            name="fk_relatorio_agendamento_usuario",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "modelo IN ('executivo', 'limites', 'comparativo', 'conformidade', 'boletim')",
            name="ck_relatorio_agendamento_modelo",
        ),
        sa.CheckConstraint(
            "formato IN ('pdf', 'xlsx', 'pptx')", name="ck_relatorio_agendamento_formato"
        ),
        sa.CheckConstraint(
            "escopo IN ('ente', 'lote', 'estadual')", name="ck_relatorio_agendamento_escopo"
        ),
        sa.CheckConstraint(
            "periodicidade IN ('diario', 'semanal', 'mensal', 'bimestral')",
            name="ck_relatorio_agendamento_periodicidade",
        ),
        schema="op",
    )
    op.create_index(
        "ix_relatorio_agendamento_org_id", "relatorio_agendamento", ["org_id"], schema="op"
    )
    op.create_index(
        "ix_relatorio_agendamento_fila",
        "relatorio_agendamento",
        ["ativo", "proxima_execucao"],
        schema="op",
    )

    for tabela in ("relatorio", "relatorio_agendamento"):
        op.execute(f"ALTER TABLE op.{tabela} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE op.{tabela} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {tabela}_tenant_isolation ON op.{tabela} "
            f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON op.{tabela} TO {APP_ROLE}")


def downgrade() -> None:
    for tabela in ("relatorio_agendamento", "relatorio"):
        op.execute(f"DROP POLICY IF EXISTS {tabela}_tenant_isolation ON op.{tabela}")
        op.execute(f"ALTER TABLE op.{tabela} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_relatorio_agendamento_fila", table_name="relatorio_agendamento", schema="op")
    op.drop_index(
        "ix_relatorio_agendamento_org_id", table_name="relatorio_agendamento", schema="op"
    )
    op.drop_table("relatorio_agendamento", schema="op")

    op.drop_index("ix_relatorio_fila", table_name="relatorio", schema="op")
    op.drop_index("ix_relatorio_lote", table_name="relatorio", schema="op")
    op.drop_index("ix_relatorio_org_id", table_name="relatorio", schema="op")
    op.drop_table("relatorio", schema="op")
