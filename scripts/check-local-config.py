"""Local launcher preflight. Print missing setting names, never their values."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from app.config import get_settings
from app.database import get_database


def main():
    settings = get_settings()
    required = {'DATABASE_URL': settings.database_url,
                'GEMINI_API_KEY': settings.gemini_api_key,
                'ANTHROPIC_API_KEY': settings.anthropic_api_key}
    missing = [name for name, value in required.items() if not value or 'your-' in value]
    if missing:
        raise ValueError('Fill in backend/.env: ' + ', '.join(missing))
    if not settings.browserbase_api_key or not settings.browserbase_project_id:
        print('Remote sign-in needs BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID in backend/.env.')
    if not settings.connector_encryption_keys:
        print('Remote sign-in needs a durable CONNECTOR_ENCRYPTION_KEYS value in backend/.env.')
    database = get_database()
    try:
        with database.connection() as connection, connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
            for table in ('app_users', 'account_sessions', 'user_connector_sessions', 'remote_usc_attempts',
                          'auth_rate_limits', 'google_calendar_connections', 'google_calendar_oauth_states',
                          'calendar_proposals', 'conversations', 'conversation_messages'):
                cursor.execute('SELECT to_regclass(%s)', ('public.' + table,))
                if not cursor.fetchone()[0]:
                    raise ValueError('Database setup is incomplete. Apply the migrations described in DEPLOYMENT.md first.')
    finally:
        database.close()
    print('Configuration and database checks passed.')


if __name__ == '__main__':
    try:
        main()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print('Local setup check failed. Check backend/.env and database connectivity; no database data was changed.', file=sys.stderr)
        raise SystemExit(1) from None
