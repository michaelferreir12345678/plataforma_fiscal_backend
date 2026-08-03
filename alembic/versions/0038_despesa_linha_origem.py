"""``fato_despesa`` passa a guardar a linha bruta que o originou.

O drill até a linha do RREO precisa saber **quais linhas do Anexo 02 alimentaram cada nó**.
Para a receita isso é trivial — ``origem_codigo`` é o próprio ``cod_conta`` do SICONFI, e a
conferência bate à centavo. Para a despesa, não:

* o eixo **função** tem código derivado (``10`` para Saúde, ``10.ADMINISTRACAO_GERAL`` para
  a subfunção), obtido do **texto** da linha por uma tabela de nomes;
* o texto guardado no nó é o **limpo** (``FU10 - Administração Geral`` → ``Administração
  Geral``), enquanto o silver guarda o bruto;
* e a mesma descrição se repete sob funções diferentes: "Administração Geral" existe em
  Saúde, Educação, Judiciária… Casar por texto traria as linhas da função errada.

Medido no RREO 2025-B6 de Fortaleza: **31 dos 105 nós** não têm correspondência por
descrição. Um drill que acertasse dois terços das linhas seria pior que nenhum — o gestor
confere alguns nós, vê bater, e passa a confiar nos outros.

``linha_origem`` grava a descrição **bruta**, exatamente como veio na entrega, no momento
em que a materialização ainda sabe qual linha alimentou qual nó. Vínculo por construção, e
não por engenharia reversa.

A carga é apagada porque a coluna não tem como ser preenchida a posteriori — é justamente
essa a informação que se perdia. Rematerializar é barato (``replay`` do bronze).

Revision ID: 0038_despesa_linha_origem
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0038_despesa_linha_origem"
down_revision: str | None = "0037_cronograma_residual"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "fato_despesa",
        sa.Column(
            "linha_origem",
            sa.Text(),
            nullable=True,
            comment="Descrição bruta da linha do RREO que alimentou o nó (vínculo do drill).",
        ),
        schema="gold",
    )
    op.execute("DELETE FROM gold.fato_despesa")


def downgrade() -> None:
    op.drop_column("fato_despesa", "linha_origem", schema="gold")
