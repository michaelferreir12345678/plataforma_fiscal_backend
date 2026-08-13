"""Sprint IA-2 — o dicionário semântico como DADO.

O acervo já descreve **de onde** o dado vem (``gold.catalogo_fonte``, ``gold.lineage_edge``),
**qual é o limite** (``gold.dim_limite_legal``) e **o que falta**
(``gold.mart_cobertura_fonte``). O que nada descrevia é o que o número *significa*: a
fórmula, o denominador correto, o sentido (piso × teto) e o que cada coluna guarda.

Sem isso, uma consulta gerada por modelo escolhe a coluna plausível e errada com sintaxe
perfeita — o modo de falha mais caro num sistema fiscal, porque **parece certo**. O
precedente está no próprio acervo: a Sprint 28 descobriu que o denominador dos limites era
a RCL cheia onde a lei manda usar a RCL **Ajustada**, e custou a migration ``0035``.

Três tabelas, todas em ``gold`` — o significado descreve o dado público compartilhado, não
o operacional de nenhuma organização:

- ``dicionario_indicador`` — o verbete do indicador (fórmula, denominador, base legal,
  sentido, sinônimos de negócio, armadilhas).
- ``dicionario_campo`` — o que cada coluna das tabelas consultáveis guarda, e o que ela
  **não** é. O ``CHECK`` de ``schema_nome`` impede, no banco, que uma tabela de ``op``
  seja declarada consultável: o §4.1 do plano de MCP proíbe consulta livre sobre dado da
  organização, e uma proibição que depende de disciplina não é proibição.
- ``dicionario_juncao`` — os caminhos de junção sancionados, com a condição que precisa
  viajar junto (vigência!) e a nota do que acontece se ela for esquecida.

Toda linha carrega ``fonte_definicao`` e ``atualizado_em``: um dicionário que envelhece em
silêncio é pior que não existir, e sem a procedência da definição ninguém sabe se o
verbete acompanhou a última mudança de regra.

Revision ID: 0045_dicionario_semantico
Revises: 0044_dq_check_seq
Create Date: 2026-08-13
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_dicionario_semantico"
down_revision: str | None = "0044_dq_check_seq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Mesmo padrão das demais migrations: a role da aplicação não é dona dos objetos e
#: precisa da concessão explícita (a 0044 quebrou seis testes por esquecê-la).
APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

_TABELAS = ("dicionario_indicador", "dicionario_campo", "dicionario_juncao")


def upgrade() -> None:
    op.create_table(
        "dicionario_indicador",
        sa.Column("codigo", sa.Text(), primary_key=True),
        sa.Column("rotulo", sa.Text(), nullable=False),
        sa.Column("definicao", sa.Text(), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False, comment="Fórmula legível por gente."),
        sa.Column(
            "denominador",
            sa.Text(),
            nullable=False,
            comment="Código do denominador gravado em gold.mart_indicador.denominador.",
        ),
        sa.Column(
            "denominador_fallback",
            sa.Text(),
            nullable=True,
            comment="Denominador de reserva quando o ente não publica o canônico.",
        ),
        sa.Column("denominador_definicao", sa.Text(), nullable=False),
        sa.Column("unidade", sa.Text(), nullable=False),
        sa.Column("sentido", sa.String(length=10), nullable=False),
        sa.Column("base_legal", sa.Text(), nullable=False),
        sa.Column("tabela_origem", sa.Text(), nullable=False),
        sa.Column("coluna_valor", sa.Text(), nullable=False),
        sa.Column("coluna_base", sa.Text(), nullable=True),
        sa.Column(
            "sinonimos",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("armadilha", sa.Text(), nullable=True),
        sa.Column("fonte_definicao", sa.Text(), nullable=False),
        sa.Column("atualizado_em", sa.Date(), nullable=False),
        sa.CheckConstraint(
            "sentido IN ('teto', 'piso', 'gerencial')", name="ck_dicionario_indicador_sentido"
        ),
        sa.CheckConstraint(
            "tabela_origem NOT LIKE 'op.%'", name="ck_dicionario_indicador_sem_op"
        ),
        schema="gold",
    )

    op.create_table(
        "dicionario_campo",
        sa.Column("schema_nome", sa.Text(), primary_key=True),
        sa.Column("tabela", sa.Text(), primary_key=True),
        sa.Column("coluna", sa.Text(), primary_key=True),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("unidade", sa.Text(), nullable=True),
        sa.Column("chave", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "consultavel",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="Coluna liberada para consulta analítica governada.",
        ),
        sa.Column(
            "armadilha",
            sa.Text(),
            nullable=True,
            comment="O que esta coluna NÃO é (sinal, base, período de outra cadência…).",
        ),
        sa.Column("fonte_definicao", sa.Text(), nullable=False),
        sa.Column("atualizado_em", sa.Date(), nullable=False),
        # A proibição do §4.1 vira invariante do banco: dado da organização não entra em
        # consulta livre, e nenhuma seed distraída consegue declará-lo consultável.
        sa.CheckConstraint("schema_nome <> 'op'", name="ck_dicionario_campo_sem_op"),
        sa.CheckConstraint(
            "schema_nome IN ('gold', 'silver')", name="ck_dicionario_campo_camada"
        ),
        schema="gold",
    )

    op.create_table(
        "dicionario_juncao",
        sa.Column("origem_tabela", sa.Text(), primary_key=True),
        sa.Column("destino_tabela", sa.Text(), primary_key=True),
        sa.Column("origem_colunas", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("destino_colunas", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("cardinalidade", sa.String(length=8), nullable=False),
        sa.Column(
            "condicao",
            sa.Text(),
            nullable=True,
            comment="Filtro que precisa viajar com a junção (vigência, relatório…).",
        ),
        sa.Column("nota", sa.Text(), nullable=False, comment="O que acontece se for esquecida."),
        sa.Column("fonte_definicao", sa.Text(), nullable=False),
        sa.Column("atualizado_em", sa.Date(), nullable=False),
        sa.CheckConstraint(
            "cardinalidade IN ('1:1', 'n:1', '1:n')", name="ck_dicionario_juncao_cardinalidade"
        ),
        sa.CheckConstraint(
            "origem_tabela NOT LIKE 'op.%' AND destino_tabela NOT LIKE 'op.%'",
            name="ck_dicionario_juncao_sem_op",
        ),
        sa.CheckConstraint(
            "array_length(origem_colunas, 1) = array_length(destino_colunas, 1)",
            name="ck_dicionario_juncao_arity",
        ),
        schema="gold",
    )

    op.create_index(
        "ix_dicionario_campo_tabela", "dicionario_campo", ["schema_nome", "tabela"], schema="gold"
    )
    for tabela in _TABELAS:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON gold.{tabela} TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_dicionario_campo_tabela", table_name="dicionario_campo", schema="gold")
    op.drop_table("dicionario_juncao", schema="gold")
    op.drop_table("dicionario_campo", schema="gold")
    op.drop_table("dicionario_indicador", schema="gold")
