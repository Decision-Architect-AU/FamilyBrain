"""
File ingestion watcher + HTTP API for webhook ingestion.

Watches /data/ReadyToIngest for new files.
Routes by subfolder (personal/ property/ decision/) or auto-classifies root drops.
Moves files: ReadyToIngest → Processing → Ingested/<schema>

Also serves HTTP on port 4001:
  POST /ingest/observation  — receives approved CommentOS items
  POST /ingest/email        — receives email payloads from email-sync service
  POST /ingest/event        — receives calendar event payloads (any calendar source)
  POST /ingest/message      — generic inbound content (WhatsApp, SMS, voice, etc.)
"""
import os
import shutil
import time
import pathlib
import threading
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

from src.extract import extract_text
from src.classify import classify
from src.ingest import ingest_personal, ingest_property, ingest_decision, _find_closest_theme
from src.extract_concepts import extract_quick, extract_deep, extract_deeper, extract_concepts
from src.llm import embed
from src import audit
from src import graph as graph_writer
from src.categorise import categorise_email, save_category, backfill_categories
from src.config_index import record_ingest

WATCH_DIR      = pathlib.Path(os.environ.get("INGEST_WATCH_DIR", "/data/ReadyToIngest"))
PROCESSING_DIR = pathlib.Path(os.environ.get("INGEST_PROCESSING_DIR", "/data/Processing"))
INGESTED_DIR   = pathlib.Path(os.environ.get("INGEST_DONE_DIR", "/data/Ingested"))

SUPPORTED = {".pdf", ".docx", ".doc", ".txt", ".md", ".text", ".csv",
             ".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp"}

ENABLE_DEEP_PASS   = os.environ.get("EXTRACT_DEEP_PASS",   "true").lower() == "true"
ENABLE_DEEPER_PASS = os.environ.get("EXTRACT_DEEPER_PASS", "false").lower() == "true"


def _normalize_chunk(chunk_result: dict) -> dict:
    """Convert raw LLM extraction chunk to the rich format graph.py expects."""
    return {
        "concepts": {
            i["name"]: {"description": i.get("description", ""), "framework": i.get("framework")}
            for i in chunk_result.get("concepts", []) if i.get("name")
        },
        "people": {
            i["name"]: {"description": i.get("description", ""), "is_author": i.get("is_author", False)}
            for i in chunk_result.get("people", []) if i.get("name")
        },
        "organisations": {
            i["name"]: i.get("description", "")
            for i in chunk_result.get("organisations", []) if i.get("name")
        },
        "claims": chunk_result.get("claims", []),
        "frameworks": {
            i["name"]: {"description": i.get("description", ""), "domain": i.get("domain", "")}
            for i in chunk_result.get("frameworks", []) if i.get("name")
        },
        "relationships": chunk_result.get("relationships", []),
    }


