"""Check the Piazza provider migration; --apply commits it to the configured DB."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import get_database

VERSION = "20261126000000"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    database = get_database()
    try:
        with database.connection() as connection, connection.cursor() as cursor:
            if not args.apply:
                cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute("SELECT to_regclass('public.user_connector_sessions'), to_regclass('supabase_migrations.schema_migrations')")
            table, history = cursor.fetchone()
            if not table:
                raise RuntimeError("Apply the hosted connector migration first.")
            cursor.execute("""SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = 'public.user_connector_sessions'::regclass
                  AND conname = 'user_connector_sessions_provider_check' AND contype = 'c'""")
            constraint = cursor.fetchone()
            if not constraint or any(name not in constraint[0] for name in ("brightspace", "handshake")):
                raise RuntimeError("Unexpected provider constraint; inspect before applying.")
            if history:
                cursor.execute("SELECT 1 FROM supabase_migrations.schema_migrations WHERE version = %s", (VERSION,))
                if cursor.fetchone():
                    if "piazza" not in constraint[0]:
                        raise RuntimeError("Migration history and provider constraint disagree.")
                    print("Piazza migration already recorded.")
                    return
            if "piazza" in constraint[0]:
                raise RuntimeError("Piazza constraint exists without migration history; inspect before applying.")
            if not args.apply:
                print("Piazza migration pending; prerequisites passed. No data changed.")
                return
            sql = (ROOT / "supabase/migrations/20261126000000_piazza_connector.sql").read_text(encoding="utf-8")
            cursor.execute(sql)
            if history:
                cursor.execute("INSERT INTO supabase_migrations.schema_migrations (version, name, statements) VALUES (%s, %s, %s)",
                               (VERSION, "piazza_connector", [sql]))
        print("Piazza migration committed.")
    finally:
        database.close()


if __name__ == "__main__":
    main()
