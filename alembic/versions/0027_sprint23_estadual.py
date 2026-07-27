"""Sprint 23 — Visão Estadual & Consolidação Territorial da UF.

Separa os dois conceitos estaduais: o **ente estadual** (dados do Governo do Estado,
já servido por ``/entes/{ibge}``) e o **consolidado territorial** (agregado dos
municípios da UF). O consolidado é SEMPRE ``Σnumerador/Σdenominador`` (nunca média de
percentuais) e carrega a cobertura como dado (quantos entes têm dado, quais faltam,
% e a marca de períodos mistos). Genérico para qualquer UF; o Ceará é o caso 1.

Cria:
- ``gold.mart_consolidado_uf`` — o consolidado materializado por (uf, período, indicador,
  versão de cálculo), com numerador/denominador/percentual e a cobertura explícita.
- ``gold.dim_regiao_uf`` — regiões da UF como DADO (nome + lista de municípios), para o
  drill UF→região→município (§6.1). Semeada da malha oficial do IBGE.
- ``gold.geo_malha_uf`` — a malha municipal real (GeoJSON do IBGE) por UF, para o mapa
  coroplético; servida por ``GET /geo/malha/{uf}``.

Revision ID: 0027_sprint23_estadual
Revises: 0026_sprint20_catalogo_fonte
Create Date: 2026-07-23
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_sprint23_estadual"
down_revision: str | None = "0026_sprint20_catalogo_fonte"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")


def upgrade() -> None:
    # --- gold.mart_consolidado_uf: consolidação territorial (Σnum/Σden, nunca média) ---
    op.create_table(
        "mart_consolidado_uf",
        sa.Column("uf", sa.String(length=2), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("indicador", sa.Text(), nullable=False),
        # Σ dos numeradores (ex.: despesa de pessoal) e Σ dos denominadores (ex.: RCL) dos
        # entes que TÊM o indicador. valor_pct = numerador/denominador (não a média dos %).
        sa.Column("numerador", sa.Numeric(), nullable=True),
        sa.Column("denominador", sa.Numeric(), nullable=True),
        sa.Column("valor_pct", sa.Numeric(), nullable=True),
        sa.Column("n_entes_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("n_entes_com_dado", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cobertura_pct", sa.Numeric(), nullable=True),
        sa.Column(
            "entes_ausentes",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("periodos_mistos", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # v1 = indicadores aditivos seguros; documenta as exclusões intra-governamentais.
        sa.Column("versao_calculo", sa.Text(), nullable=False, server_default=sa.text("'v1'")),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "uf", "periodo", "indicador", "versao_calculo", name="pk_mart_consolidado_uf"
        ),
        schema="gold",
    )
    op.create_index(
        "ix_mart_consolidado_uf_periodo",
        "mart_consolidado_uf",
        ["uf", "periodo"],
        schema="gold",
    )

    # --- gold.dim_regiao_uf: regiões da UF como DADO (drill UF→região→município) ---
    op.create_table(
        "dim_regiao_uf",
        sa.Column("uf", sa.String(length=2), nullable=False),
        sa.Column("regiao_codigo", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column(
            "municipios",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        # Nível oficial usado como "região" (ex.: 'regiao_imediata' do IBGE) e a fonte.
        sa.Column("nivel_fonte", sa.Text(), nullable=True),
        sa.Column("fonte", sa.Text(), nullable=True),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("uf", "regiao_codigo", name="pk_dim_regiao_uf"),
        schema="gold",
    )

    # --- gold.geo_malha_uf: malha municipal real (GeoJSON do IBGE) para o coroplético ---
    op.create_table(
        "geo_malha_uf",
        sa.Column("uf", sa.String(length=2), primary_key=True),
        # Guardamos GeoJSON (renderiza direto no navegador, sem dependência de topojson).
        sa.Column("formato", sa.Text(), nullable=False, server_default=sa.text("'geojson'")),
        sa.Column("malha", postgresql.JSONB(), nullable=False),
        sa.Column("simplificacao", sa.Text(), nullable=True),
        sa.Column("fonte", sa.Text(), nullable=True),
        sa.Column("ano", sa.Integer(), nullable=True),
        sa.Column("n_areas", sa.Integer(), nullable=True),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="gold",
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gold TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("geo_malha_uf", schema="gold")
    op.drop_table("dim_regiao_uf", schema="gold")
    op.drop_index("ix_mart_consolidado_uf_periodo", table_name="mart_consolidado_uf", schema="gold")
    op.drop_table("mart_consolidado_uf", schema="gold")