def process_file(src: pathlib.Path) -> None:
    # Determine schema from subfolder or classify
    relative = src.relative_to(WATCH_DIR)
    parts = relative.parts
    explicit_schema = parts[0].lower() if len(parts) > 1 else None

    # Move to Processing
    proc_path = PROCESSING_DIR / src.name
    shutil.move(str(src), str(proc_path))
    audit.log("write", f"Processing started: {src.name}", target_schema=explicit_schema)

    try:
        text = extract_text(proc_path)
        if not text.strip():
            raise ValueError("No text extracted from file")

        schema = explicit_schema if explicit_schema in ("personal", "property", "decision") else None
        if schema is None:
            print(f"[ingestor] Auto-classifying {src.name}...")
            schema = classify(text)
            print(f"[ingestor] Classified as: {schema}")

        if schema == "personal":
            node_id = ingest_personal(text, src.name)
            target_table = "personal.note"
        elif schema == "property":
            node_id = ingest_property(text, src.name)
            target_table = "property_deals.scraped_listing"
        else:
            node_id = ingest_decision(text, src.name)
            target_table = "decision_architect.published_content"

        # Move to Ingested/<schema>/
        done_dir = INGESTED_DIR / schema
        done_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(proc_path), str(done_dir / src.name))

        # Write AGE Document node
        graph_writer.write_document_node(schema, src.name, node_id, text[:300])

        # Extract concepts and write graph nodes incrementally per chunk
        vec = embed(text[:2000])
        theme_id = _find_closest_theme(vec)
        print(f"[ingestor] Extracting concepts from {src.name}...")

        # ── Pass 1: quick extraction (3b) — runs inline, nodes written as chunks complete ──
        def on_chunk(chunk_result):
            graph_writer.write_extracted_nodes(schema, src.name, node_id, _normalize_chunk(chunk_result), theme_id, embed)

        print(f"[ingestor] Pass 1 (quick) for {src.name}...")
        extract_quick(text, on_chunk=on_chunk)
        graph_writer.stamp_parse(schema, src.name, extract_concepts.QUICK_MODEL)

        # ── Pass 2: deep extraction (14b) — runs in background, enriches existing nodes ──
        if ENABLE_DEEP_PASS:
            def _deep_pass(t=text, s=schema, n=src.name, nid=node_id, tid=theme_id):
                print(f"[ingestor] Pass 2 (deep) starting for {n}...")
                def on_chunk_deep(chunk_result):
                    graph_writer.write_extracted_nodes(s, n, nid, _normalize_chunk(chunk_result), tid, embed)
                extract_deep(t, on_chunk=on_chunk_deep)
                graph_writer.stamp_parse(s, n, extract_concepts.DEEP_MODEL)
                print(f"[ingestor] Pass 2 (deep) complete for {n}")
                if ENABLE_DEEPER_PASS:
                    def on_chunk_deeper(chunk_result):
                        graph_writer.write_extracted_nodes(s, n, nid, _normalize_chunk(chunk_result), tid, embed)
                    print(f"[ingestor] Pass 3 (deeper) starting for {n}...")
                    extract_deeper(t, on_chunk=on_chunk_deeper)
                    graph_writer.stamp_parse(s, n, extract_concepts.DEEPER_MODEL)
                    print(f"[ingestor] Pass 3 (deeper) complete for {n}")
            threading.Thread(target=_deep_pass, daemon=True).start()

        # Determine source type: PDFs/DOCXs from personal folder are financial_doc by convention
        src_type = "financial_doc" if (schema == "personal" and src.suffix.lower() in (".pdf", ".docx", ".doc")) else "file"
        record_ingest(schema, src_type)

        audit.log(
            "write",
            f"Ingested [{schema}]: {src.name} → {target_table} id={node_id}",
            target_schema=schema,
            target_table=target_table,
            node_id=str(node_id),
            metadata={"filename": src.name, "chars": len(text)},
        )
        print(f"[ingestor] ✓ {src.name} → {schema} (id={node_id})")

    except Exception as e:
        err_dir = INGESTED_DIR / "unknown"
        err_dir.mkdir(parents=True, exist_ok=True)
        if proc_path.exists():
            shutil.move(str(proc_path), str(err_dir / src.name))
        audit.log("write", f"Ingest failed: {src.name} — {e}", metadata={"error": str(e)})
        print(f"[ingestor] ✗ {src.name} failed: {e}")


