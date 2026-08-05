"""Sprint G1 — adiciona a capacidade RBAC "editar" ao CheckConstraint (corrige A19).

Revision ID: 0040_sprintg1_capacidade_editar
Revises: 0039_cenario_versionado
Create Date: 2026-08-05

## O defeito (A19)

`forecast/router.py` já condicionava `PATCH /cenarios/{id}` (renomear) e
`DELETE /cenarios/{id}` (arquivar) a `require_capability("editar")` desde o deploy da
Sprint C2. O código dos dois endpoints sempre esteve certo — o problema é que "editar"
nunca existiu em `op.papel_permissao.capacidade`: nem na tupla Python (`tenancy/models.py`),
nem no `CheckConstraint ck_papel_permissao_capacidade` criado pela migration 0001.

Consequência: **nenhum papel, de nenhuma organização, jamais conseguiu ter a capacidade
"editar"** — tentar concedê-la faria o `INSERT` em `op.papel_permissao` violar a
constraint. `principal.has_cap("editar")` era `False` para todo mundo, sempre, e os dois
botões (renomear/arquivar cenário) devolviam 403 desde o primeiro dia.

## A correção

Estende o `CheckConstraint` para aceitar "editar", na mesma lista que
`tenancy/models.CAPACIDADES` agora declara. Aditiva e reversível: nenhuma linha existente
de `op.papel_permissao` é tocada, e o downgrade só falha se algum papel já tiver recebido
"editar" no meio tempo (o que é o comportamento correto — não se pode fazer downgrade
silenciosamente removendo uma permissão concedida).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0040_sprintg1_capacidade_editar"
down_revision: str | None = "0039_cenario_versionado"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ANTIGAS = ("ver", "exportar", "config_alerta", "gerar_relatorio", "usar_ia", "administrar")
_NOVAS = ("ver", "exportar", "editar", "config_alerta", "gerar_relatorio", "usar_ia", "administrar")


def _in_list(col: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{col} in ({joined})"


def upgrade() -> None:
    op.drop_constraint(
        "ck_papel_permissao_capacidade", "papel_permissao", schema="op", type_="check"
    )
    op.create_check_constraint(
        "ck_papel_permissao_capacidade",
        "papel_permissao",
        _in_list("capacidade", _NOVAS),
        schema="op",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_papel_permissao_capacidade", "papel_permissao", schema="op", type_="check"
    )
    # Se algum papel já recebeu "editar", este create falha por constraint violada — de
    # propósito: reverter a migration não pode apagar silenciosamente uma permissão real.
    op.create_check_constraint(
        "ck_papel_permissao_capacidade",
        "papel_permissao",
        _in_list("capacidade", _ANTIGAS),
        schema="op",
    )
