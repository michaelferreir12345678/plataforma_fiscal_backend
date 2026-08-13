"""Sprint IA-3 — credencial de organização para o servidor MCP.

Revision ID: 0046_mcp_credencial
Revises: 0045_dicionario_semantico
Create Date: 2026-08-13

Uma tabela nova, aditiva, sem tocar em nada publicado: ``op.mcp_credencial``.

**Por que ela existe.** O servidor MCP é a primeira porta da plataforma que um cliente
**externo** alcança (§7.2 do plano). Ele não pode autenticar por sessão de navegador: quem
chama é um agente, não uma pessoa, e não há login interativo para renovar token. A
credencial é da **organização**, e é ela que carrega o vínculo com o RBAC já existente.

**O que a credencial NÃO faz.** Ela não concede escopo nem capacidade nova. O ``papel_id``
aponta para um papel da própria organização, e as capacidades saem de
``op.papel_permissao`` — as mesmas que um usuário daquele papel teria. O escopo continua
resolvido por ``shared/scope.py`` (carteira ∩ membership ∩ licença). Uma credencial é, por
construção, um portador de identidade: não existe caminho por onde ela amplie permissão,
porque não há coluna que a expresse.

**Segredo.** Só o hash é persistido (``pbkdf2_sha256``, o mesmo de ``op.usuario``), e o
``prefixo`` público existe para localizar a linha sem varrer a tabela verificando hash de
todas — uma comparação por linha é o que transforma autenticação em ataque de tempo. O
segredo em claro aparece **uma vez**, na resposta da criação, e nunca mais.

**RLS.** É dado do tenant (quem pode falar em nome de quem), no mesmo padrão de
``op.ia_tool_call``. A autenticação em si roda no plano de controle (``is_admin=on``),
como o login: é preciso achar a linha **antes** de saber a organização — e é justamente
esse passo que fixa a organização usada em todo o resto da requisição.

**Reversibilidade.** ``downgrade`` derruba política, índices e tabela. Nada além dela é
tocado: reverter revoga o acesso externo e não perde dado fiscal nem histórico de
auditoria (``op.ia_tool_call`` é independente).

**Seed:** nenhum. Não há dado de referência aqui — credencial é emitida por um
administrador da organização em ``POST /admin/mcp/credenciais``. Tabela vazia após a
migração é o estado correto.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046_mcp_credencial"
down_revision: str | None = "0045_dicionario_semantico"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

_RLS_PREDICATE = (
    "coalesce(current_setting('app.is_admin', true), 'off') = 'on' "
    "OR org_id = nullif(current_setting('app.current_org_id', true), '')::uuid"
)


def upgrade() -> None:
    op.create_table(
        "mcp_credencial",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column(
            "nome",
            sa.Text(),
            nullable=False,
            comment="Rótulo do cliente MCP (para revogar o certo quando houver vários).",
        ),
        sa.Column(
            "prefixo",
            sa.String(length=16),
            nullable=False,
            comment="Parte pública da credencial — localiza a linha sem varrer hashes.",
        ),
        sa.Column("segredo_hash", sa.Text(), nullable=False),
        sa.Column(
            "papel_id",
            sa.Uuid(),
            nullable=False,
            comment="Papel da organização de onde saem as capacidades RBAC da credencial.",
        ),
        sa.Column("criado_por", sa.Uuid(), nullable=True),
        sa.Column(
            "escopo_ibges",
            postgresql.JSONB(),
            nullable=True,
            comment="Restrição opcional a um subconjunto da carteira; NULL ⇒ carteira inteira.",
        ),
        sa.Column(
            "expira_em",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Vencimento; NULL ⇒ sem prazo. Conferido por data, não por job.",
        ),
        sa.Column("revogada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_uso_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["op.organizacao.id"], name="fk_mcp_credencial_org", ondelete="CASCADE"
        ),
        # CASCADE, não SET NULL: sem papel não há capacidade, e uma credencial sem
        # capacidade seria uma identidade válida que não pode nada — um estado que só
        # produz diagnóstico confuso. Apagar o papel revoga quem falava por ele.
        sa.ForeignKeyConstraint(
            ["papel_id"], ["op.papel.id"], name="fk_mcp_credencial_papel", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["criado_por"], ["op.usuario.id"], name="fk_mcp_credencial_usuario",
            ondelete="SET NULL",
        ),
        schema="op",
    )
    # Único global: o prefixo é a chave de busca da autenticação, que roda antes de a
    # organização ser conhecida. Colisão entre tenants tornaria a busca ambígua.
    op.create_index(
        "uq_mcp_credencial_prefixo", "mcp_credencial", ["prefixo"], unique=True, schema="op"
    )
    op.create_index("ix_mcp_credencial_org_id", "mcp_credencial", ["org_id"], schema="op")
    op.execute("ALTER TABLE op.mcp_credencial ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE op.mcp_credencial FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY mcp_credencial_tenant_isolation ON op.mcp_credencial "
        f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
    )
    # A 0044 quebrou 6 testes por criar objeto sem conceder acesso à role da aplicação.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON op.mcp_credencial TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS mcp_credencial_tenant_isolation ON op.mcp_credencial")
    op.execute("ALTER TABLE op.mcp_credencial DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_mcp_credencial_org_id", table_name="mcp_credencial", schema="op")
    op.drop_index("uq_mcp_credencial_prefixo", table_name="mcp_credencial", schema="op")
    op.drop_table("mcp_credencial", schema="op")
