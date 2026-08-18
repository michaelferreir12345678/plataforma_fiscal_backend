"""Sprint IA-7 — conversa multi-turno: o fio que liga os turnos de um mesmo diálogo.

Revision ID: 0047_conversa_multiturno
Revises: 0046_mcp_credencial
Create Date: 2026-08-14

Três colunas aditivas em ``op.conversa``: ``thread_id`` agrupa o diálogo,
``parent_id`` registra a âncora causal de cada continuação e ``verificacao`` preserva o
laudo estruturado G6 que acompanha a resposta no histórico.

**Por que ela é necessária.** ``op.conversa`` já persistia tudo que uma resposta precisa
para ser reconstruída — pergunta, resposta, fatos, fontes, período, ``as_of``. O que
faltava não era conteúdo, era **relação**: cada linha era um evento solto, e não havia como
dizer que duas perguntas pertencem à mesma conversa. Sem isso, "e por que isso aconteceu?"
não tem sujeito, que é exatamente a queixa que originou a sprint.

**Por que fio e pai.** O fio torna barata a listagem e identifica o diálogo; sozinho, ele
não distingue dois ramos abertos a partir do mesmo turno. O pai torna a retomada causal:
um CTE recursivo sobe apenas pelos ancestrais da âncora recebida e nunca incorpora um
turno irmão criado depois em outra aba. Os dois campos têm índices e chaves estrangeiras
para a própria tabela; raízes apontam ``thread_id`` para o próprio ``id`` e têm pai nulo.

**Backfill.** Toda linha existente vira raiz do próprio fio (``thread_id = id``). São as
conversas que já aconteceram em produção: elas continuam consultáveis, cada uma como um
diálogo de um turno só — que é o que de fato foram. Nenhuma resposta é reescrita.

**RLS e GRANT.** Nada a fazer: a política de ``op.conversa`` é por ``org_id`` e já vale
para a linha inteira; a role da aplicação já tem privilégio sobre a tabela. Coluna nova em
tabela existente não cria objeto novo a conceder — a lição da 0044 (criar objeto sem
``GRANT``) se aplica a tabelas e sequências, não a colunas.

**Reversibilidade.** ``downgrade`` remove índices, chaves e as três colunas. Perdem-se o
agrupamento, a cadeia causal e o laudo estruturado; pergunta, resposta, fontes e registros
de uso permanecem intactos.

**Seed:** nenhum. Não há dado de referência nesta migração — o backfill é derivado da
própria tabela.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047_conversa_multiturno"
down_revision: str | None = "0046_mcp_credencial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversa",
        sa.Column(
            "thread_id",
            sa.Uuid(),
            nullable=True,
            comment=(
                "Fio da conversa: turnos do mesmo diálogo compartilham o valor; o primeiro "
                "turno recebe o próprio id (Sprint IA-7)."
            ),
        ),
        schema="op",
    )
    op.add_column(
        "conversa",
        sa.Column(
            "parent_id",
            sa.Uuid(),
            nullable=True,
            comment="Turno imediatamente anterior usado como âncora desta continuação.",
        ),
        schema="op",
    )
    op.add_column(
        "conversa",
        sa.Column(
            "verificacao",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Laudo estruturado do G6 exibido junto do histórico.",
        ),
        schema="op",
    )
    op.add_column(
        "conversa",
        sa.Column(
            "as_of_explicito",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment=(
                "O gestor PEDIU corte bitemporal neste turno? A coluna `as_of` guarda o "
                "instante efetivo da fotografia (auditoria) e é sempre preenchida, "
                "inclusive numa leitura corrente; só este campo distingue o corte pedido "
                "do instante resolvido. Sem ele, a continuação herdava o `as_of` do "
                "primeiro turno e a conversa inteira virava reprodução histórica silenciosa "
                "a partir do segundo turno (Sprint IA-7)."
            ),
        ),
        schema="op",
    )
    # Cada conversa já registrada vira raiz do próprio fio — elas foram, de fato,
    # diálogos de um turno só.
    op.execute("UPDATE op.conversa SET thread_id = id WHERE thread_id IS NULL")
    op.alter_column("conversa", "thread_id", nullable=False, schema="op")
    op.create_foreign_key(
        "fk_conversa_thread_id_conversa",
        "conversa",
        "conversa",
        ["thread_id"],
        ["id"],
        source_schema="op",
        referent_schema="op",
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_conversa_parent_id_conversa",
        "conversa",
        "conversa",
        ["parent_id"],
        ["id"],
        source_schema="op",
        referent_schema="op",
        ondelete="CASCADE",
    )
    op.create_index("ix_conversa_thread_id", "conversa", ["thread_id"], schema="op")
    op.create_index("ix_conversa_parent_id", "conversa", ["parent_id"], schema="op")


def downgrade() -> None:
    # ``IF EXISTS`` tambem torna o downgrade seguro para ambientes de desenvolvimento
    # que chegaram a aplicar a primeira versao (apenas ``thread_id``) desta revisao antes
    # de a Sprint IA-7 ser fechada. Producao continua em 0046, mas esses bancos locais
    # precisam conseguir voltar a 0046 para reaplicar a revisao definitiva.
    op.execute("DROP INDEX IF EXISTS op.ix_conversa_parent_id")
    op.execute("DROP INDEX IF EXISTS op.ix_conversa_thread_id")
    op.execute(
        "ALTER TABLE op.conversa DROP CONSTRAINT IF EXISTS "
        "fk_conversa_parent_id_conversa"
    )
    op.execute(
        "ALTER TABLE op.conversa DROP CONSTRAINT IF EXISTS "
        "fk_conversa_thread_id_conversa"
    )
    op.execute("ALTER TABLE op.conversa DROP COLUMN IF EXISTS as_of_explicito")
    op.execute("ALTER TABLE op.conversa DROP COLUMN IF EXISTS verificacao")
    op.execute("ALTER TABLE op.conversa DROP COLUMN IF EXISTS parent_id")
    op.execute("ALTER TABLE op.conversa DROP COLUMN IF EXISTS thread_id")