def ingest_email(payload: dict) -> dict:
    """
    Ingest an email from the email-sync service into the personal graph.

    Expected payload:
    {
        "account_id":      int,          # personal.email_account.id
        "provider_msg_id": str,          # Gmail message ID or Outlook item id
        "thread_id":       str | null,
        "from_address":    str,
        "from_name":       str | null,
        "to_addresses":    [str, ...],
        "subject":         str,
        "received_at":     str,          # ISO8601
        "body_text":       str,          # plain-text body (HTML stripped upstream)
        "attachments":     [str, ...],   # filenames only — actual files handled separately
    }

    Returns: {"ok": bool, "note_id": int, "schema": str} or {"ok": false, "error": str}
    """
    import psycopg2
    import psycopg2.extras
    from src.ingest import ingest_personal, ingest_property, ingest_decision, _find_closest_theme, _embed, _vec_str, DB_URL

    required = ("account_id", "provider_msg_id", "body_text")
    for field in required:
        if field not in payload:
            return {"ok": False, "error": f"Missing required field: {field}"}

    account_id      = payload["account_id"]
    provider_msg_id = payload["provider_msg_id"]
    subject         = payload.get("subject", "(no subject)")
    from_address    = payload.get("from_address", "")
    from_name       = payload.get("from_name", "")
    received_at     = payload.get("received_at")
    body_text       = payload["body_text"].strip()

    if not body_text:
        return {"ok": False, "error": "Empty email body — skipping"}

    # Build full text for classify + embed
    full_text = f"From: {from_name} <{from_address}>\nSubject: {subject}\n\n{body_text}"

    try:
        # Classify schema
        schema = classify(full_text)

        # Ingest into schema
        tags = [f"email", f"from:{from_address}", subject[:80]]
        note_id = None  # only set for personal schema (FK to personal.note)
        if schema == "personal":
            note_id = ingest_personal(full_text, f"email:{provider_msg_id}", tags)
            target_table = "personal.note"
            row_id = note_id
        elif schema == "property":
            row_id = ingest_property(full_text, f"email:{provider_msg_id}")
            target_table = "property_deals.scraped_listing"
        else:
            row_id = ingest_decision(full_text, f"email:{provider_msg_id}")
            target_table = "decision_architect.published_content"

        # Write AGE document node + generic Message node
        graph_writer.write_document_node(schema, f"email:{provider_msg_id}", row_id, full_text[:300])
        graph_writer.write_message_node(
            source="email",
            source_id=provider_msg_id,
            doc_row_id=row_id,
            schema=schema,
            from_handle=from_address,
            from_name=from_name,
            subject=subject,
            received_at=received_at or "",
            body_preview=body_text[:300],
        )

        # Extract concepts in background thread
        vec = embed(full_text[:2000])
        theme_id = _find_closest_theme(vec)

        def on_chunk(chunk_result):
            graph_writer.write_extracted_nodes(schema, f"email:{provider_msg_id}", row_id, _normalize_chunk(chunk_result), theme_id, embed)

        threading.Thread(target=extract_concepts, args=(full_text,), kwargs={"on_chunk": on_chunk}, daemon=True).start()

        # Categorise the email (fast: phi3.5-mini; falls back to qwen2.5:14b)
        try:
            cat, cat_conf = categorise_email(from_address, subject, body_text)
        except Exception as cat_err:
            print(f"[ingestor] categorise failed for {provider_msg_id}: {cat_err}")
            cat, cat_conf = "personal", 0.0

        # Record in email_message table (dedup + status + category)
        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO personal.email_message
                        (account_id, provider_msg_id, thread_id,
                         from_address, from_name, to_addresses, subject, received_at,
                         schema_routed, note_id, ingest_status, ingest_at,
                         category, category_confidence, categorised_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ingested', now(),
                            %s, %s, now())
                    ON CONFLICT (account_id, provider_msg_id) DO UPDATE
                        SET ingest_status        = 'ingested',
                            ingest_at            = now(),
                            note_id              = EXCLUDED.note_id,
                            category             = EXCLUDED.category,
                            category_confidence  = EXCLUDED.category_confidence,
                            categorised_at       = now()
                    RETURNING id
                    """,
                    (
                        account_id, provider_msg_id, payload.get("thread_id"),
                        from_address, from_name, payload.get("to_addresses", []),
                        subject, received_at,
                        schema, note_id,
                        cat, round(cat_conf, 3),
                    ),
                )
            conn.commit()

        record_ingest(schema, "email")

        audit.log("write", f"Email ingested [{schema}/{cat}]: {subject} from {from_address}",
                  target_schema=schema, target_table=target_table, node_id=str(row_id),
                  metadata={"provider_msg_id": provider_msg_id, "account_id": account_id,
                            "category": cat, "category_confidence": round(cat_conf, 3)})
        print(f"[ingestor] ✓ email:{provider_msg_id} → {schema}/{cat} ({cat_conf:.2f}) (row_id={row_id})")
        return {"ok": True, "note_id": note_id, "schema": schema, "category": cat}

    except Exception as e:
        print(f"[ingestor] ✗ email:{provider_msg_id} failed: {e}")
        # Mark as error in email_message if possible
        try:
            with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO personal.email_message
                            (account_id, provider_msg_id, subject, from_address, ingest_status, ingest_error, ingest_at)
                        VALUES (%s, %s, %s, %s, 'error', %s, now())
                        ON CONFLICT (account_id, provider_msg_id) DO UPDATE
                            SET ingest_status = 'error', ingest_error = EXCLUDED.ingest_error
                        """,
                        (account_id, provider_msg_id, subject, from_address, str(e)),
                    )
                conn.commit()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}


