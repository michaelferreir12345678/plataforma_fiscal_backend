"""Sprint 25E — quem tratou o alerta, e quando.

``op.alerta`` guardava só o ``status`` corrente: dava para saber que um alerta estava
resolvido, mas não **quando** nem **por quem**. Sem isso não há histórico defensável — a
auditoria (§2.13) pediu exatamente "histórico/auditoria de alertas resolvidos", e o
tempo até a resolução é a única medida de que a fila está sendo trabalhada.

Duas colunas, preenchidas quando o alerta sai da fila ativa (resolvida/descartada) e
limpas se ele voltar a ela — reabrir um alerta não pode manter a assinatura de quem o
havia fechado.

Revision ID: 0032_sprint25e_alerta_resolucao
Revises: 0031_sprint25c_denominador_mart
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_sprint25e_alerta_resolucao"
down_revision: str | None = "0031_sprint25c_denominador_mart"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "alerta",
        sa.Column(
            "resolvido_em",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Instante em que o alerta saiu da fila ativa (resolvida/descartada).",
        ),
        schema="op",
    )
    op.add_column(
        "alerta",
        sa.Column(
            "resolvido_por",
            sa.Uuid(),
            nullable=True,
            comment="Usuário que tratou o alerta; NULL quando ele voltou para a fila.",
        ),
        schema="op",
    )
    op.create_foreign_key(
        "fk_alerta_resolvido_por",
        "alerta",
        "usuario",
        ["resolvido_por"],
        ["id"],
        source_schema="op",
        referent_schema="op",
        ondelete="SET NULL",
    )
    # O histórico é lido por (org, status) e ordenado pela resolução.
    op.create_index(
        "ix_alerta_org_status_resolucao",
        "alerta",
        ["org_id", "status", "resolvido_em"],
        schema="op",
    )


def downgrade() -> None:
    op.drop_index("ix_alerta_org_status_resolucao", table_name="alerta", schema="op")
    op.drop_constraint("fk_alerta_resolvido_por", "alerta", schema="op", type_="foreignkey")
    op.drop_column("alerta", "resolvido_por", schema="op")
    op.drop_column("alerta", "resolvido_em", schema="op")
