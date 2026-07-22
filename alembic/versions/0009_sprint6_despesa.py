"""Sprint 6 — gold: dim_funcao e dim_natureza (duas hierarquias ltree) e fato_despesa.

Revision ID: 0009_sprint6_despesa
Revises: 0008_sprint5_receita
Create Date: 2026-07-21

O ``fato_despesa`` guarda **um eixo por linha**: a linha do RREO Anexo 02 (por função)
grava ``funcao_codigo`` real e ``natureza_codigo = '*'`` (sentinela "todas as naturezas");
a linha do Anexo 01 (por natureza da despesa) grava ``natureza_codigo`` real e
``funcao_codigo = '*'``. As sentinelas satisfazem as FKs sem NULL, mantendo a chave
única (``NULL`` seria distinto e quebraria a idempotência do upsert).
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_sprint6_despesa"
down_revision: str | None = "0008_sprint5_receita"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")


def _create_dim(nome: str) -> None:
    """Cria uma dimensão hierárquica (ltree) no padrão §6.1."""
    op.execute(
        f"""
        CREATE TABLE gold.{nome} (
            codigo text PRIMARY KEY,
            descricao text NOT NULL,
            parent_codigo text REFERENCES gold.{nome}(codigo),
            nivel integer NOT NULL,
            path ltree NOT NULL
        )
        """
    )
    op.execute(f"CREATE INDEX ix_{nome}_path ON gold.{nome} USING gist (path)")
    op.execute(f"CREATE INDEX ix_{nome}_parent ON gold.{nome} (parent_codigo)")


def upgrade() -> None:
    # --- duas hierarquias ltree ---
    # dim_funcao:   Função → Subfunção (RREO Anexo 02; Portaria MOG 42/1999).
    # dim_natureza: Categoria Econômica → Grupo → Modalidade → Elemento (RREO Anexo 01).
    _create_dim("dim_funcao")
    _create_dim("dim_natureza")

    # --- fato_despesa (estágios da despesa; um eixo por linha, ver docstring) ---
    op.create_table(
        "fato_despesa",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column(
            "funcao_codigo",
            sa.Text(),
            sa.ForeignKey("gold.dim_funcao.codigo"),
            nullable=False,
        ),
        sa.Column(
            "natureza_codigo",
            sa.Text(),
            sa.ForeignKey("gold.dim_natureza.codigo"),
            nullable=False,
        ),
        sa.Column("dotacao_inicial", sa.Numeric(), nullable=True),
        sa.Column("dotacao_atualizada", sa.Numeric(), nullable=True),
        sa.Column("empenhado", sa.Numeric(), nullable=True),
        sa.Column("liquidado", sa.Numeric(), nullable=True),
        sa.Column("pago", sa.Numeric(), nullable=True),
        sa.Column("inscrito_rap", sa.Numeric(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge",
            "periodo",
            "funcao_codigo",
            "natureza_codigo",
            "versao_entrega",
            name="uq_fato_despesa_chave",
        ),
        schema="gold",
    )
    op.create_index(
        "ix_fato_despesa_ente_periodo", "fato_despesa", ["cod_ibge", "periodo"], schema="gold"
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gold TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_fato_despesa_ente_periodo", table_name="fato_despesa", schema="gold")
    op.drop_table("fato_despesa", schema="gold")
    op.execute("DROP TABLE IF EXISTS gold.dim_natureza CASCADE")
    op.execute("DROP TABLE IF EXISTS gold.dim_funcao CASCADE")
