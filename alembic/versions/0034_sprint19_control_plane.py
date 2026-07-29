"""Sprint 19 — control plane da plataforma (superuser, licenças, identidade visual).

Até aqui existiam dois níveis: a organização e o usuário dentro dela. Faltava o de
cima — quem **provisiona** organizações e **libera** o acesso por ente ou por UF. Sem
ele, entrar num cliente novo é rodar SQL na mão, e nada impede que uma organização
enxergue um ente que ela não contratou.

Três mudanças:

- ``op.usuario.is_superuser`` — o operador da plataforma **não pertence a organização
  nenhuma**. É por isso que a flag mora no usuário e não num papel: papel é RBAC dentro
  de uma org, e o superuser existe fora de todas.
- ``op.licenca`` — o que a organização contratou, com vigência e histórico. Suspender
  **não apaga**: o registro continua, com o status trocado, porque "esta org já teve
  acesso a este ente?" é pergunta de auditoria e de cobrança.
- ``op.organizacao.logo_url`` e ``gold.dim_ente.brasao_url`` — identidade visual do
  cabeçalho institucional dos relatórios (Sprint 16). O brasão fica na ``gold`` porque
  é atributo do **ente** (público, compartilhado), não da organização que o monitora.

``op.licenca`` recebe RLS pelo mesmo padrão das demais tabelas do ``op``: a org lê a
própria licença (precisa saber o que contratou e até quando), mas **só o superuser
escreve** — e isso é imposto na borda, em ``/platform``, não por policy, porque a
policy de escrita não distingue "admin do tenant" de "operador da plataforma".

Revision ID: 0034_sprint19_control_plane
Revises: 0033_sprint26_qualidade_lineage
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_sprint19_control_plane"
down_revision: str | None = "0033_sprint26_qualidade_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mesmo predicado de leitura das demais tabelas do ``op``: a sessão fixa a org e o
# modo admin; sem contexto, nega tudo.
_LEITURA_TENANT = (
    "coalesce(current_setting('app.is_admin', true), 'off') = 'on' "
    "OR org_id = nullif(current_setting('app.current_org_id', true), '')::uuid"
)

_TIPOS = "'ente', 'uf', 'global'"
_STATUS = "'ativa', 'suspensa', 'expirada'"


def upgrade() -> None:
    op.add_column(
        "usuario",
        sa.Column(
            "is_superuser",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="Operador da plataforma: existe fora de qualquer organização.",
        ),
        schema="op",
    )
    op.add_column(
        "organizacao",
        sa.Column("logo_url", sa.Text(), nullable=True),
        schema="op",
    )
    op.add_column(
        "dim_ente",
        sa.Column("brasao_url", sa.Text(), nullable=True),
        schema="gold",
    )

    op.create_table(
        "licenca",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("tipo", sa.String(length=8), nullable=False),
        sa.Column(
            "cod_ibge",
            sa.String(length=7),
            nullable=True,
            comment="Obrigatório em tipo=ente; NULL nos demais.",
        ),
        sa.Column(
            "uf",
            sa.String(length=2),
            nullable=True,
            comment="Obrigatório em tipo=uf; NULL nos demais. Guarda o código IBGE da UF.",
        ),
        sa.Column("vigencia_inicio", sa.Date(), nullable=False),
        sa.Column(
            "vigencia_fim",
            sa.Date(),
            nullable=True,
            comment="NULL = sem prazo. Vigência é fechada no fim: vale até este dia, inclusive.",
        ),
        sa.Column("status", sa.String(length=9), nullable=False, server_default="ativa"),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("criada_por", sa.Uuid(), nullable=True),
        sa.Column(
            "criada_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "atualizada_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["op.organizacao.id"], ondelete="CASCADE", name="fk_licenca_org"
        ),
        sa.ForeignKeyConstraint(
            ["criada_por"], ["op.usuario.id"], ondelete="SET NULL", name="fk_licenca_criada_por"
        ),
        sa.CheckConstraint(f"tipo in ({_TIPOS})", name="licenca_tipo_valido"),
        sa.CheckConstraint(f"status in ({_STATUS})", name="licenca_status_valido"),
        # O alvo tem de casar com o tipo: licença de ente sem IBGE, ou de UF sem UF,
        # seria uma licença que não libera nada — e ninguém descobriria até o 403.
        sa.CheckConstraint(
            "(tipo = 'ente' AND cod_ibge IS NOT NULL AND uf IS NULL) "
            "OR (tipo = 'uf' AND uf IS NOT NULL AND cod_ibge IS NULL) "
            "OR (tipo = 'global' AND cod_ibge IS NULL AND uf IS NULL)",
            name="licenca_alvo_coerente",
        ),
        sa.CheckConstraint(
            "vigencia_fim IS NULL OR vigencia_fim >= vigencia_inicio",
            name="licenca_vigencia_coerente",
        ),
        schema="op",
    )
    op.create_index("ix_licenca_org", "licenca", ["org_id", "status"], schema="op")
    op.create_index("ix_licenca_alvo", "licenca", ["tipo", "cod_ibge", "uf"], schema="op")

    op.execute("ALTER TABLE op.licenca ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY licenca_tenant_isolation ON op.licenca "
        f"USING ({_LEITURA_TENANT}) WITH CHECK ({_LEITURA_TENANT})"
    )

    # --- Congelar o acesso vigente como licença -----------------------------
    #
    # A partir daqui "sem licença" significa "sem acesso". Subir esta migration sem
    # preencher a tabela tiraria o acesso de **todas** as organizações existentes no
    # instante do deploy. As duas inserções abaixo transcrevem o escopo que cada org
    # já tinha — nada é concedido além do que ela enxergava ontem.
    #
    # Conta estadual vira licença de UF (era assim que a expansão territorial da
    # Sprint 4 funcionava); as demais viram uma licença por ente da carteira.
    op.execute(
        """
        INSERT INTO op.licenca (id, org_id, tipo, uf, vigencia_inicio, status, observacao)
        SELECT gen_random_uuid(), o.id, 'uf', left(c.cod_ibge, 2), current_date, 'ativa',
               'Migrada da carteira vigente (Sprint 19) — expansão estadual da Sprint 4.'
          FROM op.organizacao o
          JOIN op.carteira_ente c ON c.org_id = o.id
         WHERE o.tipo_conta = 'estado'
         GROUP BY o.id, left(c.cod_ibge, 2)
        """
    )
    op.execute(
        """
        INSERT INTO op.licenca (id, org_id, tipo, cod_ibge, vigencia_inicio, status, observacao)
        SELECT gen_random_uuid(), c.org_id, 'ente', c.cod_ibge, current_date, 'ativa',
               'Migrada da carteira vigente (Sprint 19).'
          FROM op.carteira_ente c
          JOIN op.organizacao o ON o.id = c.org_id
         WHERE o.tipo_conta <> 'estado'
           AND length(c.cod_ibge) = 7
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS licenca_tenant_isolation ON op.licenca")
    op.drop_index("ix_licenca_alvo", table_name="licenca", schema="op")
    op.drop_index("ix_licenca_org", table_name="licenca", schema="op")
    op.drop_table("licenca", schema="op")
    op.drop_column("dim_ente", "brasao_url", schema="gold")
    op.drop_column("organizacao", "logo_url", schema="op")
    op.drop_column("usuario", "is_superuser", schema="op")
