"""Cenário vira histórico versionado, e cada versão sabe sobre qual dado foi calculada.

Revision ID: 0039_cenario_versionado
Revises: 0038_despesa_linha_origem
Create Date: 2026-08-04

Um cenário salvo em `op.cenario` guardava as premissas e o resultado do momento — e nada
mais. Duas consequências, as duas silenciosas:

**Editar destruía.** Ajustar uma premissa sobrescrevia o registro. Mas cenário é peça de
decisão: "o que eu levei à reunião de agosto" precisa sobreviver ao ajuste de outubro, ou
não há como reconstruir por que a decisão foi aquela.

**Reabrir mentia por omissão.** O `resultado` congelado era exibido como se fosse a
projeção corrente. Se o ente entregou RGF novo no intervalo, as mesmas premissas produzem
outro número hoje — e a tela não tinha como dizer qual dos dois estava mostrando. É o
problema de `as_of` do §6.5 do CLAUDE.md aplicado ao cenário: sem gravar **sobre qual
entrega** o cálculo rodou, reprodução e recálculo viram a mesma tela.

## O desenho

`op.cenario` passa a ser o cabeçalho (nome, ente, indicador, quem criou) e
`op.cenario_versao` guarda cada salvamento: premissas, resultado, `as_of`, as versões de
entrega que alimentaram a série e as premissas macro **observadas naquele momento**.

Essa última coluna é a menos óbvia e a mais necessária. "Aceitei o IPCA observado" muda de
significado quando o observado muda: o cenário de agosto rodou com IPCA de 4,54%, e o
mesmo botão em dezembro significa outro número. Sem gravar o valor vigente à época, a
premissa registrada ("observado") não reproduz nada.

## Migração dos cenários existentes

Cada cenário atual vira sua versão 1, preservando `parametros` e `resultado`. `as_of` fica
nulo — não temos como saber a que instante o cálculo antigo se referia, e inventar um
carimbo daria a esses cenários uma reprodutibilidade que eles não têm. Nulo é a resposta
honesta, e a tela o trata como "procedência não registrada".

As colunas antigas de `op.cenario` **não** são removidas nesta migration: a leitura passa a
usar a versão, mas apagar a origem antes de a nova rotina estar em produção tiraria a única
cópia do dado. Ficam para uma limpeza posterior, depois de provado que nada as lê.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039_cenario_versionado"
down_revision: str | None = "0038_despesa_linha_origem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

#: Mesmo predicado das demais tabelas do plano de dados: admin passa, tenant vê o seu.
_RLS_PREDICATE = (
    "coalesce(current_setting('app.is_admin', true), 'off') = 'on' "
    "OR org_id = nullif(current_setting('app.current_org_id', true), '')::uuid"
)


def upgrade() -> None:
    # ------------------------------------------------------------------ cabeçalho
    # `arquivado_em` em vez de DELETE: um cenário que embasou decisão não deve sumir do
    # histórico porque alguém limpou a lista. Some da tela, permanece auditável.
    op.add_column(
        "cenario",
        sa.Column("arquivado_em", sa.DateTime(timezone=True), nullable=True),
        schema="op",
    )
    op.add_column(
        "cenario",
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=True),
        schema="op",
    )

    # ------------------------------------------------------------------ versões
    op.create_table(
        "cenario_versao",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # `org_id` repetido aqui de propósito: a RLS precisa do predicado na própria
        # tabela. Deduzi-lo por join deixaria a política dependente de outra tabela, que
        # é como isolamento de tenant costuma vazar.
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("cenario_id", sa.Uuid(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("parametros", postgresql.JSONB(), nullable=False),
        sa.Column("resultado", postgresql.JSONB(), nullable=True),
        # --- procedência: o que torna a versão reproduzível ---
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("versoes_entrega", postgresql.JSONB(), nullable=True),
        sa.Column("premissas_observadas", postgresql.JSONB(), nullable=True),
        sa.Column("modelo", sa.Text(), nullable=True),
        sa.Column("horizonte", sa.Integer(), nullable=True),
        sa.Column("nota", sa.Text(), nullable=True),
        sa.Column("criado_por", sa.Uuid(), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["op.organizacao.id"], name="fk_cenario_versao_org", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["cenario_id"],
            ["op.cenario.id"],
            name="fk_cenario_versao_cenario",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["criado_por"], ["op.usuario.id"], name="fk_cenario_versao_usuario",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("cenario_id", "versao", name="uq_cenario_versao"),
        sa.CheckConstraint("versao >= 1", name="ck_cenario_versao_positiva"),
        schema="op",
    )
    op.create_index(
        "ix_cenario_versao_cenario", "cenario_versao", ["cenario_id", "versao"], schema="op"
    )
    op.create_index("ix_cenario_versao_org", "cenario_versao", ["org_id"], schema="op")

    op.execute("ALTER TABLE op.cenario_versao ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE op.cenario_versao FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY cenario_versao_tenant_isolation ON op.cenario_versao "
        f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON op.cenario_versao TO {APP_ROLE}")

    # ------------------------------------------------------------------ backfill
    # Cada cenário existente vira sua versão 1. `as_of` fica **nulo**: não sabemos a que
    # instante o cálculo antigo se referia, e carimbar `now()` daria a esses registros uma
    # reprodutibilidade que eles não têm.
    op.execute(
        """
        INSERT INTO op.cenario_versao
            (id, org_id, cenario_id, versao, nome, parametros, resultado,
             as_of, criado_por, criado_em, nota)
        SELECT gen_random_uuid(), c.org_id, c.id, 1, c.nome, c.parametros, c.resultado,
               NULL, c.criado_por, c.criado_em,
               'Versão migrada do formato anterior; procedência do dado não registrada.'
          FROM op.cenario c
        """
    )
    op.execute("UPDATE op.cenario SET atualizado_em = criado_em WHERE atualizado_em IS NULL")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS cenario_versao_tenant_isolation ON op.cenario_versao")
    op.drop_index("ix_cenario_versao_org", table_name="cenario_versao", schema="op")
    op.drop_index("ix_cenario_versao_cenario", table_name="cenario_versao", schema="op")
    op.drop_table("cenario_versao", schema="op")
    op.drop_column("cenario", "atualizado_em", schema="op")
    op.drop_column("cenario", "arquivado_em", schema="op")
