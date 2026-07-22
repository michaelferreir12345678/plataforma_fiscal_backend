"""Sprint 13 - historico das definicoes ajustaveis de coorte.

Revision ID: 0019_sprint13_cohort_bitemporal
Revises: 0018_sprint13_cohort_lineage
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_sprint13_cohort_bitemporal"
down_revision: str | None = "0018_sprint13_cohort_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dim_coorte_versao",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "coorte_id",
            sa.Uuid(),
            sa.ForeignKey("gold.dim_coorte.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("codigo", sa.Text(), nullable=False),
        sa.Column("criterio", sa.String(length=20), nullable=False),
        sa.Column("faixa", sa.Text(), nullable=False),
        sa.Column("rotulo", sa.Text(), nullable=False),
        sa.Column("unidade_criterio", sa.String(length=20), nullable=True),
        sa.Column("limite_inferior", sa.Numeric(), nullable=True),
        sa.Column("limite_superior", sa.Numeric(), nullable=True),
        sa.Column("inclusivo_superior", sa.Boolean(), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("source_ref", postgresql.JSONB(), nullable=False),
        sa.Column("valido_desde", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valido_ate", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "coorte_id", "valido_desde", name="uq_dim_coorte_versao_inicio"
        ),
        schema="gold",
    )
    op.create_index(
        "ix_dim_coorte_versao_validade",
        "dim_coorte_versao",
        ["coorte_id", "valido_desde", "valido_ate"],
        schema="gold",
    )

    # A configuracao inicial e deliberadamente retroativa: define como os periodos
    # historicos devem ser agrupados ate que um ajuste explicito crie nova versao.
    op.execute(
        """
        INSERT INTO gold.dim_coorte_versao (
            coorte_id, codigo, criterio, faixa, rotulo, unidade_criterio,
            limite_inferior, limite_superior, inclusivo_superior, ordem, ativo,
            source_ref, valido_desde, valido_ate
        )
        SELECT id, codigo, criterio, faixa, rotulo, unidade_criterio,
               limite_inferior, limite_superior, inclusivo_superior, ordem, ativo,
               source_ref, TIMESTAMPTZ '1900-01-01 00:00:00+00', NULL
          FROM gold.dim_coorte
        """
    )

    op.execute(
        """
        CREATE FUNCTION gold.touch_dim_coorte_updated_at()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.atualizado_em := clock_timestamp();
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION gold.capture_dim_coorte_version()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                UPDATE gold.dim_coorte_versao
                   SET valido_ate = NEW.atualizado_em
                 WHERE coorte_id = NEW.id
                   AND valido_ate IS NULL;
            END IF;

            INSERT INTO gold.dim_coorte_versao (
                coorte_id, codigo, criterio, faixa, rotulo, unidade_criterio,
                limite_inferior, limite_superior, inclusivo_superior, ordem, ativo,
                source_ref, valido_desde, valido_ate
            ) VALUES (
                NEW.id, NEW.codigo, NEW.criterio, NEW.faixa, NEW.rotulo,
                NEW.unidade_criterio, NEW.limite_inferior, NEW.limite_superior,
                NEW.inclusivo_superior, NEW.ordem, NEW.ativo, NEW.source_ref,
                NEW.atualizado_em, NULL
            );
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_dim_coorte_touch
        BEFORE UPDATE ON gold.dim_coorte
        FOR EACH ROW EXECUTE FUNCTION gold.touch_dim_coorte_updated_at()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_dim_coorte_history
        AFTER INSERT OR UPDATE ON gold.dim_coorte
        FOR EACH ROW EXECUTE FUNCTION gold.capture_dim_coorte_version()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_dim_coorte_history ON gold.dim_coorte")
    op.execute("DROP TRIGGER IF EXISTS trg_dim_coorte_touch ON gold.dim_coorte")
    op.execute("DROP FUNCTION IF EXISTS gold.capture_dim_coorte_version()")
    op.execute("DROP FUNCTION IF EXISTS gold.touch_dim_coorte_updated_at()")
    op.drop_index(
        "ix_dim_coorte_versao_validade",
        table_name="dim_coorte_versao",
        schema="gold",
    )
    op.drop_table("dim_coorte_versao", schema="gold")
