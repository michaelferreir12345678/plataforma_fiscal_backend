"""Desempate determinístico do veredito vigente do check de qualidade.

Revision ID: 0044_dq_check_seq
Revises: 0043_ia_tool_call
Create Date: 2026-08-13

## O defeito

A E1 (A26) elegeu o "veredito vigente" por chave como o de ``executado_em`` mais recente,
com desempate por ``id`` quando os carimbos coincidem. O comentário do próprio
``upsert_check`` reconhecia o risco e o descartava com uma premissa: gravando o carimbo
pelo relógio da aplicação (e não por ``now()`` do PostgreSQL, que é o instante da
transação), "a ordem é a ordem real das execuções".

**A premissa é falsa onde o relógio é grosso.** Medido nesta base, no Windows: 200 chamadas
consecutivas de ``datetime.now(UTC)`` devolveram **o mesmo valor**. Quando dois vereditos da
mesma chave caem no mesmo tique, quem vence é ``uuid4()`` — sorteio.

Alcance medido no acervo antes da correção: **31 de 193 linhas** com carimbo empatado por
chave; dessas, **nenhuma** com status divergente. Ou seja, o sorteio nunca chegou a mudar o
que o gestor vê — mas elegia por moeda, e numa plataforma de governo "ainda não errou" não
é o mesmo que "não erra". Em produção (Linux, relógio de microssegundo) a colisão é rara;
no desenvolvimento (Windows) ela reprovava a suíte por volta de metade das execuções.

## A correção

Uma coluna ``seq`` alimentada por sequência: **ordem de escrita**, que é o fato que o
desempate precisa e que o carimbo de tempo não consegue expressar quando o relógio não
distingue. O desempate deixa de ser aleatório e passa a ser "quem foi gravado depois".

Não se falsifica ``executado_em`` para forçar unicidade (a alternativa considerada): o
carimbo continua sendo o instante real da execução, e a ordem de escrita vira um fato
próprio, ao lado — não uma distorção do primeiro.

Linhas existentes recebem ``seq`` na ordem física da tabela: arbitrária, porém **estável**,
e sem efeito prático porque nenhum empate atual tem veredito divergente.

O ``downgrade`` devolve o desempate por ``id`` — volta a sortear, que era o comportamento
anterior.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_dq_check_seq"
down_revision: str | None = "0043_ia_tool_call"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEQ = "gold.dq_check_seq"
#: Mesmo padrão das demais migrations: a role da aplicação não é dona do objeto e precisa
#: da concessão explícita — sem ela o *upsert* falha com "permission denied for sequence".
APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")


def upgrade() -> None:
    op.execute(sa.text(f"CREATE SEQUENCE IF NOT EXISTS {_SEQ}"))
    # DEFAULT volátil: no PostgreSQL 11+ o ADD COLUMN avalia a expressão por linha, então
    # cada linha existente recebe um valor distinto — é o que dá ordem estável ao acervo.
    op.execute(
        sa.text(
            "ALTER TABLE gold.data_quality_check "
            f"ADD COLUMN seq BIGINT NOT NULL DEFAULT nextval('{_SEQ}')"
        )
    )
    op.execute(sa.text(f"ALTER SEQUENCE {_SEQ} OWNED BY gold.data_quality_check.seq"))
    op.execute(sa.text(f"GRANT USAGE, SELECT ON SEQUENCE {_SEQ} TO {APP_ROLE}"))
    # Sustenta a subconsulta correlacionada que elege o vigente por chave.
    op.create_index(
        "ix_data_quality_check_seq",
        "data_quality_check",
        ["check_codigo", "fonte", "cod_ibge", "periodo", "seq"],
        schema="gold",
    )


def downgrade() -> None:
    op.drop_index("ix_data_quality_check_seq", table_name="data_quality_check", schema="gold")
    op.execute(sa.text("ALTER TABLE gold.data_quality_check DROP COLUMN seq"))
    op.execute(sa.text(f"DROP SEQUENCE IF EXISTS {_SEQ}"))
