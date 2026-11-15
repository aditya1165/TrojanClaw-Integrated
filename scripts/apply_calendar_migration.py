"""Inspect/apply only the additive Calendar approval migration in backend/.env."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import get_database


def main():
    database = get_database()
    try:
        with database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute("""SELECT to_regclass('public.app_users'),
                to_regclass('supabase_migrations.schema_migrations'),
                to_regclass('public.google_calendar_connections'),
                to_regclass('public.google_calendar_oauth_states'),
                to_regclass('public.calendar_proposals')""")
            users, history, *tables = cursor.fetchone()
            print(f"Account profiles present: {bool(users)}")
            print(f"Calendar tables present: {sum(bool(table) for table in tables)}/3")
            if "--apply" not in sys.argv:
                return
            if not users:
                raise RuntimeError("Apply the base account migrations first.")
            version = "20261120000000"
            if history:
                cursor.execute("SELECT 1 FROM supabase_migrations.schema_migrations WHERE version = %s", (version,))
                if cursor.fetchone():
                    if not all(tables):
                        raise RuntimeError("Calendar migration is recorded but tables are missing. Inspect before continuing.")
                    print("Calendar migration already recorded; no changes needed.")
                    return
            if any(tables):
                raise RuntimeError("Calendar tables exist without migration history. Inspect before applying.")
            sql = (ROOT / "supabase/migrations/20261120000000_calendar_approval.sql").read_text(encoding="utf-8")
            cursor.execute(sql)
            if history:
                cursor.execute("INSERT INTO supabase_migrations.schema_migrations (version, name, statements) VALUES (%s, %s, %s)",
                               (version, "calendar_approval", [sql]))
        print("Calendar approval migration committed.")
    finally:
        database.close()


if __name__ == "__main__":
    main()
