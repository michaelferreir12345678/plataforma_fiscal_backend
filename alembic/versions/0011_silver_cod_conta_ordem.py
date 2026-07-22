"""Silver: captura cod_conta (slug STN) e linha_seq (ordem da linha) nos relatórios.

Revision ID: 0011_silver_cod_conta_ordem
Revises: 0010_sprint7_pessoal
Create Date: 2026-07-21

O SICONFI **não expõe código numérico hierárquico** (ex.: 1.7.1) no RREO/RGF: o campo
``conta`` é a descrição e ``cod_conta`` é um slug estável do STN (ex.: ``ReceitasCorrentes``).
A hierarquia é derivada da **ordem** das linhas + descrição/slug. Guardamos ``cod_conta``
(identificador estável) e ``linha_seq`` (posição no payload) para reconstruir a árvore.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_silver_cod_conta_ordem"
down_revision: str | None = "0010_sprint7_pessoal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SILVER_RELATORIOS = ("siconfi_rreo", "siconfi_rgf", "siconfi_dca")


def upgrade() -> None:
    for nome in _SILVER_RELATORIOS:
        op.add_column(nome, sa.Column("cod_conta", sa.Text(), nullable=True), schema="silver")
        op.add_column(nome, sa.Column("linha_seq", sa.Integer(), nullable=True), schema="silver")


def downgrade() -> None:
    for nome in _SILVER_RELATORIOS:
        op.drop_column(nome, "linha_seq", schema="silver")
        op.drop_column(nome, "cod_conta", schema="silver")
