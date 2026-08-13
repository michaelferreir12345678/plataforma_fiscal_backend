"""Sprint IA-1a — auditoria das chamadas de ferramenta da camada de IA.

Revision ID: 0043_ia_tool_call
Revises: 0042_ibge_malha_job
Create Date: 2026-08-12

Uma tabela nova, aditiva, sem tocar em nada publicado: ``op.ia_tool_call``.

**Por que ela existe.** A camada de ferramentas (``shared/tooling``) é o caminho pelo qual
um modelo de linguagem — interno hoje, cliente MCP amanhã — obtém dado fiscal. O guardrail
G7 do plano exige que "como a IA chegou nisso?" tenha resposta **consultável**, não
reconstituída a partir de log de aplicação: quem chamou, qual ferramenta, com quais
argumentos, em que ``as_of``, quantas linhas voltaram, quanto demorou e como terminou.

**A chamada que falha é a que mais importa.** Tentativa de leitura de ente fora da
carteira, ente sem licença, argumento inventado, ferramenta pedida por um nome que não
existe — tudo isso é registrado com ``status='erro'`` e o ``type`` do Problem Details em
``erro_tipo``, que é justamente o campo que distingue *fora do escopo* de *sem licença*
(A22/E1). O envelope grava em transação própria para que o *rollback* da requisição
recusada não apague o registro da recusa.

**RLS.** É dado do tenant (quem perguntou o quê), não dado fiscal público: isolamento por
``org_id``, no mesmo padrão de ``op.conversa``.

**Reversibilidade.** ``downgrade`` derruba política, índices e tabela. Nada além dela é
tocado, então reverter não implica perda de dado fiscal — só do histórico de chamadas.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_ia_tool_call"
down_revision: str | None = "0042_ibge_malha_job"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

_RLS_PREDICATE = (
    "coalesce(current_setting('app.is_admin', true), 'off') = 'on' "
    "OR org_id = nullif(current_setting('app.current_org_id', true), '')::uuid"
)


def upgrade() -> None:
    op.create_table(
        "ia_tool_call",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("usuario_id", sa.Uuid(), nullable=True),
        sa.Column(
            "conversa_id",
            sa.Uuid(),
            nullable=True,
            comment="Conversa que originou a chamada (liga a cadeia de ferramentas à resposta).",
        ),
        sa.Column("ferramenta", sa.Text(), nullable=False),
        sa.Column(
            "origem",
            sa.String(length=24),
            nullable=False,
            server_default="api",
            comment="assistente | mcp | api — de onde partiu a chamada.",
        ),
        sa.Column("cod_ibge", sa.String(length=7), nullable=True),
        sa.Column(
            "argumentos",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "as_of",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="as_of pedido na chamada; NULL ⇒ versão vigente (§6.5).",
        ),
        sa.Column("status", sa.String(length=8), nullable=False, server_default="ok"),
        sa.Column(
            "erro_tipo",
            sa.Text(),
            nullable=True,
            comment="type do Problem Details — distingue escopo de licença (A22/E1).",
        ),
        sa.Column("erro_detalhe", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("linhas", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("duracao_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "source_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="Fontes dos números devolvidos — o lastro do que o modelo recebeu.",
        ),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["op.organizacao.id"], name="fk_ia_tool_call_org", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"],
            ["op.usuario.id"],
            name="fk_ia_tool_call_usuario",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["conversa_id"],
            ["op.conversa.id"],
            name="fk_ia_tool_call_conversa",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("status IN ('ok', 'erro')", name="ck_ia_tool_call_status"),
        schema="op",
    )
    op.create_index("ix_ia_tool_call_org_id", "ia_tool_call", ["org_id"], schema="op")
    # A consulta natural da auditoria é "as últimas chamadas desta organização".
    op.create_index(
        "ix_ia_tool_call_org_criado", "ia_tool_call", ["org_id", "criado_em"], schema="op"
    )
    # E a segunda é "onde isto falhou?" — sem índice, viraria varredura da tabela inteira.
    op.create_index(
        "ix_ia_tool_call_org_status", "ia_tool_call", ["org_id", "status", "ferramenta"],
        schema="op",
    )
    op.execute("ALTER TABLE op.ia_tool_call ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE op.ia_tool_call FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY ia_tool_call_tenant_isolation ON op.ia_tool_call "
        f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON op.ia_tool_call TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS ia_tool_call_tenant_isolation ON op.ia_tool_call")
    op.execute("ALTER TABLE op.ia_tool_call DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_ia_tool_call_org_status", table_name="ia_tool_call", schema="op")
    op.drop_index("ix_ia_tool_call_org_criado", table_name="ia_tool_call", schema="op")
    op.drop_index("ix_ia_tool_call_org_id", table_name="ia_tool_call", schema="op")
    op.drop_table("ia_tool_call", schema="op")
