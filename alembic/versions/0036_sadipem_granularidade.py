"""SADIPEM na granularidade da fonte: campos que faltavam, colunas mortas fora, CDP nacional.

Três problemas resolvidos de uma vez, todos com a mesma raiz — o silver descrevia um
SADIPEM imaginado em vez do publicado:

1. **Campos descartados.** O PVL publica 18 campos e guardávamos 7; iam embora
   ``num_pvl``/``num_processo`` (a âncora documental da operação no Tesouro),
   ``finalidade``, ``credor``, ``tipo_credor``, ``moeda`` e ``data_protocolo``. O
   cronograma publica a separação **dívida consolidada × operações contratadas** e
   guardávamos só o total — justamente o corte que responde "quanto do serviço da dívida
   vem do que acabei de contratar".

2. **Colunas estruturalmente vazias.** ``cronograma.juros`` (0 de 182 linhas),
   ``cronograma.mes`` (0 de 182) e ``pvl.decisao`` (0 de 606) mapeavam campos que a API
   não devolve. Coluna que nunca se preenche não é dado ausente: é promessa de
   granularidade que a fonte não tem. Na gold, ``juros`` chegava à tela como
   "R$ 0,00" — que se lê como "não há juros", não como "a fonte não separa".

3. **CDP nacional atribuído ao ente.** ``res-cdp`` ignora ``id_ente``: Fortaleza, São
   Paulo e sem filtro devolvem os mesmos registros. As 117 mil linhas da base do país
   estavam gravadas sob o código do ente consultado. Passam a ser ``BR``, com
   ``num_pvl``/``id_pleito`` fazendo a ponte para o ente via ``sadipem_pvl``.

A carga existente é apagada: as linhas antigas não têm como ser completadas sem
reconsultar a fonte, e o CDP antigo está atribuído ao ente errado. A reingestão é barata
(o SADIPEM é uma fotografia, não um histórico a reconstruir) e é a única forma de o dado
ficar correto — manter o que está seria preservar a atribuição errada.

Revision ID: 0036_sadipem_granularidade
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0036_sadipem_granularidade"
down_revision: str | None = "0035_sprint28_rcl_ajustada"
branch_labels: str | None = None
depends_on: str | None = None


_PVL_NOVAS = (
    ("num_pvl", sa.Text()),
    ("num_processo", sa.Text()),
    ("finalidade", sa.Text()),
    ("credor", sa.Text()),
    ("tipo_credor", sa.Text()),
    ("moeda", sa.Text()),
    ("data_protocolo", sa.Date()),
)
_OP_NOVAS = (
    ("num_pvl", sa.Text()),
    ("num_processo", sa.Text()),
    ("finalidade", sa.Text()),
    ("tipo_credor", sa.Text()),
    ("status", sa.Text()),
)
_CRONO_NOVAS = (
    ("num_pvl", sa.Text()),
    ("num_processo", sa.Text()),
    ("dc_amortizacao", sa.Numeric()),
    ("dc_encargos", sa.Numeric()),
    ("oc_amortizacao", sa.Numeric()),
    ("oc_encargos", sa.Numeric()),
    ("moeda_estrangeira", sa.Boolean()),
)
_CDP_NOVAS = (
    ("num_pvl", sa.Text()),
    ("num_processo", sa.Text()),
    ("id_pleito", sa.Text()),
)
_FATO_NOVAS = (
    ("dc_amortizacao", sa.Numeric()),
    ("dc_encargos", sa.Numeric()),
    ("oc_amortizacao", sa.Numeric()),
    ("oc_encargos", sa.Numeric()),
)


def upgrade() -> None:
    # A carga antiga não é recuperável na forma nova (faltam campos que só a fonte tem) e,
    # no caso do CDP, está atribuída ao ente errado. Limpar antes de alterar evita deixar
    # linhas meio-preenchidas que passariam por dado completo.
    for tabela in (
        "silver.sadipem_pvl",
        "silver.sadipem_op_contratada",
        "silver.sadipem_cronograma_pgto",
        "silver.sadipem_cdp",
        "gold.fato_vencimento",
    ):
        op.execute(f"DELETE FROM {tabela}")

    for coluna, tipo in _PVL_NOVAS:
        op.add_column("sadipem_pvl", sa.Column(coluna, tipo, nullable=True), schema="silver")
    for coluna, tipo in _OP_NOVAS:
        op.add_column(
            "sadipem_op_contratada", sa.Column(coluna, tipo, nullable=True), schema="silver"
        )
    for coluna, tipo in _CRONO_NOVAS:
        op.add_column(
            "sadipem_cronograma_pgto", sa.Column(coluna, tipo, nullable=True), schema="silver"
        )
    for coluna, tipo in _CDP_NOVAS:
        op.add_column("sadipem_cdp", sa.Column(coluna, tipo, nullable=True), schema="silver")
    for coluna, tipo in _FATO_NOVAS:
        op.add_column("fato_vencimento", sa.Column(coluna, tipo, nullable=True), schema="gold")

    # Colunas mortas: campos que a API do SADIPEM não publica.
    op.drop_column("sadipem_pvl", "decisao", schema="silver")
    op.drop_column("sadipem_cronograma_pgto", "juros", schema="silver")
    op.drop_column("sadipem_cronograma_pgto", "mes", schema="silver")

    # O ``mes`` saía da chave única junto com a coluna: fixado em zero, fazia a chave
    # carregar uma dimensão que a fonte não tem.
    op.drop_constraint("uq_fato_vencimento_chave", "fato_vencimento", schema="gold")
    op.drop_column("fato_vencimento", "mes", schema="gold")
    op.drop_column("fato_vencimento", "juros", schema="gold")
    op.create_unique_constraint(
        "uq_fato_vencimento_chave",
        "fato_vencimento",
        ["cod_ibge", "periodo_ref", "id_operacao", "ano", "versao_entrega"],
        schema="gold",
    )


def downgrade() -> None:
    op.drop_constraint("uq_fato_vencimento_chave", "fato_vencimento", schema="gold")
    op.add_column(
        "fato_vencimento",
        sa.Column("juros", sa.Numeric(), nullable=False, server_default="0"),
        schema="gold",
    )
    op.add_column(
        "fato_vencimento",
        sa.Column("mes", sa.Integer(), nullable=False, server_default="0"),
        schema="gold",
    )
    op.create_unique_constraint(
        "uq_fato_vencimento_chave",
        "fato_vencimento",
        ["cod_ibge", "periodo_ref", "id_operacao", "ano", "mes", "versao_entrega"],
        schema="gold",
    )
    op.add_column(
        "sadipem_cronograma_pgto", sa.Column("mes", sa.Integer(), nullable=True), schema="silver"
    )
    op.add_column(
        "sadipem_cronograma_pgto", sa.Column("juros", sa.Numeric(), nullable=True), schema="silver"
    )
    op.add_column("sadipem_pvl", sa.Column("decisao", sa.Text(), nullable=True), schema="silver")

    for coluna, _ in _FATO_NOVAS:
        op.drop_column("fato_vencimento", coluna, schema="gold")
    for coluna, _ in _CDP_NOVAS:
        op.drop_column("sadipem_cdp", coluna, schema="silver")
    for coluna, _ in _CRONO_NOVAS:
        op.drop_column("sadipem_cronograma_pgto", coluna, schema="silver")
    for coluna, _ in _OP_NOVAS:
        op.drop_column("sadipem_op_contratada", coluna, schema="silver")
    for coluna, _ in _PVL_NOVAS:
        op.drop_column("sadipem_pvl", coluna, schema="silver")
