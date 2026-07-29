"""Sprint 25B — meta fiscal da LDO cadastrada pela organização (decisão §11.5 da auditoria).

``op.meta_fiscal`` — meta do resultado primário/nominal **declarada pelo gestor** quando o
ente não a publicou no RREO Anexo 6. É dado da organização (RLS por ``org_id``), nunca dado
oficial: a resposta sempre diz a origem, e a meta manual **não entra em agregados**
(carteira, consolidado de UF) nem em relatório institucional — decisão do dono do produto.

Chave: ``(org_id, cod_ibge, exercicio, indicador)`` — uma meta por exercício e indicador.
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_sprint25b_meta_fiscal"
down_revision: str | None = "0029_sprint24_job_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

_RLS_PREDICATE = (
    "coalesce(current_setting('app.is_admin', true), 'off') = 'on' "
    "OR org_id = nullif(current_setting('app.current_org_id', true), '')::uuid"
)


def upgrade() -> None:
    op.create_table(
        "meta_fiscal",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("exercicio", sa.Integer(), nullable=False),
        sa.Column("indicador", sa.String(length=16), nullable=False),  # primario | nominal
        sa.Column("valor", sa.Numeric(), nullable=False),
        # De onde o gestor tirou o número (ex.: "LDO 2024, art. 3º, Anexo de Metas").
        sa.Column("fonte_declarada", sa.Text(), nullable=False),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("criado_por", sa.Uuid(), nullable=True),
        sa.Column("atualizado_por", sa.Uuid(), nullable=True),
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
            ["org_id"], ["op.organizacao.id"], name="fk_meta_fiscal_org", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "indicador IN ('primario', 'nominal')", name="ck_meta_fiscal_indicador"
        ),
        sa.CheckConstraint("exercicio BETWEEN 1990 AND 2200", name="ck_meta_fiscal_exercicio"),
        sa.UniqueConstraint(
            "org_id", "cod_ibge", "exercicio", "indicador", name="uq_meta_fiscal_chave"
        ),
        schema="op",
    )
    op.create_index("ix_meta_fiscal_org_id", "meta_fiscal", ["org_id"], schema="op")
    op.create_index(
        "ix_meta_fiscal_ente",
        "meta_fiscal",
        ["org_id", "cod_ibge", "exercicio"],
        schema="op",
    )

    op.execute("ALTER TABLE op.meta_fiscal ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE op.meta_fiscal FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY meta_fiscal_tenant_isolation ON op.meta_fiscal "
        f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON op.meta_fiscal TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS meta_fiscal_tenant_isolation ON op.meta_fiscal")
    op.execute("ALTER TABLE op.meta_fiscal DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_meta_fiscal_ente", table_name="meta_fiscal", schema="op")
    op.drop_index("ix_meta_fiscal_org_id", table_name="meta_fiscal", schema="op")
    op.drop_table("meta_fiscal", schema="op")
