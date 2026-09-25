"""Minimal SQL migration runner.

Applies migrations/NNNN_*.sql in order, each in its own transaction, and records
them in `schema_migrations`. Re-running is safe: applied files are skipped.

    python -m app.migrate                 # apply everything pending
    python -m app.migrate --until 0001    # stop after 0001 (e.g. no pgvector yet)
    python -m app.migrate --status        # list applied / pending
"""

import argparse
import sys
from pathlib import Path

import psycopg

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def _applied(conn: psycopg.Connection) -> set[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version text PRIMARY KEY,"
        " applied_at timestamptz NOT NULL DEFAULT now())"
    )
    conn.commit()
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def migrate(database_url: str | None = None, until: str | None = None) -> list[str]:
    url = database_url or get_settings().libpq_url
    ran: list[str] = []
    with psycopg.connect(url) as conn:
        done = _applied(conn)
        for path in _migration_files():
            version = path.stem
            if until and version.split("_", 1)[0] > until:
                break
            if version in done:
                continue
            print(f"applying {path.name} ...", flush=True)
            try:
                with conn.transaction():
                    conn.execute(path.read_text())
                    conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            except psycopg.Error as exc:
                print(f"FAILED {path.name}: {exc}", file=sys.stderr)
                if "vector" in str(exc):
                    print(
                        "hint: pgvector is not installed on this server. Install it, or run "
                        "`python -m app.migrate --until 0001` to use the structured tables only.",
                        file=sys.stderr,
                    )
                raise SystemExit(1) from exc
            ran.append(version)
    return ran


def status(database_url: str | None = None) -> None:
    url = database_url or get_settings().libpq_url
    with psycopg.connect(url) as conn:
        done = _applied(conn)
    for path in _migration_files():
        print(f"[{'x' if path.stem in done else ' '}] {path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--until", help="apply migrations up to and including this number, e.g. 0001")
    parser.add_argument("--status", action="store_true", help="show applied/pending migrations")
    args = parser.parse_args()
    if args.status:
        status()
        return
    ran = migrate(until=args.until)
    print(f"done: {len(ran)} migration(s) applied" if ran else "up to date")


if __name__ == "__main__":
    main()
