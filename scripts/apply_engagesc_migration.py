"""Apply only the reviewed EngageSC schema change to the configured database."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import get_database


def main():
    database = get_database()
    try:
        with database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.campus_events'), to_regclass('supabase_migrations.schema_migrations'), to_regclass('public.campus_event_sources')")
            events, history, sources = cursor.fetchone()
            if not events:
                raise RuntimeError("The core academic schema must be applied first.")
            if history:
                cursor.execute("SELECT 1 FROM supabase_migrations.schema_migrations WHERE version = '20261117000000'")
                if cursor.fetchone():
                    print("EngageSC migration already applied.")
                    return
            if sources:
                raise RuntimeError("Source table already exists without recorded migration history; inspect first.")
            sql = (ROOT / "supabase/migrations/20261117000000_engagesc_public_events.sql").read_text(encoding="utf-8")
            cursor.execute(sql)
            if history:
                cursor.execute("INSERT INTO supabase_migrations.schema_migrations (version, name, statements) VALUES (%s, %s, %s)",
                               ("20261117000000", "engagesc_public_events", [sql]))
        print("EngageSC migration committed.")
    finally:
        database.close()


if __name__ == "__main__":
    main()
