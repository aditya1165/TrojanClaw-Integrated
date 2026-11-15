from functools import lru_cache

from supabase import Client, create_client

from app.config import get_supabase_credentials


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """
    Return a cached Supabase client instance.

    Required environment variables:
    - SUPABASE_URL
    - SUPABASE_KEY
    """
    url, key = get_supabase_credentials()
    return create_client(url, key)
