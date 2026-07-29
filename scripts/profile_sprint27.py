"""Read-only EXPLAIN ANALYZE evidence for the Sprint 27 analytical budget.

The script selects representative high-cardinality scopes from the connected database,
opens a read-only transaction and prints ten plans as Markdown. It never prints the DSN
or credentials and never changes database state.
"""

from __future__ import annotations

import argparse
import math
import textwrap
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, create_engine, text

from app.core.config import settings


@dataclass(frozen=True)
class Profile:
    name: str
    endpoint: str
    sql: str
    params: dict[str, Any]


def _one(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    row = conn.execute(text(textwrap.dedent(sql)), params or {}).mappings().first()
    if row is None:
        raise RuntimeError("Banco sem volume suficiente para selecionar parametros de perfil.")
    return dict(row)


def _profiles(conn: Connection) -> list[Profile]:
    msc = _one(
        conn,
        """
        SELECT cod_ibge, periodo, count(*) AS n
        FROM gold.mart_msc_rollup
        GROUP BY cod_ibge, periodo
        ORDER BY n DESC
        LIMIT 1
        """,
    )
    msc_account = _one(
        conn,
        """
        SELECT cod_conta, count(*) AS n
        FROM gold.mart_msc_rollup
        WHERE cod_ibge = :cod_ibge
        GROUP BY cod_conta
        ORDER BY n DESC
        LIMIT 1
        """,
        {"cod_ibge": msc["cod_ibge"]},
    )
    uf_ranking = _one(
        conn,
        """
        SELECT substr(r.cod_ibge, 1, 2) AS uf, r.periodo_ref AS periodo, count(*) AS n
        FROM gold.fato_rcl AS r
        JOIN gold.dim_entrega AS d
          ON d.cod_ibge = r.cod_ibge
         AND d.periodo = r.periodo_ref
         AND d.versao_entrega = r.versao_entrega
         AND d.relatorio = 'RREO'
         AND d.vigente IS TRUE
        WHERE length(r.cod_ibge) = 7
        GROUP BY substr(r.cod_ibge, 1, 2), r.periodo_ref
        ORDER BY n DESC
        LIMIT 1
        """,
    )
    carteira = _one(
        conn,
        """
        SELECT periodo, indicador, count(*) AS n
        FROM gold.mart_carteira
        GROUP BY periodo, indicador
        ORDER BY n DESC
        LIMIT 1
        """,
    )
    benchmark = _one(
        conn,
        """
        SELECT coorte_id, indicador, periodo, snapshot_hash, count(*) AS n
        FROM gold.mart_benchmark
        GROUP BY coorte_id, indicador, periodo, snapshot_hash
        ORDER BY n DESC
        LIMIT 1
        """,
    )
    cobertura = _one(
        conn,
        """
        SELECT fonte, uf, ano, count(*) AS n
        FROM gold.mart_cobertura_fonte
        WHERE uf IS NOT NULL
        GROUP BY fonte, uf, ano
        ORDER BY n DESC
        LIMIT 1
        """,
    )
    dashboard = _one(
        conn,
        """
        SELECT cod_ibge, periodo, count(*) AS n
        FROM gold.mart_indicador
        GROUP BY cod_ibge, periodo
        ORDER BY n DESC
        LIMIT 1
        """,
    )
    receita = _one(
        conn,
        """
        SELECT cod_ibge, periodo, count(*) AS n
        FROM gold.fato_receita
        GROUP BY cod_ibge, periodo
        ORDER BY n DESC
        LIMIT 1
        """,
    )
    despesa = _one(
        conn,
        """
        SELECT cod_ibge, periodo, count(*) AS n
        FROM gold.fato_despesa
        GROUP BY cod_ibge, periodo
        ORDER BY n DESC
        LIMIT 1
        """,
    )

    msc_year = int(str(msc["periodo"])[:4])
    return [
        Profile(
            "01_msc_arvore",
            "GET /entes/{cod_ibge}/msc/arvore",
            """
            SELECT m.*
            FROM gold.mart_msc_rollup AS m
            WHERE m.cod_ibge = :cod_ibge
              AND m.periodo = :periodo
              AND m.parent_conta IS NULL
              AND m.versao_entrega = (
                  SELECT max(v.versao_entrega)
                  FROM gold.mart_msc_rollup AS v
                  WHERE v.cod_ibge = :cod_ibge AND v.periodo = :periodo
              )
            ORDER BY m.cod_conta
            """,
            {"cod_ibge": msc["cod_ibge"], "periodo": msc["periodo"]},
        ),
        Profile(
            "02_msc_matriz_mensal",
            "GET /entes/{cod_ibge}/msc/conta/{codigo}/saldos",
            """
            SELECT m.*
            FROM gold.mart_msc_rollup AS m
            WHERE m.cod_ibge = :cod_ibge
              AND m.ano = :ano
              AND m.cod_conta = :cod_conta
            ORDER BY m.mes
            """,
            {
                "cod_ibge": msc["cod_ibge"],
                "ano": msc_year,
                "cod_conta": msc_account["cod_conta"],
            },
        ),
        Profile(
            "03_ranking_uf",
            "GET /uf/{uf}/ranking",
            """
            SELECT r.cod_ibge, e.nome, r.rcl_12m
            FROM gold.fato_rcl AS r
            JOIN gold.dim_ente AS e ON e.cod_ibge = r.cod_ibge
            JOIN gold.dim_entrega AS d
              ON d.cod_ibge = r.cod_ibge
             AND d.periodo = r.periodo_ref
             AND d.versao_entrega = r.versao_entrega
             AND d.relatorio = 'RREO'
            WHERE substr(r.cod_ibge, 1, 2) = :uf
              AND length(r.cod_ibge) = 7
              AND r.periodo_ref = :periodo
              AND d.vigente IS TRUE
            ORDER BY r.rcl_12m DESC NULLS LAST, r.cod_ibge
            LIMIT 200
            """,
            {
                "uf": uf_ranking["uf"],
                "periodo": uf_ranking["periodo"],
                "indicador": "rcl",
            },
        ),
        Profile(
            "04_carteira_entes",
            "GET /carteira/entes",
            """
            SELECT m.*
            FROM gold.mart_carteira AS m
            WHERE substr(m.cod_ibge, 1, 2) = :uf
              AND m.periodo = :periodo
              AND m.indicador = :indicador
            ORDER BY m.cod_ibge, m.indicador
            """,
            {
                "uf": uf_ranking["uf"],
                "periodo": carteira["periodo"],
                "indicador": carteira["indicador"],
            },
        ),
        Profile(
            "05_benchmark_ranking",
            "GET /benchmark/ranking",
            """
            SELECT b.*
            FROM gold.mart_benchmark AS b
            WHERE b.coorte_id = :coorte_id
              AND b.indicador = :indicador
              AND b.periodo = :periodo
              AND b.snapshot_hash = :snapshot_hash
            ORDER BY b.posicao, b.cod_ibge
            """,
            {
                "coorte_id": benchmark["coorte_id"],
                "indicador": benchmark["indicador"],
                "periodo": benchmark["periodo"],
                "snapshot_hash": benchmark["snapshot_hash"],
            },
        ),
        Profile(
            "06_cobertura_total",
            "GET /admin/ingestion/cobertura (contagem)",
            """
            SELECT count(*)
            FROM (
                SELECT c.fonte, c.cod_ibge, c.ano
                FROM gold.mart_cobertura_fonte AS c
                WHERE c.fonte = :fonte AND c.uf = :uf AND c.ano = :ano
                GROUP BY c.fonte, c.cod_ibge, c.ano
            ) AS grupos
            """,
            {
                "fonte": cobertura["fonte"],
                "uf": cobertura["uf"],
                "ano": cobertura["ano"],
            },
        ),
        Profile(
            "07_cobertura_pagina",
            "GET /admin/ingestion/cobertura (pagina)",
            """
            WITH grupos AS (
                SELECT c.fonte, c.cod_ibge, c.ano
                FROM gold.mart_cobertura_fonte AS c
                WHERE c.fonte = :fonte AND c.uf = :uf AND c.ano = :ano
                GROUP BY c.fonte, c.cod_ibge, c.ano
                ORDER BY c.fonte, c.cod_ibge, c.ano
                LIMIT 100
            )
            SELECT c.*
            FROM gold.mart_cobertura_fonte AS c
            JOIN grupos AS g
              ON g.fonte = c.fonte
             AND g.cod_ibge = c.cod_ibge
             AND g.ano = c.ano
            WHERE c.fonte = :fonte AND c.uf = :uf AND c.ano = :ano
            ORDER BY c.fonte, c.cod_ibge, c.ano, c.periodo
            """,
            {
                "fonte": cobertura["fonte"],
                "uf": cobertura["uf"],
                "ano": cobertura["ano"],
            },
        ),
        Profile(
            "08_dashboard_indicadores",
            "GET /entes/{cod_ibge}/dashboard",
            """
            SELECT m.*
            FROM gold.mart_indicador AS m
            WHERE m.cod_ibge = :cod_ibge AND m.periodo = :periodo
            ORDER BY m.indicador
            """,
            {
                "cod_ibge": dashboard["cod_ibge"],
                "periodo": dashboard["periodo"],
            },
        ),
        Profile(
            "09_receita_arvore",
            "GET /entes/{cod_ibge}/receita/arvore",
            """
            SELECT r.*, d.descricao, d.parent_codigo, d.nivel
            FROM gold.fato_receita AS r
            JOIN gold.dim_origem_receita AS d ON d.codigo = r.origem_codigo
            WHERE r.cod_ibge = :cod_ibge AND r.periodo = :periodo
            ORDER BY d.codigo
            """,
            {
                "cod_ibge": receita["cod_ibge"],
                "periodo": receita["periodo"],
            },
        ),
        Profile(
            "10_despesa_arvore",
            "GET /entes/{cod_ibge}/despesa/arvore",
            """
            SELECT f.*, fn.descricao AS funcao, n.descricao AS natureza
            FROM gold.fato_despesa AS f
            JOIN gold.dim_funcao AS fn ON fn.codigo = f.funcao_codigo
            JOIN gold.dim_natureza AS n ON n.codigo = f.natureza_codigo
            WHERE f.cod_ibge = :cod_ibge AND f.periodo = :periodo
            ORDER BY fn.codigo, n.codigo
            """,
            {
                "cod_ibge": despesa["cod_ibge"],
                "periodo": despesa["periodo"],
            },
        ),
    ]


def _print_latency_profiles(
    conn: Connection,
    profiles: list[Profile],
    *,
    runs: int,
) -> None:
    print("## Query round-trip latency")
    print()
    print("| Query | Runs | P50 ms | P95 ms | Max ms | Rows |")
    print("|---|---:|---:|---:|---:|---:|")
    for profile in profiles:
        statement = text(textwrap.dedent(profile.sql))
        for _ in range(2):
            conn.execute(statement, profile.params).fetchall()
        samples: list[float] = []
        row_count = 0
        for _ in range(runs):
            started = time.perf_counter()
            rows = conn.execute(statement, profile.params).fetchall()
            samples.append((time.perf_counter() - started) * 1000)
            row_count = len(rows)
        ordered = sorted(samples)
        p50 = ordered[math.ceil(runs * 0.50) - 1]
        p95 = ordered[math.ceil(runs * 0.95) - 1]
        print(
            f"| {profile.name} | {runs} | {p50:.3f} | {p95:.3f} | "
            f"{max(samples):.3f} | {row_count} |"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--latency-runs",
        type=int,
        default=0,
        help="Warm up twice, then report query round-trip P50/P95 for N read-only runs.",
    )
    parser.add_argument(
        "--skip-explain",
        action="store_true",
        help="Print only the optional latency table.",
    )
    args = parser.parse_args()
    if args.latency_runs < 0:
        parser.error("--latency-runs must be non-negative")

    engine = create_engine(
        settings.database_admin_url,
        connect_args={"connect_timeout": 5},
        pool_pre_ping=True,
    )
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '30s'"))
        profiles = _profiles(conn)
        print("# Sprint 27 - EXPLAIN ANALYZE (read-only)")
        print()
        print("Generated from the configured host database; DSN and credentials omitted.")
        print()
        if args.latency_runs:
            _print_latency_profiles(conn, profiles, runs=args.latency_runs)
        if args.skip_explain:
            conn.rollback()
            return
        for profile in profiles:
            explain = "EXPLAIN (ANALYZE, BUFFERS, WAL, TIMING, SUMMARY, FORMAT TEXT)\n"
            plan = conn.execute(
                text(explain + textwrap.dedent(profile.sql)),
                profile.params,
            ).scalars()
            print(f"## {profile.name}")
            print()
            print(f"`{profile.endpoint}`")
            print()
            print("```text")
            for line in plan:
                print(line)
            print("```")
            print()
        conn.rollback()


if __name__ == "__main__":
    main()
