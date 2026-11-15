"""Scrapes the configured USC pages into the campus `documents` table.

Run from backend/: python -m app.scraper.run_all
"""

import os
import sys

# Ensure we can import from app
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from app.config import get_settings
from app.scraper.usc_pages import USC_PAGES
from app.scraper.chunker import fetch_html, clean_html, chunk_text
from app.database import connect_database
from app.scraper.db_schema import validate_documents_schema
from app.services.embeddings import EmbeddingService


def run_scraper_pipeline():
    # 1. Validate environment
    settings = get_settings()
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is not set in backend/.env.")
    embeddings = EmbeddingService(settings)

    print("Connecting to Postgres database...")
    conn = connect_database()
    cur = conn.cursor()

    validate_documents_schema(cur)

    
    total_inserted = 0

    print("Starting TrojanAI scraper pipeline...")
    
    # 2. Iterate through each configured USC page
    for page in USC_PAGES:
        url = page["url"]
        print(f"\nProcessing {url} ...")
        
        # Fetch & Clean
        raw_html = fetch_html(url)
        if not raw_html:
            print(f"  -> Skipped (Failed to fetch HTML)")
            continue
            
        clean_txt = clean_html(raw_html)
        if not clean_txt:
            print(f"  -> Skipped (No text extracted)")
            continue
            
        # Chunk
        chunks = chunk_text(clean_txt, metadata=page, chunk_size=400, overlap=50)
        print(f"  -> Extracted {len(chunks)} chunks.")
        
        # 3. Embed (one batched request per page) & Insert
        try:
            vectors = embeddings.embed_documents([chunk["chunk_text"] for chunk in chunks])
        except Exception as e:
            print(f"  -> Skipped (embedding failed: {type(e).__name__})")
            continue

        for idx, (chunk_data, embedding) in enumerate(zip(chunks, vectors)):
            try:
                
                # Upsert to Supabase
                # Convert the float list to a string formatted as a Postgres array or pgvector literal
                vec_str = "[" + ",".join(map(str, embedding)) + "]"
                
                cur.execute(
                    """
                    INSERT INTO documents (
                        source_url,
                        page_title,
                        data_type,
                        campus,
                        chunk_text,
                        embedding,
                        chunk_index,
                        last_scraped
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (source_url, chunk_index)
                    DO UPDATE SET
                        page_title = EXCLUDED.page_title,
                        data_type = EXCLUDED.data_type,
                        campus = EXCLUDED.campus,
                        chunk_text = EXCLUDED.chunk_text,
                        embedding = EXCLUDED.embedding,
                        last_scraped = NOW()
                    """,
                    (
                        chunk_data["source_url"],
                        chunk_data["page_title"],
                        chunk_data["data_type"],
                        chunk_data["campus"],
                        chunk_data["chunk_text"],
                        vec_str,
                        chunk_data.get("chunk_index", idx),
                    )
                )
                conn.commit()
                total_inserted += 1
                print(f"    - Inserted chunk {idx+1}/{len(chunks)}")
            except Exception as e:
                print(f"    - Error inserting chunk {idx+1}: {e}")
                conn.rollback()
                
    cur.close()
    conn.close()

    print(f"\nPipeline finished. Total chunks inserted: {total_inserted}")

if __name__ == "__main__":
    run_scraper_pipeline()