def ingest_event(payload: dict) -> dict:
    """
    Write a calendar event into personal_graph as a generic (:Event) node.
    Called by email-sync after upserting into personal.event.
    Also works for events extracted from WhatsApp ("physio Thursday") or voice notes.

    Payload:
    {
        "event_row_id":      int,          # personal.event.id
        "title":             str,
        "starts_at":         str,          # ISO8601
        "ends_at":           str | null,
        "event_type":        str,          # school | medical | ndis | household | family
        "calendar_source":   str,          # "gmail:x@gmail.com" | "outlook:x@hotmail.com" | "whatsapp" | "voice"
        "calendar_event_id": str,          # provider event ID or generated key
        "notes":             str | null,
    }
    """
    required = ("event_row_id", "title", "starts_at")
    for f in required:
        if f not in payload:
            return {"ok": False, "error": f"Missing required field: {f}"}
    try:
        graph_writer.write_event_node(
            event_row_id=payload["event_row_id"],
            title=payload["title"],
            starts_at=str(payload["starts_at"]),
            ends_at=str(payload.get("ends_at") or ""),
            event_type=payload.get("event_type", "family"),
            calendar_source=payload.get("calendar_source", ""),
            calendar_event_id=payload.get("calendar_event_id", ""),
            notes=payload.get("notes", ""),
        )
        return {"ok": True, "event_row_id": payload["event_row_id"]}
    except Exception as e:
        print(f"[ingestor] ingest_event failed: {e}")
        return {"ok": False, "error": str(e)}


def ingest_message(payload: dict) -> dict:
    """
    Generic inbound content ingestion — WhatsApp, SMS, voice transcript, web clip, etc.
    Classifies, ingests to personal.note, writes (:Message) node in personal_graph.

    Payload:
    {
        "source":       str,   # "whatsapp" | "sms" | "voice" | "web" | ...
        "source_id":    str,   # provider message ID
        "from_handle":  str,   # phone number, WA number, URL, ...
        "from_name":    str | null,
        "subject":      str | null,   # first line of voice note, WA chat name, ...
        "body_text":    str,
        "received_at":  str | null,   # ISO8601
    }
    """
    import psycopg2
    import psycopg2.extras
    from src.ingest import ingest_personal, ingest_property, ingest_decision, _find_closest_theme, DB_URL

    required = ("source", "source_id", "body_text")
    for f in required:
        if f not in payload:
            return {"ok": False, "error": f"Missing required field: {f}"}

    source      = payload["source"]
    source_id   = payload["source_id"]
    from_handle = payload.get("from_handle", "")
    from_name   = payload.get("from_name", "")
    subject     = payload.get("subject", "")
    received_at = payload.get("received_at", "")
    body_text   = payload["body_text"].strip()

    if not body_text:
        return {"ok": False, "error": "Empty body — skipping"}

    label_line = f"From: {from_name or from_handle} via {source}"
    if subject:
        label_line += f"\n{subject}"
    full_text = f"{label_line}\n\n{body_text}"

    try:
        schema = classify(full_text)
        tags   = [f"source:{source}", from_handle or "unknown", subject[:80]] if subject else [f"source:{source}", from_handle or "unknown"]

        if schema == "personal":
            note_id = ingest_personal(full_text, f"{source}:{source_id}", tags)
            target_table = "personal.note"
        elif schema == "property":
            note_id = ingest_property(full_text, f"{source}:{source_id}")
            target_table = "property_deals.scraped_listing"
        else:
            note_id = ingest_decision(full_text, f"{source}:{source_id}")
            target_table = "decision_architect.published_content"

        graph_writer.write_document_node(schema, f"{source}:{source_id}", note_id, full_text[:300])
        graph_writer.write_message_node(
            source=source,
            source_id=source_id,
            doc_row_id=note_id,
            schema=schema,
            from_handle=from_handle,
            from_name=from_name,
            subject=subject,
            received_at=received_at,
            body_preview=body_text[:300],
        )

        vec = embed(full_text[:2000])
        theme_id = _find_closest_theme(vec)

        def on_chunk(chunk_result):
            graph_writer.write_extracted_nodes(schema, f"{source}:{source_id}", note_id, _normalize_chunk(chunk_result), theme_id, embed)

        threading.Thread(target=extract_concepts, args=(full_text,), kwargs={"on_chunk": on_chunk}, daemon=True).start()

        record_ingest(schema, source)  # source = 'whatsapp' | 'voice' | 'sms' | etc.

        audit.log("write", f"Message ingested [{source}→{schema}]: {subject or source_id} from {from_handle}",
                  target_schema=schema, target_table=target_table, node_id=str(note_id),
                  metadata={"source": source, "source_id": source_id})
        print(f"[ingestor] ✓ {source}:{source_id} → {schema} (note_id={note_id})")
        return {"ok": True, "note_id": note_id, "schema": schema}

    except Exception as e:
        print(f"[ingestor] ✗ {source}:{source_id} failed: {e}")
        return {"ok": False, "error": str(e)}


