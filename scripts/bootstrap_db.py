"""Bootstrap do Postgres local: cria a role de aplicação e o banco de dados.

Idempotente. A extensão ``ltree``, o schema ``op``, as tabelas e os grants ficam a
cargo da migration Alembic (rodada em seguida com a URL administrativa).

Uso:  python -m scripts.bootstrap_db
"""

from __future__ import annotations

import sys
from pathlib import Path

# Garante que ``app`` (em src/) seja importável quando executado via -m.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg2  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core.config import settings  # noqa: E402


def _connect(dbname: str):  # type: ignore[no-untyped-def]  # psycopg2 connection
    url = make_url(settings.database_admin_url)
    conn = psycopg2.connect(
        host=url.host or "localhost",
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        dbname=dbname,
    )
    conn.autocommit = True
    return conn


def ensure_role_and_database() -> None:
    admin_url = make_url(settings.database_admin_url)
    target_db = settings.db_name
    role = settings.app_db_role
    password = settings.app_db_password
    owner = admin_url.username or "postgres"

    # Conecta ao banco de manutenção 'postgres' para criar role/banco.
    conn = _connect("postgres")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cur.fetchone() is None:
                # Role de aplicação: LOGIN, sem superuser e sem BYPASSRLS (sofre RLS).
                cur.execute(
                    f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %s',
                    (password,),
                )
                print(f"[bootstrap] role '{role}' criada")
            else:
                print(f"[bootstrap] role '{role}' já existe")

            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{target_db}" OWNER "{owner}"')
                print(f"[bootstrap] banco '{target_db}' criado")
            else:
                print(f"[bootstrap] banco '{target_db}' já existe")
    finally:
        conn.close()

    # Garante que a role possa conectar ao banco alvo.
    conn = _connect(target_db)
    try:
        with conn.cursor() as cur:
            cur.execute(f'GRANT CONNECT ON DATABASE "{target_db}" TO "{role}"')
    finally:
        conn.close()
    print("[bootstrap] concluído")


def main() -> None:
    ensure_role_and_database()


if __name__ == "__main__":
    main()
