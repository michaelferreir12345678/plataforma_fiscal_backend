"""Sprint 12 — Patrimônio (DCA) & Explorador MSC (Módulo 11).

Revision ID: 0016_sprint12_accounting
Revises: 0015_sprint11_health_edu
Create Date: 2026-07-22

Materializa o Plano de Contas Aplicado ao Setor Público (PCASP) e o patrimônio do ente a
partir de duas fontes do SICONFI:

- ``silver.siconfi_msc`` (Matriz de Saldos Contábeis — **mensal, por conta PCASP**; a maior
  tabela do sistema) → ``gold.fato_msc_saldo`` (**particionada por (uf, ano)**, desenhada
  para migrar a um OLAP colunar — ver CLAUDE.md §3) e ``gold.mart_msc_rollup`` (rollup
  pré-calculado pai = Σ filhos, para o drill *lazy* e a matriz mensal < 300 ms).
- ``silver.siconfi_dca`` (Declaração de Contas Anuais — Balanço Patrimonial I-AB, Variações
  I-HI, Balanço Orçamentário I-C/I-D) → ``gold.fato_balanco``.

``gold.dim_conta_pcasp`` (ltree) é a hierarquia PCASP posicional que concilia os códigos da
MSC (9 dígitos) e da DCA (``P1.1.…``) num código canônico único.

**Particionamento composto (uf → ano):** a raiz particiona por ``LIST (uf)``; cada UF é
subparticionada por ``LIST (ano)``. Partições DEFAULT em ambos os níveis garantem que a
ingestão de qualquer ente/UF/ano nunca falhe (rows caem no catch-all). O caminho de OLAP
(Sprint 12 / §3) recria as partições quentes (uf, ano) dedicadas — ver ``docs`` do módulo.
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_sprint12_accounting"
down_revision: str | None = "0015_sprint11_health_edu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

# UFs com partição dedicada de saída (as demais caem na partição DEFAULT, ainda válida).
# SP publica MSC; CE (Fortaleza) publica DCA. Ampliar aqui é opcional — não é pré-requisito.
UFS_EXPLICITAS = ("SP", "CE", "RJ", "MG", "DF")
ANOS = tuple(range(2019, 2026))  # 2019..2025


def upgrade() -> None:
    # --- silver: MSC ganha a natureza da conta (D/C), capturada do payload real ---
    op.add_column(
        "siconfi_msc",
        sa.Column("natureza", sa.String(length=1), nullable=True),
        schema="silver",
    )

    # --- gold.dim_conta_pcasp: hierarquia PCASP posicional (ltree) ---
    op.execute(
        """
        CREATE TABLE gold.dim_conta_pcasp (
            codigo text PRIMARY KEY,
            descricao text NOT NULL,
            parent_codigo text REFERENCES gold.dim_conta_pcasp(codigo),
            nivel integer NOT NULL,
            path ltree NOT NULL,
            classe integer NOT NULL,
            natureza text NOT NULL DEFAULT 'D',
            fonte_dado text
        )
        """
    )
    op.execute("CREATE INDEX ix_dim_conta_pcasp_path ON gold.dim_conta_pcasp USING gist (path)")
    op.execute("CREATE INDEX ix_dim_conta_pcasp_parent ON gold.dim_conta_pcasp (parent_codigo)")
    op.execute("CREATE INDEX ix_dim_conta_pcasp_classe ON gold.dim_conta_pcasp (classe)")

    # --- gold.fato_msc_saldo: particionada por (uf, ano) — LIST(uf) → LIST(ano) ---
    op.execute(
        """
        CREATE TABLE gold.fato_msc_saldo (
            uf text NOT NULL,
            ano integer NOT NULL,
            cod_ibge text NOT NULL,
            periodo text NOT NULL,
            mes integer NOT NULL,
            cod_conta text NOT NULL,
            natureza text,
            saldo_inicial numeric,
            mov_devedor numeric,
            mov_credor numeric,
            saldo_final numeric,
            versao_entrega text NOT NULL,
            CONSTRAINT pk_fato_msc_saldo
                PRIMARY KEY (uf, ano, cod_ibge, periodo, cod_conta, versao_entrega)
        ) PARTITION BY LIST (uf)
        """
    )
    # Partição DEFAULT de UF (subparticionada por ano, com DEFAULT) — catch-all seguro.
    op.execute(
        "CREATE TABLE gold.fato_msc_saldo_uf_default "
        "PARTITION OF gold.fato_msc_saldo DEFAULT PARTITION BY LIST (ano)"
    )
    op.execute(
        "CREATE TABLE gold.fato_msc_saldo_uf_default_default "
        "PARTITION OF gold.fato_msc_saldo_uf_default DEFAULT"
    )
    # Partições dedicadas por UF × ano (+ DEFAULT de ano por UF).
    for uf in UFS_EXPLICITAS:
        low = uf.lower()
        op.execute(
            f"CREATE TABLE gold.fato_msc_saldo_{low} "
            f"PARTITION OF gold.fato_msc_saldo FOR VALUES IN ('{uf}') PARTITION BY LIST (ano)"
        )
        for ano in ANOS:
            op.execute(
                f"CREATE TABLE gold.fato_msc_saldo_{low}_{ano} "
                f"PARTITION OF gold.fato_msc_saldo_{low} FOR VALUES IN ({ano})"
            )
        op.execute(
            f"CREATE TABLE gold.fato_msc_saldo_{low}_default "
            f"PARTITION OF gold.fato_msc_saldo_{low} DEFAULT"
        )
    # Índices na raiz propagam a todas as partições (consulta de conta < 300 ms).
    op.execute(
        "CREATE INDEX ix_fato_msc_saldo_conta "
        "ON gold.fato_msc_saldo (cod_ibge, ano, cod_conta)"
    )
    op.execute(
        "CREATE INDEX ix_fato_msc_saldo_periodo "
        "ON gold.fato_msc_saldo (cod_ibge, periodo, cod_conta)"
    )

    # --- gold.mart_msc_rollup: rollup pré-calculado (pai = Σ filhos), leitura otimizada ---
    op.create_table(
        "mart_msc_rollup",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("uf", sa.String(length=2), nullable=True),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),  # AAAA-Mnn
        sa.Column("mes", sa.Integer(), nullable=False),
        sa.Column("cod_conta", sa.Text(), nullable=False),
        sa.Column("parent_conta", sa.Text(), nullable=True),
        sa.Column("nivel", sa.Integer(), nullable=False),
        sa.Column("classe", sa.Integer(), nullable=False),
        sa.Column("natureza", sa.String(length=1), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("saldo_inicial", sa.Numeric(), nullable=True),
        sa.Column("saldo_final", sa.Numeric(), nullable=True),
        sa.Column("has_children", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge", "periodo", "cod_conta", "versao_entrega",
            name="uq_mart_msc_rollup_chave",
        ),
        schema="gold",
    )
    # Drill *lazy*: filhos diretos de um nó em um mês.
    op.create_index(
        "ix_mart_msc_rollup_children", "mart_msc_rollup",
        ["cod_ibge", "periodo", "parent_conta"], schema="gold",
    )
    # Matriz mensal: uma conta ao longo do ano.
    op.create_index(
        "ix_mart_msc_rollup_conta", "mart_msc_rollup",
        ["cod_ibge", "ano", "cod_conta"], schema="gold",
    )

    # --- gold.fato_balanco: balanços da DCA (patrimonial / variações / orçamentário) ---
    op.create_table(
        "fato_balanco",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cod_ibge", sa.String(length=7), nullable=False),
        sa.Column("uf", sa.String(length=2), nullable=True),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("periodo", sa.Text(), nullable=False),  # str(ano)
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("anexo", sa.Text(), nullable=True),
        sa.Column("cod_conta", sa.Text(), nullable=False),
        sa.Column("parent_conta", sa.Text(), nullable=True),
        sa.Column("nivel", sa.Integer(), nullable=True),
        sa.Column("conta_descricao", sa.Text(), nullable=True),
        sa.Column("coluna", sa.Text(), nullable=True),
        sa.Column("valor", sa.Numeric(), nullable=True),
        sa.Column("versao_entrega", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "cod_ibge", "ano", "tipo", "cod_conta", "coluna", "versao_entrega",
            name="uq_fato_balanco_chave",
        ),
        schema="gold",
    )
    op.create_index(
        "ix_fato_balanco_ente_tipo", "fato_balanco", ["cod_ibge", "ano", "tipo"], schema="gold"
    )
    op.create_index(
        "ix_fato_balanco_children", "fato_balanco",
        ["cod_ibge", "ano", "tipo", "parent_conta"], schema="gold",
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gold TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_fato_balanco_children", table_name="fato_balanco", schema="gold")
    op.drop_index("ix_fato_balanco_ente_tipo", table_name="fato_balanco", schema="gold")
    op.drop_table("fato_balanco", schema="gold")
    op.drop_index("ix_mart_msc_rollup_conta", table_name="mart_msc_rollup", schema="gold")
    op.drop_index("ix_mart_msc_rollup_children", table_name="mart_msc_rollup", schema="gold")
    op.drop_table("mart_msc_rollup", schema="gold")
    # CASCADE remove todas as partições da árvore de fato_msc_saldo.
    op.execute("DROP TABLE IF EXISTS gold.fato_msc_saldo CASCADE")
    op.execute("DROP TABLE IF EXISTS gold.dim_conta_pcasp CASCADE")
    op.drop_column("siconfi_msc", "natureza", schema="silver")