def ingest_observation(payload: dict) -> dict:
    """Ingest a CommentOS approved item into decision_graph as an Observation."""
    original_post = payload.get("original_post", "").strip()
    approved_comment = payload.get("approved_comment", "").strip()
    source_url = payload.get("source_url", "")
    platform = payload.get("platform", "linkedin")

    if not original_post:
        return {"ok": False, "error": "original_post is required"}

    try:
        # Save original post as decision content (raw_scrape status)
        import psycopg2
        import psycopg2.extras
        from src.ingest import _find_closest_theme, _embed, _vec_str, DB_URL

        vec = _embed(original_post[:2000])
        theme_id = _find_closest_theme(vec)

        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
            with conn.cursor() as cur:
                # Save original post
                cur.execute(
                    """INSERT INTO decision_architect.published_content
                           (title, body, platform, content_type, status, theme_id, embedding)
                       VALUES (%s, %s, %s, 'observation', 'raw_scrape', %s, %s::vector)
                       RETURNING id""",
                    (f"{platform} post", original_post, platform, theme_id, _vec_str(vec)),
                )
                post_id = cur.fetchone()["id"]

                # Save approved comment as draft
                if approved_comment:
                    cur.execute(
                        """INSERT INTO decision_architect.published_content
                               (title, body, platform, content_type, status, theme_id, embedding)
                           VALUES (%s, %s, %s, 'comment', 'draft', %s, %s::vector)
                           RETURNING id""",
                        (f"Comment on {platform} post", approved_comment, platform, theme_id, _vec_str(vec)),
                    )
            conn.commit()

        # Write Observation node to decision_graph
        graph_writer.write_document_node("decision", f"{platform}:{source_url}", post_id, original_post[:300])

        # Extract concepts from original post
        def on_chunk(chunk_result):
            graph_writer.write_extracted_nodes("decision", f"{platform}:{source_url}", post_id, _normalize_chunk(chunk_result), theme_id, embed)

        threading.Thread(
            target=extract_concepts,
            args=(original_post,),
            kwargs={"on_chunk": on_chunk},
            daemon=True,
        ).start()

        audit.log("write", f"Observation ingested from {platform}: {source_url}", target_schema="decision")
        print(f"[ingestor] ✓ Observation from {platform} (id={post_id})")
        return {"ok": True, "id": post_id}

    except Exception as e:
        print(f"[ingestor] ✗ Observation ingest failed: {e}")
        return {"ok": False, "error": str(e)}


class WebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
        elif self.path == "/scan":
            threading.Thread(target=scan_existing, daemon=True).start()
            self._respond(200, {"status": "scan triggered"})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        if self.path == "/ingest/observation":
            result = ingest_observation(body)
        elif self.path == "/ingest/email":
            result = ingest_email(body)
        elif self.path == "/ingest/event":
            result = ingest_event(body)
        elif self.path == "/ingest/message":
            result = ingest_message(body)
        elif self.path == "/ingest/reparse":
            # Body: {filename, schema, ollama_url (optional), model (optional)}
            result = reparse_document(body)
        elif self.path == "/ingest/categorise-batch":
            # Backfill categories for previously ingested emails with no category.
            # Optional body: {"limit": 200}
            limit = int(body.get("limit", 200))
            # Run in background so the HTTP response returns immediately
            def _run():
                stats = backfill_categories(limit=limit)
                print(f"[ingestor] categorise-batch done: {stats}")
            threading.Thread(target=_run, daemon=True).start()
            result = {"ok": True, "message": f"Backfill started (limit={limit}). Check ingestor logs."}
        else:
            self._respond(404, {"error": "not found"})
            return
        self._respond(200 if result.get("ok") else 400, result)

    def _respond(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"[webhook] {fmt % args}")


