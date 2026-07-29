"""Sprint 26 — qualidade do dado e lineage explícito.

Até aqui as invariantes fiscais só existiam **dentro dos testes**: se um dado chegasse
corrompido em produção, nada no sistema perguntaria "os filhos somam o pai?" ou "a RCL
que eu calculei bate com a que o ente publicou?". E o caminho fonte→página só existia
implícito no código — ninguém conseguia responder "o que quebra se o RGF falhar?".

Duas tabelas:

- ``gold.data_quality_check`` — **resultado** de cada verificação, com os dois lados da
  comparação, a diferença e a tolerância aplicada. Guardar os lados (e não só um booleano)
  é o que permite ao gestor discordar do check: ele vê a conta.
- ``gold.lineage_edge`` — o grafo fonte→bronze→silver→gold→endpoint→página, mantido por
  código (seed) e consultável nos dois sentidos.

Ambas são **gold**: qualidade e linhagem descrevem o dado público compartilhado, não o
operacional de uma organização.

Revision ID: 0033_sprint26_qualidade_lineage
Revises: 0032_sprint25e_alerta_resolucao
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_sprint26_qualidade_lineage"
down_revision: str | None = "0032_sprint25e_alerta_resolucao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_quality_check",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), nullable=True, comment="ingest_job que disparou (24)."),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("cod_ibge", sa.String(length=7), nullable=True, comment="NULL = check global."),
        sa.Column("periodo", sa.Text(), nullable=True),
        sa.Column("check_codigo", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("esquerda", sa.Numeric(), nullable=True),
        sa.Column("direita", sa.Numeric(), nullable=True),
        sa.Column("diferenca", sa.Numeric(), nullable=True),
        sa.Column("tolerancia", sa.Numeric(), nullable=True),
        sa.Column("detalhe", postgresql.JSONB(), nullable=True),
        sa.Column(
            "executado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'aviso', 'falha')", name="ck_data_quality_check_status"
        ),
        # Reexecutar o mesmo check no mesmo recorte atualiza a linha em vez de empilhar
        # histórico infinito: o que interessa é o estado corrente + o alerta que ele gera.
        sa.UniqueConstraint(
            "check_codigo", "fonte", "cod_ibge", "periodo", name="uq_data_quality_check_chave"
        ),
        schema="gold",
    )
    op.create_index(
        "ix_data_quality_check_status",
        "data_quality_check",
        ["status", "executado_em"],
        schema="gold",
    )
    op.create_index(
        "ix_data_quality_check_fonte", "data_quality_check", ["fonte"], schema="gold"
    )

    op.create_table(
        "lineage_edge",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("origem", sa.Text(), nullable=False),
        sa.Column("destino", sa.Text(), nullable=False),
        sa.Column("tipo", sa.String(length=24), nullable=False),
        sa.Column("detalhe", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint(
            "tipo IN ('fonte_bronze', 'bronze_silver', 'silver_gold', 'gold_endpoint', "
            "'endpoint_pagina')",
            name="ck_lineage_edge_tipo",
        ),
        sa.UniqueConstraint("origem", "destino", "tipo", name="uq_lineage_edge_chave"),
        schema="gold",
    )
    op.create_index("ix_lineage_edge_origem", "lineage_edge", ["origem"], schema="gold")
    op.create_index("ix_lineage_edge_destino", "lineage_edge", ["destino"], schema="gold")


def downgrade() -> None:
    op.drop_index("ix_lineage_edge_destino", table_name="lineage_edge", schema="gold")
    op.drop_index("ix_lineage_edge_origem", table_name="lineage_edge", schema="gold")
    op.drop_table("lineage_edge", schema="gold")
    op.drop_index("ix_data_quality_check_fonte", table_name="data_quality_check", schema="gold")
    op.drop_index("ix_data_quality_check_status", table_name="data_quality_check", schema="gold")
    op.drop_table("data_quality_check", schema="gold")
