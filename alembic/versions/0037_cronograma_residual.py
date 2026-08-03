"""O "Restante a pagar" do cronograma deixa de ser descartado em silêncio.

O ``/opc-cronograma-pagamentos`` publica os anos explícitos **e** uma linha-resumo com
tudo o que vence além do horizonte, cujo campo ``ano`` vem preenchido com o texto
``"Restante a pagar"``. O conector descartava essa linha — corretamente, porque ela não é
um vencimento anual e atribuí-la a um ano fictício seria inventar dado —, mas descartava
sem deixar rastro. O total exibido passava a ser só a soma dos anos listados.

Para Fortaleza (pleito 64171) isso são **R$ 848.213.800,41** fora da conta: os anos de
2023 a 2033 somam R$ 4.261.461.097,37 e o serviço remanescente é R$ 5.109.674.897,78. A
tela subestimava o compromisso em **16,6%** — e um número de dívida para menos é o erro
que menos denuncia a si mesmo.

A linha passa a ser gravada com ``residual = true`` e ``ano = NULL``. Continua fora da
série anual (não é um ano) e vira um total à parte, declarado como "após {último ano}".

Revision ID: 0037_cronograma_residual
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0037_cronograma_residual"
down_revision: str | None = "0036_sadipem_granularidade"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "sadipem_cronograma_pgto",
        sa.Column(
            "residual",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="Linha-resumo 'Restante a pagar': vence além do horizonte publicado.",
        ),
        schema="silver",
    )
    # A carga atual foi ingerida sem a linha-resumo; reingerir é o que a traz. Apagar
    # evita conviver com cronogramas em que o residual "não existe" por omissão e outros
    # em que ele é zero de verdade — indistinguíveis depois.
    op.execute("DELETE FROM silver.sadipem_cronograma_pgto")
    op.execute("DELETE FROM gold.fato_vencimento")


def downgrade() -> None:
    op.drop_column("sadipem_cronograma_pgto", "residual", schema="silver")
