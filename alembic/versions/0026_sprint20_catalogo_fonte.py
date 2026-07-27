"""Sprint 20 — completa gold.catalogo_fonte com a rastreabilidade do parser e do impacto.

A Sprint 21 criou ``gold.catalogo_fonte`` com o essencial (família, cadência, órgão) porque
era o instrumento pelo qual ela seria medida. A Sprint 20 fecha o contrato especificado:
``parser_versao`` (qual versão do parser produziu o dado — muda quando o layout da fonte
muda) e, como DADO e não código, ``paginas_impactadas``/``dependencias`` — que respondem
"se esta fonte cair, o que para de funcionar?".

Revision ID: 0026_sprint20_catalogo_fonte
Revises: 0025_sprint21_cobertura
Create Date: 2026-07-23
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_sprint20_catalogo_fonte"
down_revision: str | None = "0025_sprint21_cobertura"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalogo_fonte",
        sa.Column("parser_versao", sa.Text(), nullable=True),
        schema="gold",
    )
    op.add_column(
        "catalogo_fonte",
        sa.Column(
            "paginas_impactadas",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        schema="gold",
    )
    op.add_column(
        "catalogo_fonte",
        sa.Column(
            "dependencias",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        schema="gold",
    )


def downgrade() -> None:
    op.drop_column("catalogo_fonte", "dependencias", schema="gold")
    op.drop_column("catalogo_fonte", "paginas_impactadas", schema="gold")
    op.drop_column("catalogo_fonte", "parser_versao", schema="gold")
