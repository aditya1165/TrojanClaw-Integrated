"""Inspect/apply only the additive password-auth migration using backend/.env.

Run with the backend Python environment; pass --apply to commit the migration.
Existing Supabase migration history is updated in the same transaction.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import get_database


def main() -> None:
    database = get_database()
    try:
        with database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('public.app_users'), to_regclass('public.account_sessions'), to_regclass('supabase_migrations.schema_migrations')")
                users, sessions, history = cursor.fetchone()
                print(f"Account profiles present: {bool(users)}")
                print(f"Authentication tables present: {bool(sessions)}")
                if "--apply" not in sys.argv:
                    return
                if not users:
                    raise RuntimeError("Apply the existing base schema migrations first.")
                version = "20261116000000"
                if history:
                    cursor.execute("SELECT 1 FROM supabase_migrations.schema_migrations WHERE version = %s", (version,))
                    if cursor.fetchone():
                        print("Authentication migration already recorded; no changes needed.")
                        return
                if sessions:
                    raise RuntimeError("Auth tables already exist without migration history; inspect before applying.")
                sql = (ROOT / "supabase/migrations/20261116000000_password_auth.sql").read_text(encoding="utf-8")
                cursor.execute(sql)
                if history:
                    cursor.execute(
                        "INSERT INTO supabase_migrations.schema_migrations (version, name, statements) VALUES (%s, %s, %s)",
                        (version, "password_auth", [sql]),
                    )
        print("Authentication migration committed.")
    finally:
        database.close()


if __name__ == "__main__":
    main()
