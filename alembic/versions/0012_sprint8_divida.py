"""Sprint 8 — Dívida: DDCL, CAPAG, credores e vencimentos SADIPEM.

Revision ID: 0012_sprint8_divida
Revises: 0011_silver_cod_conta_ordem
Create Date: 2026-07-21
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_sprint8_divida"
down_revision: str | None = "0011_silver_cod_conta_ordem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")
ARO_LIMIT_IDS = {
    "municipal": "8a8b5ea0-87a7-4f53-a01e-5ea267c20601",
    "estadual": "8a8b5ea0-87a7-4f53-a01e-5ea267c20602",
}


def upgrade() -> None:
    # A nota oficial pode ser ``n.d.`` (não disponível), além de A–D.
    op.alter_column(
        "tesouro_capag",
        "nota_final",
        existing_type=sa.String(length=2),
        type_=sa.String(length=10),
        schema="silver",
    )
    # A API SADIPEM informa a origem na operação/PVL. A Sprint 1B ainda não a
    # preservava, mas ela é indispensável ao drill origem → credor.
    op.add_column(
        "sadipem_op_contratada",
        sa.Column("tipo_operacao", sa.Text(), nullable=True),
        schema="silver",
    )

    op.create_table(
        "dim_origem_divida",
        sa.Column("codigo", sa.Text(), primary_key=True),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column(
            "parent_codigo",
            sa.Text(),
            sa.ForeignKey("gold.dim_origem_divida.codigo"),
            nullable=True,
        ),
        sa.Column("nivel", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        schema="gold",
    )
    op.execute("ALTER TABLE gold.dim_origem_divida ALTER COLUMN path TYPE ltree USING path::ltree")
    op.execute("CREATE INDEX ix_dim_origem_divida_path ON gold.dim_origem_divida USING gist (path)")
    op.create_index(
        "ix_dim_origem_divida_parent",
        "dim_origem_divida",
        ["parent_codigo"],
        schema="gold",
    )

    op.create_table(
        "dim_credor",
        sa.Column("codigo", sa.Text(), primary_key=True),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column(
            "parent_codigo",
            sa.Text(),
            sa.ForeignKey("gold.dim_credor.codigo"),
            nullable=True,
        ),
        sa.Column("nivel", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("origem", sa.String(length=20), nullable=True),
        schema="gold",
    )
    op.execute("ALTER TABLE gold.dim_credor ALTER COLUMN path TYPE ltree USING path::ltree")
    op.execute("CREATE INDEX ix_dim_credor_path ON gold.dim_credor USING gist (path)")
    op.create_index("ix_dim_credor_parent", "dim_credor", ["parent_codigo"], schema="gold")

    op.create_table(
        "fato_divida",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("dc_bruta", sa.Numeric(), nullable=False),
        sa.Column("disponibilidades", sa.Numeric(), nullable=False),
        sa.Column("haveres", sa.Numeric(), nullable=False),
        sa.Column("dcl", sa.Numeric(), nullable=False),
        sa.Column("dcl_reportada", sa.Numeric(), nullable=True),
        sa.Column("diferenca_reconciliacao", sa.Numeric(), nullable=True),
        sa.Column("rcl_ajustada", sa.Numeric(), nullable=True),
        sa.Column("pct_rcl", sa.Numeric(), nullable=True),
        sa.Column("saldo_interno", sa.Numeric(), nullable=True),
        sa.Column("saldo_externo", sa.Numeric(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.UniqueConstraint("cod_ibge", "periodo", "versao_entrega", name="uq_fato_divida_chave"),
        schema="gold",
    )
    op.create_index(
        "ix_fato_divida_ente_periodo",
        "fato_divida",
        ["cod_ibge", "periodo"],
        schema="gold",
    )

    op.create_table(
        "fato_capag",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("ano_ref", sa.Integer(), nullable=False),
        sa.Column("nota_final", sa.String(length=10), nullable=True),
        sa.Column("ind_endividamento", sa.Numeric(), nullable=True),
        sa.Column("ind_poupanca", sa.Numeric(), nullable=True),
        sa.Column("ind_liquidez", sa.Numeric(), nullable=True),
        sa.Column("metodologia_versao", sa.Text(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.UniqueConstraint("cod_ibge", "ano_ref", "versao_entrega", name="uq_fato_capag_chave"),
        schema="gold",
    )
    op.create_index(
        "ix_fato_capag_ente_ano",
        "fato_capag",
        ["cod_ibge", "ano_ref"],
        schema="gold",
    )

    op.create_table(
        "fato_vencimento",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo_ref", sa.Text(), nullable=False),
        sa.Column("id_operacao", sa.Text(), nullable=False),
        sa.Column(
            "credor_codigo",
            sa.Text(),
            sa.ForeignKey("gold.dim_credor.codigo"),
            nullable=True,
        ),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("mes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("principal", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("juros", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("encargos", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("valor", sa.Numeric(), nullable=False),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge",
            "periodo_ref",
            "id_operacao",
            "ano",
            "mes",
            "versao_entrega",
            name="uq_fato_vencimento_chave",
        ),
        schema="gold",
    )
    op.create_index(
        "ix_fato_vencimento_ente_ano",
        "fato_vencimento",
        ["cod_ibge", "ano"],
        schema="gold",
    )

    # Resolução SF 43/2001: ARO tem teto próprio de 7% da RCL. É dado de
    # referência por esfera, assim como os demais limites — nunca constante do request.
    # IDs fixos permitem que o downgrade remova somente o que esta revisão
    # criou. O conflito natural torna o seed compatível com catálogos que já
    # possuam o limite ARO.
    insert_aro = sa.text(
        """
        INSERT INTO gold.dim_limite_legal
            (id, indicador, esfera, poder, sentido, teto_pct, alerta_pct, prudencial_pct)
        VALUES
            (CAST(:id AS uuid), 'aro', :esfera, '', 'teto', 7, 6.3, 6.65)
        ON CONFLICT (indicador, esfera, poder) DO NOTHING
        """
    )
    bind = op.get_bind()
    for esfera, limite_id in ARO_LIMIT_IDS.items():
        bind.execute(insert_aro, {"id": limite_id, "esfera": esfera})

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gold TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA silver TO {APP_ROLE}")


def downgrade() -> None:
    delete_aro = sa.text(
        """
        DELETE FROM gold.dim_limite_legal
        WHERE id IN (CAST(:municipal AS uuid), CAST(:estadual AS uuid))
        """
    )
    op.get_bind().execute(delete_aro, ARO_LIMIT_IDS)
    op.drop_index("ix_fato_vencimento_ente_ano", table_name="fato_vencimento", schema="gold")
    op.drop_table("fato_vencimento", schema="gold")
    op.drop_index("ix_fato_capag_ente_ano", table_name="fato_capag", schema="gold")
    op.drop_table("fato_capag", schema="gold")
    op.drop_index("ix_fato_divida_ente_periodo", table_name="fato_divida", schema="gold")
    op.drop_table("fato_divida", schema="gold")
    op.drop_index("ix_dim_credor_parent", table_name="dim_credor", schema="gold")
    op.execute("DROP INDEX IF EXISTS gold.ix_dim_credor_path")
    op.drop_table("dim_credor", schema="gold")
    op.drop_index("ix_dim_origem_divida_parent", table_name="dim_origem_divida", schema="gold")
    op.execute("DROP INDEX IF EXISTS gold.ix_dim_origem_divida_path")
    op.drop_table("dim_origem_divida", schema="gold")
    op.drop_column("sadipem_op_contratada", "tipo_operacao", schema="silver")
    op.alter_column(
        "tesouro_capag",
        "nota_final",
        existing_type=sa.String(length=10),
        type_=sa.String(length=2),
        postgresql_using="left(nota_final, 2)",
        schema="silver",
    )
