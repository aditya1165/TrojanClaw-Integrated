from psycopg2.extensions import cursor


def validate_documents_schema(cur: cursor) -> None:
    """
    Fail clearly when required migrations have not been deployed.

    Schema creation belongs in supabase/migrations, not application runtime code.
    """
    cur.execute(
        """
        SELECT
            to_regclass('public.documents'),
            EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')
        """
    )
    documents_table, vector_installed = cur.fetchone()
    if documents_table is None or not vector_installed:
        raise RuntimeError(
            "Database schema is missing. Run `npx supabase db push --remote` first."
        )

