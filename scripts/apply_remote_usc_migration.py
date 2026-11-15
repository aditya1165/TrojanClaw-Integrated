"""Check or apply only the remote USC schema change, without exposing credentials.

Run with backend dependencies from backend/, using an absolute script path or
../scripts/apply_remote_usc_migration.py. Default mode is read-only; --apply commits.
"""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import get_database

VERSION = "20261125000000"


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
            cursor.execute("SELECT to_regclass('public.remote_usc_attempts'), to_regclass('supabase_migrations.schema_migrations')")
            table, history = cursor.fetchone()
            if history:
                cursor.execute("SELECT 1 FROM supabase_migrations.schema_migrations WHERE version = %s", (VERSION,))
                if cursor.fetchone():
                    print("Remote USC migration already recorded.")
                    return
            if table:
                raise RuntimeError("Remote USC table exists without migration history; inspect before applying.")
            cursor.execute("SELECT to_regclass('public.user_connector_sessions')")
            if not cursor.fetchone()[0]:
                raise RuntimeError("Apply the hosted connector migration first.")
            cursor.execute("""SELECT count(*) FROM (
                SELECT profile->'usc'->>'brightspace_user_id' FROM public.app_users
                WHERE profile->'usc'->>'brightspace_user_id' IS NOT NULL
                GROUP BY profile->'usc'->>'brightspace_user_id' HAVING count(*) > 1
            ) AS duplicates""")
            if cursor.fetchone()[0]:
                raise RuntimeError("Duplicate USC identities exist. Review accounts before migration; no data changed.")
            if not args.apply:
                print("Remote USC migration is pending; prerequisites passed. No data changed.")
                return
            sql = (ROOT / "supabase/migrations/20261125000000_remote_usc_login.sql").read_text(encoding="utf-8")
            cursor.execute(sql)
            if history:
                cursor.execute("INSERT INTO supabase_migrations.schema_migrations (version, name, statements) VALUES (%s, %s, %s)",
                               (VERSION, "remote_usc_login", [sql]))
        print("Remote USC migration committed.")
    finally:
        database.close()


if __name__ == "__main__":
    main()
