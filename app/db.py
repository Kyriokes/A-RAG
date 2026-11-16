"""Postgres + pgvector access and migrations.

`connect()` opens a connection with the pgvector types registered.
`python -m app.db migrate` applies migrations/*.sql in name order, once each, and records
them in `schema_migrations` together with their sha256. A migration that changed after
being applied is an error: write a new migration instead.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

from app.settings import ROOT, Settings, get_settings

MIGRATIONS_DIR = ROOT / "migrations"

# Arbitrary constant: serialises concurrent `migrate` runs against the same database.
_MIGRATION_LOCK_ID = 4_242_001


class MigrationError(RuntimeError):
    pass


def connect(
    settings: Settings | None = None, *, register: bool = True, autocommit: bool = False
) -> psycopg.Connection:
    """Open a connection to settings.database_url with pgvector registered.

    `register=False` is only for running migrations on a database where the `vector`
    extension does not exist yet.
    """
    settings = settings or get_settings()
    conn = psycopg.connect(settings.database_url, autocommit=autocommit, connect_timeout=10)
    if register:
        register_vector(conn)
    return conn


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    return sorted(directory.glob("*.sql"))


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations on `conn`; returns the names applied now.

    Tables are created in the first schema of the connection's search_path, which lets
    tests run migrations inside a throwaway schema.
    """
    applied_now: list[str] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_ID,))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name       text PRIMARY KEY,
                sha256     text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        done = dict(conn.execute("SELECT name, sha256 FROM schema_migrations").fetchall())
        for path in migration_files(directory):
            sql = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            if path.name in done:
                if done[path.name] != digest:
                    raise MigrationError(
                        f"La migración {path.name} cambió después de aplicarse. "
                        "No se editan migraciones aplicadas: hay que escribir una nueva."
                    )
                continue
            conn.execute(sql)
            conn.execute(
                "INSERT INTO schema_migrations (name, sha256) VALUES (%s, %s)",
                (path.name, digest),
            )
            applied_now.append(path.name)
    return applied_now


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["migrate"]:
        print("Uso: python -m app.db migrate", file=sys.stderr)
        return 2
    try:
        with connect(register=False) as conn:
            applied = migrate(conn)
    except psycopg.OperationalError as exc:
        print(f"No se pudo conectar a la base (DATABASE_URL): {exc}", file=sys.stderr)
        return 1
    except MigrationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if applied:
        for name in applied:
            print(f"Aplicada: {name}")
    else:
        print("Sin migraciones pendientes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
