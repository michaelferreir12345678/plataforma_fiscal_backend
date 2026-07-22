"""Sprint 10 — Restos a Pagar & Caixa: dim_fonte_recurso, fato_disponibilidade, fato_rap.

Revision ID: 0014_sprint10_cash_rap
Revises: 0013_sprint9_resultado
Create Date: 2026-07-21

Materializa a **suficiência financeira por fonte de recurso** (RGF Anexo 5 —
Demonstrativo da Disponibilidade de Caixa e dos Restos a Pagar) e o **quadro de restos a
pagar por poder/órgão** (RREO Anexo 7). A análise é sempre **fonte a fonte** (LRF art. 8º,
§ único, e art. 42): a disponibilidade vinculada a uma fonte só cobre obrigações daquela
fonte — nunca se compensa o superávit de uma fonte com o déficit de outra. ``dim_fonte_recurso``
é a hierarquia (Total → Não Vinculados / Vinculados / Vinculados ao RPPS → fonte) com a flag
``vinculada``. Tudo em reais, versionado por entrega (bitemporal).
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_sprint10_cash_rap"
down_revision: str | None = "0013_sprint9_resultado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")


def upgrade() -> None:
    # --- dim_fonte_recurso (hierárquica: Total → grupo → fonte; flag vinculada) ---
    op.execute(
        """
        CREATE TABLE gold.dim_fonte_recurso (
            codigo text PRIMARY KEY,
            descricao text NOT NULL,
            parent_codigo text REFERENCES gold.dim_fonte_recurso(codigo),
            nivel integer NOT NULL,
            path ltree NOT NULL,
            vinculada boolean NOT NULL DEFAULT false
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_dim_fonte_recurso_path ON gold.dim_fonte_recurso USING gist (path)"
    )
    op.execute(
        "CREATE INDEX ix_dim_fonte_recurso_parent ON gold.dim_fonte_recurso (parent_codigo)"
    )

    # --- fato_disponibilidade (suficiência de caixa por fonte; RGF Anexo 5) ---
    op.create_table(
        "fato_disponibilidade",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),  # RGF quadrimestral (AAAA-Qn)
        sa.Column(
            "fonte_codigo",
            sa.Text(),
            sa.ForeignKey("gold.dim_fonte_recurso.codigo"),
            nullable=False,
        ),
        sa.Column("disp_bruta", sa.Numeric(), nullable=True),  # (a)
        sa.Column("obrigacoes", sa.Numeric(), nullable=True),  # (b+c+d+e) = a − líquida antes
        sa.Column("disp_liquida_antes", sa.Numeric(), nullable=True),  # (f) antes da inscrição
        sa.Column("rpnp_exercicio", sa.Numeric(), nullable=True),  # (g) RP não proc. do exercício
        sa.Column("disp_liquida_apos", sa.Numeric(), nullable=True),  # (h) = f − g após inscrição
        sa.Column("rpnp_sem_lastro", sa.Numeric(), nullable=True),  # max(0, g − max(0, f))
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge", "periodo", "fonte_codigo", "versao_entrega",
            name="uq_fato_disponibilidade_chave",
        ),
        schema="gold",
    )
    op.create_index(
        "ix_fato_disponibilidade_ente_periodo", "fato_disponibilidade",
        ["cod_ibge", "periodo"], schema="gold",
    )

    # --- fato_rap (restos a pagar por poder/órgão; RREO Anexo 7) ---
    op.create_table(
        "fato_rap",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),  # RREO bimestral (AAAA-Bn)
        sa.Column("orgao", sa.Text(), nullable=False),  # poder/órgão (rótulo do Anexo 7)
        # Restos a pagar processados e não processados liquidados.
        sa.Column("rpp_inscritos", sa.Numeric(), nullable=True),  # (a+b)
        sa.Column("rpp_pagos", sa.Numeric(), nullable=True),  # (c)
        sa.Column("rpp_cancelados", sa.Numeric(), nullable=True),  # (d)
        sa.Column("rpp_a_pagar", sa.Numeric(), nullable=True),  # saldo (e)
        # Restos a pagar não processados.
        sa.Column("rpnp_inscritos", sa.Numeric(), nullable=True),  # (f+g)
        sa.Column("rpnp_liquidados", sa.Numeric(), nullable=True),  # (h)
        sa.Column("rpnp_pagos", sa.Numeric(), nullable=True),  # (i)
        sa.Column("rpnp_cancelados", sa.Numeric(), nullable=True),  # (j)
        sa.Column("rpnp_a_pagar", sa.Numeric(), nullable=True),  # saldo (k)
        sa.Column("saldo_total", sa.Numeric(), nullable=True),  # (L) = e + k
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge", "periodo", "orgao", "versao_entrega", name="uq_fato_rap_chave"
        ),
        schema="gold",
    )
    op.create_index(
        "ix_fato_rap_ente_periodo", "fato_rap", ["cod_ibge", "periodo"], schema="gold"
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gold TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_fato_rap_ente_periodo", table_name="fato_rap", schema="gold")
    op.drop_table("fato_rap", schema="gold")
    op.drop_index(
        "ix_fato_disponibilidade_ente_periodo", table_name="fato_disponibilidade", schema="gold"
    )
    op.drop_table("fato_disponibilidade", schema="gold")
    op.execute("DROP TABLE IF EXISTS gold.dim_fonte_recurso CASCADE")
