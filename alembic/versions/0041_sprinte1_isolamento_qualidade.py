"""Sprint E1 — versão da entrega no check de qualidade e o refresh de carteira como job.

Revision ID: 0041_sprinte1_isolamento_qual
Revises: 0040_sprintg1_capacidade_editar
Create Date: 2026-08-06

Duas mudanças aditivas e reversíveis, ambas de achados **confirmados no código** pela
frente P4 da auditoria A0R.

## A26 — o check de qualidade não guardava a versão conferida

``gold.data_quality_check`` tinha chave única ``(check, fonte, ente, período)``. Reexecutar
o check depois de uma retificação **sobrescrevia** o resultado da versão anterior: o painel
dizia "ok" e ninguém sabia se aquele "ok" se referia ao número novo ou ao velho. É a mesma
família do A14/A15 — *versão que existe, vigência que não se declara* —, agora na
verificação em vez de no valor.

A coluna ``versao_entrega`` entra na chave. A retificação passa a **criar linha nova**, o
histórico de vereditos acompanha o histórico do dado, e a leitura (painel e selo) filtra o
veredito mais recente por chave — sem isso, uma falha de entrega já retificada continuaria
selando a página.

``NOT NULL DEFAULT '-'``, e não ``NULL``: em PostgreSQL, ``NULL`` é distinto de ``NULL``
numa UNIQUE, então um NULL faria o *upsert* nunca conflitar e empilhar uma linha por
execução — exatamente o oposto do que se quer para o check de atualidade, que não se
ancora em entrega nenhuma.

O índice ``ix_data_quality_check_chave`` acompanha a mudança: o filtro de "veredito
vigente" é uma subconsulta correlacionada pela chave, e sem índice ela varreria a tabela
uma vez por linha.

## Refresh de carteira como job durável

``POST /carteira/refresh`` percorria o escopo inteiro dentro do handler HTTP — 5.598
iterações para uma licença global. O trabalho passou a nascer em ``op.carteira_lote_job``,
que já era o caminho de ``POST /carteira/lote/{acao}``; falta apenas a ação ``refresh``
ser aceita pelo ``CheckConstraint``.

## Reversibilidade

O ``downgrade`` recria a chave antiga. Se o acervo já tiver dois vereditos do mesmo check
para versões diferentes — que é justamente o que esta migration passa a permitir —, ele
falha por constraint violada. É o comportamento correto: reverter não pode apagar em
silêncio o resultado de uma verificação real.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_sprinte1_isolamento_qual"
down_revision: str | None = "0040_sprintg1_capacidade_editar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACOES_ANTIGAS = ("relatorio", "alerta")
_ACOES_NOVAS = ("relatorio", "alerta", "refresh")

_CHAVE_ANTIGA = ("check_codigo", "fonte", "cod_ibge", "periodo")
_CHAVE_NOVA = ("check_codigo", "fonte", "cod_ibge", "periodo", "versao_entrega")


def _in_list(col: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{col} in ({joined})"


def upgrade() -> None:
    # --- A26: versão da entrega conferida ---
    op.add_column(
        "data_quality_check",
        sa.Column(
            "versao_entrega",
            sa.Text(),
            nullable=False,
            server_default="-",
            comment="Entrega conferida; '-' quando o check não se ancora numa entrega.",
        ),
        schema="gold",
    )
    op.drop_constraint(
        "uq_data_quality_check_chave", "data_quality_check", schema="gold", type_="unique"
    )
    op.create_unique_constraint(
        "uq_data_quality_check_chave_versao",
        "data_quality_check",
        list(_CHAVE_NOVA),
        schema="gold",
    )
    # Sustenta a subconsulta correlacionada que elege o veredito vigente por chave.
    op.create_index(
        "ix_data_quality_check_chave",
        "data_quality_check",
        ["check_codigo", "fonte", "cod_ibge", "periodo", "executado_em"],
        schema="gold",
    )

    # --- Refresh de carteira como job durável ---
    op.drop_constraint(
        "ck_carteira_lote_job_acao", "carteira_lote_job", schema="op", type_="check"
    )
    op.create_check_constraint(
        "ck_carteira_lote_job_acao",
        "carteira_lote_job",
        _in_list("acao", _ACOES_NOVAS),
        schema="op",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_carteira_lote_job_acao", "carteira_lote_job", schema="op", type_="check"
    )
    # Falha se algum job 'refresh' já existir — reverter não apaga trabalho enfileirado.
    op.create_check_constraint(
        "ck_carteira_lote_job_acao",
        "carteira_lote_job",
        _in_list("acao", _ACOES_ANTIGAS),
        schema="op",
    )

    op.drop_index("ix_data_quality_check_chave", table_name="data_quality_check", schema="gold")
    op.drop_constraint(
        "uq_data_quality_check_chave_versao",
        "data_quality_check",
        schema="gold",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_data_quality_check_chave",
        "data_quality_check",
        list(_CHAVE_ANTIGA),
        schema="gold",
    )
    op.drop_column("data_quality_check", "versao_entrega", schema="gold")
