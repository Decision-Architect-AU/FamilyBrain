"""Write extracted text into the appropriate schema with embeddings."""
import os
import json
import ollama
import psycopg2
import psycopg2.extras

OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://ollama:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
DB_URL      = os.environ.get("DATABASE_URL")


def _conn():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def _embed(text: str) -> list[float]:
    """Embed text, chunking and averaging if it exceeds the model's context window."""
    client = ollama.Client(host=OLLAMA_URL)
    chunk_size = 4000
    chunks = [text[i:i+chunk_size] for i in range(0, min(len(text), 32000), chunk_size)]
    vectors = []
    for chunk in chunks:
        resp = client.embeddings(model=EMBED_MODEL, prompt=chunk)
        vectors.append(resp["embedding"])
    if len(vectors) == 1:
        return vectors[0]
    # Average the chunk embeddings
    n = len(vectors)
    return [sum(v[i] for v in vectors) / n for i in range(len(vectors[0]))]


def _vec_str(vec: list[float]) -> str:
    return "[" + ",".join(str(v) for v in vec) + "]"


def ingest_personal(text: str, filename: str, tags: list[str] | None = None) -> int:
    """Write to personal.note. Returns inserted id."""
    vec = _embed(text)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO personal.note (source, body, tags, embedding)
                   VALUES ('file', %s, %s, %s::vector) RETURNING id""",
                (text, tags or [filename], _vec_str(vec)),
            )
            row = cur.fetchone()
        conn.commit()
    return row["id"]


def ingest_property(text: str, filename: str) -> int:
    """Write to property_deals.property as a note embedded in a new raw property row,
    or if it looks like research rather than a listing, write to a scraped_listing as raw text."""
    vec = _embed(text[:2000])
    # Store as a scraped listing with source='file' so the dedup worker can handle it
    with _conn() as conn:
        with conn.cursor() as cur:
            # Create a placeholder scrape job for file ingestion
            cur.execute(
                """INSERT INTO property_deals.scrape_job (source, search_params, status, started_at, finished_at, listings_found, listings_new)
                   VALUES ('file_ingest', '{}', 'done', now(), now(), 1, 1) RETURNING id""",
            )
            job_id = cur.fetchone()["id"]

            cur.execute(
                """INSERT INTO property_deals.scraped_listing (job_id, source, external_id, raw_data)
                   VALUES (%s, 'file_ingest', %s, %s)
                   ON CONFLICT (source, external_id) DO UPDATE SET raw_data = EXCLUDED.raw_data
                   RETURNING id""",
                (job_id, filename, json.dumps({"filename": filename, "text": text[:5000]})),
            )
            row = cur.fetchone()
        conn.commit()
    return row["id"]


def ingest_decision(text: str, filename: str) -> int:
    """Write to decision_architect.published_content as a draft note for the curator."""
    vec = _embed(text)
    # Try to find the most relevant theme by embedding similarity
    theme_id = _find_closest_theme(vec)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO decision_architect.published_content
                       (title, body, platform, content_type, status, theme_id, embedding)
                   VALUES (%s, %s, 'file_ingest', 'note', 'draft', %s, %s::vector)
                   RETURNING id""",
                (filename, text, theme_id, _vec_str(vec)),
            )
            row = cur.fetchone()
        conn.commit()
    return row["id"]


def _find_closest_theme(vec: list[float]) -> int | None:
    vec_str = _vec_str(vec)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id FROM decision_architect.theme
                   WHERE embedding IS NOT NULL
                   ORDER BY embedding <=> %s::vector LIMIT 1""",
                (vec_str,),
            )
            row = cur.fetchone()
    return row["id"] if row else None
