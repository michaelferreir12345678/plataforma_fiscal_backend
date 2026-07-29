"""Sprint 25C — o mart passa a dizer **sobre o que** o percentual foi calculado.

Até aqui todo indicador de ``gold.mart_indicador`` era um percentual da RCL, e a coluna
``valor_pct_rcl`` carregava esse contrato no próprio nome. Os mínimos constitucionais
(ASPS, MDE e FUNDEB) entram no mart nesta sprint e **não** têm a RCL por denominador:
a base é a receita de impostos e transferências (CF art. 198/212; LC 141/2012) — no caso
do FUNDEB, as receitas principais do fundo. Guardar 25,8% de impostos numa coluna lida
por todo o produto como "% da RCL" seria publicar um número certo com o rótulo errado.

Duas colunas resolvem isso sem reescrever o histórico:

- ``denominador`` — o que é 100% naquela linha (``rcl`` para tudo que já existia; os
  novos indicadores declaram a sua própria base). Default ``'rcl'`` preserva o
  significado de cada linha já gravada, sem backfill.
- ``base_valor`` — o denominador em R$, para que o percentual seja **reconferível** na
  própria linha (pct = valor_rs ÷ base_valor × 100) sem voltar ao fato de origem.

``valor_pct_rcl`` mantém o nome físico por compatibilidade com dez módulos que a leem;
quem apresenta o número passa a ler ``denominador`` para rotulá-lo corretamente.

Revision ID: 0031_sprint25c_denominador_mart
Revises: 0030_sprint25b_meta_fiscal
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_sprint25c_denominador_mart"
down_revision: str | None = "0030_sprint25b_meta_fiscal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mart_indicador",
        sa.Column(
            "denominador",
            sa.Text(),
            nullable=False,
            server_default="rcl",
            comment="Base do percentual: 'rcl' | 'impostos_transferencias' | 'fundeb'.",
        ),
        schema="gold",
    )
    op.add_column(
        "mart_indicador",
        sa.Column(
            "base_valor",
            sa.Numeric(),
            nullable=True,
            comment="Denominador em R$ — torna o percentual reconferível na própria linha.",
        ),
        schema="gold",
    )
    # Índice do caminho novo: benchmark/limites varrem (indicador, periodo) por coorte.
    op.create_index(
        "ix_mart_indicador_indicador_periodo",
        "mart_indicador",
        ["indicador", "periodo"],
        schema="gold",
    )
    # ``gold.mart_benchmark.unidade`` nasceu varchar(20) quando as unidades possíveis
    # eram 'percentual_rcl' e 'brl'. 'percentual_impostos_transferencias' não cabe, e
    # abreviar o rótulo para caber seria esconder a base no nome da métrica.
    op.alter_column(
        "mart_benchmark",
        "unidade",
        existing_type=sa.String(length=20),
        type_=sa.Text(),
        existing_nullable=False,
        schema="gold",
    )


def downgrade() -> None:
    # Snapshots de benchmark são materialização regenerável a partir de
    # ``gold.mart_indicador`` (o próprio build recria o que faltar). Voltar a coluna
    # para varchar(20) truncaria o rótulo da unidade — preferimos remover os snapshots
    # que só existem por causa desta revisão a devolver um rótulo mutilado.
    op.execute("DELETE FROM gold.mart_benchmark WHERE length(unidade) > 20;")
    op.alter_column(
        "mart_benchmark",
        "unidade",
        existing_type=sa.Text(),
        type_=sa.String(length=20),
        existing_nullable=False,
        schema="gold",
    )
    op.drop_index(
        "ix_mart_indicador_indicador_periodo", table_name="mart_indicador", schema="gold"
    )
    op.drop_column("mart_indicador", "base_valor", schema="gold")
    op.drop_column("mart_indicador", "denominador", schema="gold")
