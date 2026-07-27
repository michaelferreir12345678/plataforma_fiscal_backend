"""Redireciona o volume do medallion para um tablespace dedicado (ops local).

O backfill âncora (CE completo, 2021→atual) multiplica o volume do bronze/silver por
~200×. Em máquinas onde o disco padrão do Postgres está saturado, este utilitário move as
tabelas volumosas (bronze/silver) para um tablespace em outro disco e define esse
tablespace como *default* do banco, de modo que as partições/linhas novas do backfill
nasçam lá.

**Não é migration** (localização de tablespace é específica do ambiente e não deve ser
versionada). Idempotente; exige superusuário (usa a ``DATABASE_ADMIN_URL``).

Uso::

    python -m scripts.ensure_tablespace --tablespace fiscal_dados \
        --location D:/pg_tablespace_fiscal
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.core.config import settings  # noqa: E402

# Tabelas que o backfill faz crescer (bronze é o maior; silver acompanha).
_SILVER_TABELAS = (
    "silver.siconfi_rreo",
    "silver.siconfi_rgf",
    "silver.siconfi_dca",
    "silver.siconfi_msc",
    "silver.bcb_indice",
    "silver.tesouro_fpm",
    "silver.fnde_fundeb_repasse",
    "silver.transferencia_generica",
    "silver.tesouro_capag",
    "silver.siops_saude",
    "silver.siope_educacao",
)


def _bronze_partitions(conn) -> list[str]:
    rows = conn.execute(
        text(
            "select schemaname||'.'||tablename from pg_tables "
            "where schemaname='bronze' and tablename like 'raw_payload%'"
        )
    ).scalars()
    return list(rows)


def run(tablespace: str, location: str | None) -> None:
    engine = create_engine(settings.database_admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        exists = conn.execute(
            text("select 1 from pg_tablespace where spcname = :n"), {"n": tablespace}
        ).first()
        if not exists:
            if not location:
                raise SystemExit(
                    f"Tablespace '{tablespace}' não existe e --location não foi informado."
                )
            conn.execute(text(f"CREATE TABLESPACE {tablespace} LOCATION '{location}'"))
            print(f"tablespace criado: {tablespace} -> {location}")

        db = settings.db_name
        conn.execute(text(f'ALTER DATABASE "{db}" SET default_tablespace = {tablespace}'))
        print(f"default_tablespace do banco {db} = {tablespace} (novas tabelas/partições)")

        alvos = list(_SILVER_TABELAS) + _bronze_partitions(conn)
        for tabela in alvos:
            try:
                conn.execute(text(f"ALTER TABLE {tabela} SET TABLESPACE {tablespace}"))
                print(f"movida: {tabela}")
            except Exception as exc:  # noqa: BLE001 (log e segue; tabela pode não existir)
                print(f"pulada: {tabela} ({exc.__class__.__name__})")
    engine.dispose()
    print("ok")


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tablespace", default="fiscal_dados")
    p.add_argument("--location", default=None, help="Cria o tablespace aqui se ainda não existir.")
    return p.parse_args()


if __name__ == "__main__":
    a = _args()
    run(a.tablespace, a.location)
