"""Sprint 13 - linhagem IBGE da coorte e rotulos de fronteira.

Revision ID: 0018_sprint13_cohort_lineage
Revises: 0017_sprint13_benchmark
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_sprint13_cohort_lineage"
down_revision: str | None = "0017_sprint13_benchmark"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dim_ente",
        sa.Column("pop_source_ref", postgresql.JSONB(), nullable=True),
        schema="gold",
    )
    op.add_column(
        "dim_ente",
        sa.Column("pib_source_ref", postgresql.JSONB(), nullable=True),
        schema="gold",
    )

    op.execute(
        """
        UPDATE gold.dim_ente AS d
           SET pop_source_ref = (
               SELECT jsonb_build_object(
                   'relatorio', 'IBGE-POP',
                   'anexo', 'Agregado 6579 - variavel 9324',
                   'periodo', p.ano_ref::text,
                   'versao_entrega', p.versao_entrega
               )
                 FROM silver.ibge_populacao AS p
                 JOIN gold.dim_entrega AS e
                   ON e.cod_ibge = p.cod_ibge
                  AND e.relatorio = 'IBGE-POP'
                  AND e.periodo = p.ano_ref::text
                  AND e.versao_entrega = p.versao_entrega
                WHERE p.cod_ibge = d.cod_ibge
                  AND p.ano_ref = d.pop_ano_ref
                  AND p.populacao IS NOT NULL
                  AND e.vigente IS TRUE
                ORDER BY e.homologada_em DESC
                LIMIT 1
           )
         WHERE d.pop_ano_ref IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE gold.dim_ente AS d
           SET pib_source_ref = (
               SELECT jsonb_build_object(
                   'relatorio', 'IBGE-PIB',
                   'anexo', 'Agregado 5938 - variavel 37 (mil reais)',
                   'periodo', p.ano_ref::text,
                   'versao_entrega', p.versao_entrega
               )
                 FROM silver.ibge_pib AS p
                 JOIN gold.dim_entrega AS e
                   ON e.cod_ibge = p.cod_ibge
                  AND e.relatorio = 'IBGE-PIB'
                  AND e.periodo = p.ano_ref::text
                  AND e.versao_entrega = p.versao_entrega
                WHERE p.cod_ibge = d.cod_ibge
                  AND p.ano_ref = d.pib_ano_ref
                  AND p.pib_nominal IS NOT NULL
                  AND e.vigente IS TRUE
                ORDER BY e.homologada_em DESC
                LIMIT 1
           )
         WHERE d.pib_ano_ref IS NOT NULL
        """
    )

    labels = {
        "porte:pequeno": "Menos de 50 mil habitantes",
        "porte:medio": "50 mil a menos de 200 mil habitantes",
        "porte:grande": "200 mil a menos de 1 milhão de habitantes",
        "pib:ate_1bi": "PIB abaixo de R$ 1 bilhão",
        "pib:1a5bi": "PIB de R$ 1 a menos de 5 bilhões",
        "pib:5a20bi": "PIB de R$ 5 a menos de 20 bilhões",
        "pib:20a50bi": "PIB de R$ 20 a menos de 50 bilhões",
        "pib:50a100bi": "PIB de R$ 50 a menos de 100 bilhões",
    }
    for codigo, rotulo in labels.items():
        op.execute(
            sa.text("UPDATE gold.dim_coorte SET rotulo = :rotulo WHERE codigo = :codigo")
            .bindparams(codigo=codigo, rotulo=rotulo)
        )


def downgrade() -> None:
    labels = {
        "porte:pequeno": "Até 50 mil habitantes",
        "porte:medio": "50 mil a 200 mil habitantes",
        "porte:grande": "200 mil a 1 milhão de habitantes",
        "pib:ate_1bi": "PIB até R$ 1 bilhão",
        "pib:1a5bi": "PIB de R$ 1 a 5 bilhões",
        "pib:5a20bi": "PIB de R$ 5 a 20 bilhões",
        "pib:20a50bi": "PIB de R$ 20 a 50 bilhões",
        "pib:50a100bi": "PIB de R$ 50 a 100 bilhões",
    }
    for codigo, rotulo in labels.items():
        op.execute(
            sa.text("UPDATE gold.dim_coorte SET rotulo = :rotulo WHERE codigo = :codigo")
            .bindparams(codigo=codigo, rotulo=rotulo)
        )
    op.drop_column("dim_ente", "pib_source_ref", schema="gold")
    op.drop_column("dim_ente", "pop_source_ref", schema="gold")
