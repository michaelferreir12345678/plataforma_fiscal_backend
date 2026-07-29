"""Sprint 28 — o denominador do limite de pessoal é a RCL **ajustada**.

A validação em lote contra os demonstrativos oficiais mostrou uma divergência
sistemática, em todos os entes da amostra e sempre no mesmo sentido: o percentual de
despesa com pessoal saía **para menos**. A causa não estava no numerador — a despesa
líquida batia com a publicada —, e sim no denominador.

O teto do art. 20 da LRF incide sobre a *Receita Corrente Líquida Ajustada para cálculo
dos limites da despesa com pessoal* (a EC 105/2019 manda deduzir as transferências
recebidas por emenda individual). A plataforma dividia pela RCL cheia. Em Maracanaú
2024 isso significava exibir **46,91%** onde o município publicou **48,20%** — 1,29
ponto percentual a menos, num indicador cuja faixa prudencial começa em 51,3%.

Num monitor de limites, errar para menos é o pior sentido possível: esconde o risco que
o produto existe para mostrar.

O demonstrativo já publica essa receita ajustada. Guardá-la ao lado da apuração — e não
recalculá-la — é o que mantém o percentual reproduzível quando a regra de dedução mudar
de novo.

Revision ID: 0035_sprint28_rcl_ajustada
Revises: 0034_sprint19_control_plane
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_sprint28_rcl_ajustada"
down_revision: str | None = "0034_sprint19_control_plane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fato_pessoal",
        sa.Column(
            "rcl_ajustada",
            sa.Numeric(),
            nullable=True,
            comment=(
                "RCL ajustada publicada no RGF Anexo 01 — denominador do limite do "
                "art. 20 da LRF. NULL quando o ente não publicou a linha."
            ),
        ),
        schema="gold",
    )
    # As linhas já materializadas ficam com o denominador antigo até o próximo cálculo.
    # Zerar o ``pct_rcl`` aqui deixaria o produto sem número; recalculá-lo em SQL exigiria
    # reimplementar a apuração dentro da migration. A rematerialização é feita pelo
    # ``workers.materialize`` (``scripts/materializar.py``), que é quem sabe a regra.


def downgrade() -> None:
    op.drop_column("fato_pessoal", "rcl_ajustada", schema="gold")
