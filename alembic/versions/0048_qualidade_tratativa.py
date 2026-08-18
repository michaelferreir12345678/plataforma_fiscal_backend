"""Sprint Q1 — a tratativa de uma falha de qualidade: o que se fez e como terminou.

A Sprint 26 entregou a metade que **detecta** (``gold.data_quality_check``). Faltava a
outra: o que o gestor faz com a falha. Sem registro de tratativa, o mesmo caso é triado do
zero a cada visita — e um aviso permanente que ninguém consegue encerrar é um aviso que
todos aprendem a ignorar, que é o pior desfecho possível para um selo de qualidade.

Fica em ``op`` (e não em ``gold``) por uma razão de fronteira: o **veredito** é dado
público e compartilhado — a mesma falha do mesmo ente vale para toda organização que o
acompanha. Já a **decisão sobre o que fazer** é operacional e privada: quem analisou, o
que aplicou, com que justificativa aceitou. Duas consultorias que acompanham o mesmo
município podem chegar a leituras diferentes sobre a mesma divergência.

Revision ID: 0048_qualidade_tratativa
Revises: 0047_conversa_multiturno
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048_qualidade_tratativa"
down_revision: str | None = "0047_conversa_multiturno"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")

_RLS_PREDICATE = (
    "coalesce(current_setting('app.is_admin', true), 'off') = 'on' "
    "OR org_id = nullif(current_setting('app.current_org_id', true), '')::uuid"
)


def upgrade() -> None:
    op.create_table(
        "qualidade_tratativa",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(),
            sa.ForeignKey("op.organizacao.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # A chave de negócio é a mesma do veredito, MENOS a versão da entrega. É
        # deliberado: uma retificação cria veredito novo (0044/A26), mas a tratativa
        # acompanha o **caso** — senão cada retificação zeraria a análise já feita e o
        # gestor recomeçaria a triagem de um problema que já conhece.
        sa.Column("check_codigo", sa.Text(), nullable=False),
        sa.Column("cod_ibge", sa.String(length=7), nullable=True),
        sa.Column("periodo", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="aberta",
            comment="aberta | diagnosticada | acao_aplicada | resolvida | aceita_como_fato",
        ),
        sa.Column(
            "classe",
            sa.String(length=12),
            nullable=True,
            comment=(
                "De quem é o número que não fechou: plataforma | fonte | misto | cobertura. "
                "É o que decide qual ação pode ser oferecida."
            ),
        ),
        sa.Column(
            "diagnostico",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Evidência que sustenta a classe — os dois lados e o que a fonte tem.",
        ),
        sa.Column(
            "justificativa",
            sa.Text(),
            nullable=True,
            comment=(
                "Obrigatória para aceitar como fato da fonte. Aceitar NÃO apaga o selo: "
                "ele passa a exibir este motivo e quem o assinou."
            ),
        ),
        sa.Column(
            "tentativas",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
            comment=(
                "Histórico de ações aplicadas e do veredito que cada uma produziu. Sem "
                "isto, uma falha que resiste a três reprocessamentos parece nunca ter "
                "sido tratada."
            ),
        ),
        sa.Column(
            "usuario_id",
            sa.Uuid(),
            sa.ForeignKey("op.usuario.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('aberta', 'diagnosticada', 'acao_aplicada', 'resolvida', "
            "'aceita_como_fato')",
            name="ck_qualidade_tratativa_status",
        ),
        sa.CheckConstraint(
            "classe IS NULL OR classe IN ('plataforma', 'fonte', 'misto', 'cobertura')",
            name="ck_qualidade_tratativa_classe",
        ),
        # Aceitar como fato sem dizer por quê seria silenciar a divergência com um clique.
        sa.CheckConstraint(
            "status <> 'aceita_como_fato' OR (justificativa IS NOT NULL AND "
            "length(btrim(justificativa)) >= 10)",
            name="ck_qualidade_tratativa_justificativa",
        ),
        sa.UniqueConstraint(
            "org_id",
            "check_codigo",
            "cod_ibge",
            "periodo",
            name="uq_qualidade_tratativa_caso",
        ),
        schema="op",
    )
    op.create_index(
        "ix_qualidade_tratativa_status",
        "qualidade_tratativa",
        ["org_id", "status"],
        schema="op",
    )

    # RLS pelo mesmo padrão das demais tabelas de `op`.
    op.execute("ALTER TABLE op.qualidade_tratativa ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE op.qualidade_tratativa FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY qualidade_tratativa_tenant_isolation ON op.qualidade_tratativa "
        f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
    )
    # A 0044 quebrou 6 testes por criar objeto sem conceder acesso à role da aplicação.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON op.qualidade_tratativa TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS qualidade_tratativa_tenant_isolation ON op.qualidade_tratativa"
    )
    op.drop_index("ix_qualidade_tratativa_status", table_name="qualidade_tratativa", schema="op")
    op.drop_table("qualidade_tratativa", schema="op")