SCAN_INTERVAL = int(os.environ.get("INGEST_SCAN_INTERVAL", "15"))  # seconds


def reparse_document(payload: dict) -> dict:
    """
    Re-run deep extraction on an already-ingested document using a specified model endpoint.
    Called by the external scheduler when the 32B server is up.
    Body: {filename, schema, ollama_url, model_name (optional)}
    """
    filename   = payload.get("filename", "")
    schema     = payload.get("schema", "personal")
    ollama_url = payload.get("ollama_url", "")
    model_name = payload.get("model_name", "qwen2.5:32b")

    if not filename:
        return {"ok": False, "error": "filename required"}

    # Find file in Ingested dir
    file_path = INGESTED_DIR / schema / filename
    if not file_path.exists():
        # Try other schemas
        for s in ("personal", "property", "decision", "unknown"):
            candidate = INGESTED_DIR / s / filename
            if candidate.exists():
                file_path = candidate
                schema = s
                break
    if not file_path.exists():
        return {"ok": False, "error": f"{filename} not found in Ingested"}

    def _run():
        try:
            text = extract_text(str(file_path))
            if not text:
                print(f"[reparse] No text extracted from {filename}")
                return

            # Get existing node_id from DB for graph linking
            from src.ingest import ingest_personal, _find_closest_theme
            vec      = embed(text[:2000])
            theme_id = _find_closest_theme(vec)

            # Re-run extraction with the deep model at the provided URL
            import src.extract_concepts as ec
            orig_deeper = ec.DEEPER_MODEL
            ec.DEEPER_MODEL = model_name

            extracted = extract_deeper(text, ollama_url=ollama_url)

            ec.DEEPER_MODEL = orig_deeper

            if extracted:
                # Write enriched nodes back — reuses same graph write logic
                # node_id=0 means we link by filename match in graph
                from src import graph as gw
                from src.ingest import _find_closest_theme
                # Find row_id from personal.document
                import psycopg2
                conn = psycopg2.connect(os.environ["DATABASE_URL"])
                row_id = 0
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id FROM personal.document WHERE filename=%s LIMIT 1", (filename,))
                        row = cur.fetchone()
                        if row:
                            row_id = row[0]
                finally:
                    conn.close()

                from src.ingest import _normalize_chunk
                gw.write_extracted_nodes(schema, filename, row_id, _normalize_chunk(extracted), theme_id, embed)
                gw.stamp_parse(schema, filename, model_name)
                print(f"[reparse] ✓ {filename} with {model_name}")
            else:
                print(f"[reparse] No extraction result for {filename} — server may be down")
        except Exception as e:
            print(f"[reparse] ✗ {filename}: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": f"Reparse started for {filename} with {model_name}"}


def scan_existing() -> None:
    """Scan ReadyToIngest and process any files present."""
    for f in WATCH_DIR.rglob("*"):
        if f.is_file() and f.suffix.lower() in SUPPORTED:
            print(f"[ingestor] Found file: {f.name}")
            process_file(f)


def _poll_loop() -> None:
    """Background thread: poll WATCH_DIR every SCAN_INTERVAL seconds.

    WSL2's 9P filesystem does not propagate Windows-side file creation events
    reliably to watchdog/inotify, so we scan the directory on a timer instead.
    """
    while True:
        time.sleep(SCAN_INTERVAL)
        try:
            scan_existing()
        except Exception as e:
            print(f"[ingestor] Poll scan error: {e}")


if __name__ == "__main__":
    print(f"[ingestor] Watching {WATCH_DIR} (poll every {SCAN_INTERVAL}s)")
    print(f"[ingestor] Supported types: {', '.join(sorted(SUPPORTED))}")

    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSING_DIR.mkdir(parents=True, exist_ok=True)
    INGESTED_DIR.mkdir(parents=True, exist_ok=True)

    # Startup scan
    scan_existing()

    # Background poll loop
    threading.Thread(target=_poll_loop, daemon=True).start()

    # Webhook server
    server = HTTPServer(("0.0.0.0", 4001), WebhookHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print("[ingestor] Webhook API listening on :4001")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        server.shutdown()
