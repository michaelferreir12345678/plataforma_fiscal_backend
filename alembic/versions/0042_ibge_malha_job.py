"""Registra a malha do IBGE como fonte de ingestão durável.

Revision ID: 0042_ibge_malha_job
Revises: 0041_sprinte1_isolamento_qual
Create Date: 2026-08-07

A tabela gold.geo_malha_uf já existe desde a Sprint 23. Esta migration acrescenta a
partição bronze, atualiza o catálogo da integração e corrige a linhagem para o fluxo
direto bronze -> gold usado pelo mapa.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Sequence

from alembic import op

revision: str = "0042_ibge_malha_job"
down_revision: str | None = "0041_sprinte1_isolamento_qual"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "plataforma_app")
_RAW_COLUNAS = (
    "fonte, ano, periodo, cod_ibge, versao, ingerido_em, hash_payload, payload"
)


def _sql_literal(valor: str | None) -> str:
    if valor is None:
        return "NULL"
    return "'" + valor.replace("'", "''") + "'"


def _substituir_arestas_malha(
    arestas: tuple[tuple[str, str, str, str | None], ...],
) -> None:
    valores: list[str] = []
    for origem, destino, tipo, nota in arestas:
        edge_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"lineage:{tipo}:{origem}:{destino}")
        )
        detalhe = json.dumps({"nota": nota}, ensure_ascii=False) if nota else None
        valores.append(
            "(" + ", ".join(
                (
                    f"{_sql_literal(edge_id)}::uuid",
                    _sql_literal(origem),
                    _sql_literal(destino),
                    _sql_literal(tipo),
                    f"{_sql_literal(detalhe)}::jsonb",
                )
            ) + ")"
        )

    op.execute(
        f"""
        DO $migration$
        DECLARE
            lineage_malha_existia boolean;
        BEGIN
            SELECT EXISTS (
                SELECT 1
                FROM gold.lineage_edge
                WHERE origem IN (
                    'bronze.ibge_malha', 'silver.ibge_malha',
                    'gold.dim_malha', 'gold.geo_malha_uf',
                    'GET /uf/{{uf}}/malha', 'GET /geo/malha/{{uf}}'
                )
            ) INTO lineage_malha_existia;

            DELETE FROM gold.lineage_edge
            WHERE (origem = 'bronze.ibge_malha' AND destino = 'silver.ibge_malha')
               OR (origem = 'silver.ibge_malha' AND destino = 'gold.dim_malha')
               OR (origem = 'gold.dim_malha' AND destino = 'GET /uf/{{uf}}/malha')
               OR (origem = 'GET /uf/{{uf}}/malha' AND destino = '/carteira')
               OR (origem = 'bronze.ibge_malha' AND destino = 'gold.geo_malha_uf')
               OR (origem = 'gold.geo_malha_uf' AND destino = 'GET /geo/malha/{{uf}}')
               OR (origem = 'GET /geo/malha/{{uf}}' AND destino = '/carteira');

            IF lineage_malha_existia THEN
                INSERT INTO gold.lineage_edge (id, origem, destino, tipo, detalhe)
                VALUES {", ".join(valores)}
                ON CONFLICT ON CONSTRAINT uq_lineage_edge_chave
                DO UPDATE SET detalhe = EXCLUDED.detalhe;
            END IF;
        END
        $migration$
        """
    )


def upgrade() -> None:
    # A primeira subida não encontra linhas dessa fonte. O estágio torna o ciclo
    # downgrade -> upgrade igualmente seguro: nesse caso elas estarão na DEFAULT.
    op.execute("LOCK TABLE bronze.raw_payload IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE bronze.raw_payload_default IN ACCESS EXCLUSIVE MODE")
    op.execute(
        f"""
        CREATE TEMP TABLE raw_payload_ibge_malha_0042 ON COMMIT DROP AS
        SELECT {_RAW_COLUNAS}
        FROM bronze.raw_payload_default
        WHERE fonte = 'ibge_malha'
        """
    )
    op.execute("DELETE FROM bronze.raw_payload_default WHERE fonte = 'ibge_malha'")
    op.execute(
        "CREATE TABLE bronze.raw_payload_ibge_malha "
        "PARTITION OF bronze.raw_payload FOR VALUES IN ('ibge_malha')"
    )
    op.execute(
        f"""
        INSERT INTO bronze.raw_payload ({_RAW_COLUNAS})
        SELECT {_RAW_COLUNAS}
        FROM raw_payload_ibge_malha_0042
        """
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE "
        f"ON bronze.raw_payload_ibge_malha TO {APP_ROLE}"
    )
    op.drop_constraint(
        "ck_lineage_edge_tipo", "lineage_edge", schema="gold", type_="check"
    )
    op.create_check_constraint(
        "ck_lineage_edge_tipo",
        "lineage_edge",
        "tipo IN ('fonte_bronze', 'bronze_silver', 'bronze_gold', 'silver_gold', "
        "'gold_endpoint', 'endpoint_pagina')",
        schema="gold",
    )
    _substituir_arestas_malha(
        (
            (
                "bronze.ibge_malha",
                "gold.geo_malha_uf",
                "bronze_gold",
                "GeoJSON municipal vigente por UF",
            ),
            ("gold.geo_malha_uf", "GET /geo/malha/{uf}", "gold_endpoint", None),
            ("GET /geo/malha/{uf}", "/carteira", "endpoint_pagina", None),
        )
    )
    op.execute(
        """
        UPDATE op.integracao
        SET nome = 'IBGE — População, PIB e malhas',
            descricao = 'População, PIB e malha geográfica municipal por UF.',
            fontes = '["ibge_populacao", "ibge_pib", "ibge_malha"]'::jsonb,
            atualizado_em = now()
        WHERE codigo = 'IBGE'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE op.integracao
        SET nome = 'IBGE — População e PIB',
            descricao = 'População e PIB municipal (enriquecimento de dim_ente e coortes).',
            fontes = '["ibge_populacao", "ibge_pib"]'::jsonb,
            atualizado_em = now()
        WHERE codigo = 'IBGE'
        """
    )
    _substituir_arestas_malha(
        (
            (
                "bronze.ibge_malha",
                "silver.ibge_malha",
                "bronze_silver",
                "normalização e tipagem; idempotente por versão de entrega",
            ),
            ("silver.ibge_malha", "gold.dim_malha", "silver_gold", "geometria por UF"),
            ("gold.dim_malha", "GET /uf/{uf}/malha", "gold_endpoint", None),
            ("GET /uf/{uf}/malha", "/carteira", "endpoint_pagina", None),
        )
    )
    op.drop_constraint(
        "ck_lineage_edge_tipo", "lineage_edge", schema="gold", type_="check"
    )
    op.create_check_constraint(
        "ck_lineage_edge_tipo",
        "lineage_edge",
        "tipo IN ('fonte_bronze', 'bronze_silver', 'silver_gold', 'gold_endpoint', "
        "'endpoint_pagina')",
        schema="gold",
    )
    # Preserva o bronze imutável: sem a partição dedicada, o roteamento volta à DEFAULT.
    op.execute(
        "ALTER TABLE bronze.raw_payload "
        "DETACH PARTITION bronze.raw_payload_ibge_malha"
    )
    op.execute(
        f"""
        INSERT INTO bronze.raw_payload ({_RAW_COLUNAS})
        SELECT {_RAW_COLUNAS}
        FROM bronze.raw_payload_ibge_malha
        """
    )
    op.execute("DROP TABLE bronze.raw_payload_ibge_malha")
    op.execute("DELETE FROM gold.mart_cobertura_fonte WHERE fonte = 'ibge_malha'")
    op.execute("DELETE FROM gold.catalogo_fonte WHERE fonte = 'ibge_malha'")
