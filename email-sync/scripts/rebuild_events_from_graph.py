"""
Rebuild personal.event for a date range from :Event nodes in personal_graph.

Dedup strategy (in order):
  1. Exact: same lower(title) + AEST date → merge notes, keep earliest starts_at
  2. Time overlap: events on same AEST date whose time windows overlap → merge group
  3. Fuzzy title: events on same AEST date with title similarity >= FUZZY_THRESHOLD → merge group

Within each merge group: collect all titles + notes, LLM synthesises the best
single title + description.  Falls back to longest title if LLM unavailable.

appointment_updater then pushes to GCal.
"""
import os, sys, re
sys.path.insert(0, "/app")

import psycopg2, psycopg2.extras, requests
from datetime import datetime, timezone, timedelta
from dateutil.parser import parse as dtparse

DB_URL       = os.environ["DATABASE_URL"]
INGESTOR_URL = os.environ.get("INGESTOR_URL", "http://ingestor:4001")
OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://172.23.96.1:11434")
AGENT_MODEL  = os.environ.get("AGENT_MODEL", "qwen2.5:14b")

# Date range — override via env or edit here
START = os.environ.get("REBUILD_START", "2026-06-30")
END   = os.environ.get("REBUILD_END",   "2026-08-01")

# Minimum word-overlap ratio to consider two titles fuzzy-duplicates
FUZZY_THRESHOLD = 0.5

# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _title_similarity(a: str, b: str) -> float:
    """Jaccard word overlap between two lowercased titles."""
    wa = set(re.findall(r"\w+", a.lower()))
    wb = set(re.findall(r"\w+", b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _times_overlap(ev_a: dict, ev_b: dict, slack_mins: int = 30) -> bool:
    """True if two events overlap in time (or start within slack_mins of each other)."""
    a_start = ev_a["starts_at"]
    b_start = ev_b["starts_at"]
    a_end   = ev_a.get("ends_at") or (a_start + timedelta(hours=1))
    b_end   = ev_b.get("ends_at") or (b_start + timedelta(hours=1))
    slack   = timedelta(minutes=slack_mins)
    return a_start < (b_end + slack) and b_start < (a_end + slack)


def _llm_merge(candidates: list[dict]) -> tuple[str, str]:
    """
    Ask LLM to synthesise the best title + description from a group of duplicate events.
    Returns (title, notes). Falls back to longest title + concatenated notes on failure.
    """
    if len(candidates) == 1:
        return candidates[0]["title"], _strip_html(candidates[0].get("notes") or "")

    blocks = []
    for i, c in enumerate(candidates, 1):
        notes_clean = _strip_html(c.get("notes") or "")
        blocks.append(f"Version {i}:\n  Title: {c['title']}\n  Notes: {notes_clean[:400]}")

    prompt = (
        "These are duplicate calendar events for the same appointment, "
        "created from different sources (email invite, calendar placeholder, Outlook mirror, etc.).\n\n"
        + "\n\n".join(blocks)
        + "\n\nTask: Produce ONE merged event that captures the most useful information from all versions.\n"
        "Rules:\n"
        "- Title: specific and informative (person names, topic, organisation if known). Max 70 chars.\n"
        "- Description: 1-4 lines combining the best details across all versions "
        "(location, link, reference, who/what/where). Omit boilerplate and HTML.\n"
        "- Only include information actually present in the versions above.\n\n"
        "Reply in this EXACT format:\n"
        "TITLE: <merged title>\n"
        "DESCRIPTION: <merged description or blank>"
    )
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": AGENT_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.1, "num_predict": 200}},
            timeout=45,
        )
        text = resp.json().get("response", "").strip()
        title, desc = candidates[0]["title"], ""
        for line in text.splitlines():
            if line.upper().startswith("TITLE:"):
                t = line[6:].strip()
                if t:
                    title = t[:70]
            elif line.upper().startswith("DESCRIPTION:"):
                desc = line[12:].strip()
        return title, desc
    except Exception as e:
        print(f"  [merge] LLM failed: {e} — using longest title")
        best = max(candidates, key=lambda c: len(c["title"]))
        all_notes = " | ".join(
            _strip_html(c.get("notes") or "")
            for c in candidates if c.get("notes")
        )
        return best["title"], all_notes[:500]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import pytz
    from datetime import date as date_type
    from src.db import upsert_event

    _brisbane = pytz.timezone("Australia/Brisbane")

    # Parse date window
    try:
        start_dt = datetime.fromisoformat(START).replace(tzinfo=timezone.utc)
        end_dt   = datetime.fromisoformat(END).replace(tzinfo=timezone.utc)
        # Target range for output (one day inside the pull window)
        target_start = (start_dt + timedelta(days=1)).date()
        target_end   = (end_dt   - timedelta(days=1)).date()
    except Exception as e:
        print(f"Bad date range: {e}")
        return

    # Pull graph — fetch one day wider each side to catch UTC-shifted events
    pull_start = (start_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    pull_end   = (end_dt   + timedelta(days=1)).strftime("%Y-%m-%d")

    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    with conn.cursor() as cur:
        cur.execute("LOAD 'age'; SET search_path = ag_catalog, \"$user\", public;")
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT * FROM cypher('personal_graph', $$
                MATCH (e:Event)
                WHERE e.starts_at >= '{pull_start}' AND e.starts_at < '{pull_end}'
                RETURN e.title, e.starts_at, e.ends_at, e.event_type,
                       e.notes, e.calendar_source, e.calendar_event_id
            $$) AS (title agtype, starts_at agtype, ends_at agtype, event_type agtype,
                    notes agtype, calendar_source agtype, calendar_event_id agtype)
        """)
        rows = cur.fetchall()
    conn.close()
    print(f"Graph: {len(rows)} raw Event nodes")

    # ── Step 1: Parse all rows ────────────────────────────────────────────────
    parsed = []
    for r in rows:
        title = (r["title"] or '""').strip('"')
        if not title:
            continue
        try:
            starts_at = dtparse(r["starts_at"].strip('"'))
            if starts_at.tzinfo is None:
                starts_at = starts_at.replace(tzinfo=timezone.utc)
            starts_at = starts_at.astimezone(timezone.utc)
            aest_date = starts_at.astimezone(_brisbane).date()
        except Exception:
            continue

        ends_at = None
        raw_ends = (r["ends_at"] or '""').strip('"')
        if raw_ends:
            try:
                ends_at = dtparse(raw_ends)
                if ends_at.tzinfo is None:
                    ends_at = ends_at.replace(tzinfo=timezone.utc)
                ends_at = ends_at.astimezone(timezone.utc)
            except Exception:
                pass

        parsed.append({
            "title":      title,
            "starts_at":  starts_at,
            "ends_at":    ends_at,
            "event_type": (r["event_type"] or '"calendar_event"').strip('"'),
            "notes":      (r["notes"] or '""').strip('"'),
            "aest_date":  aest_date,
        })

    # ── Step 2: Group by AEST date, then fuzzy-merge within each day ──────────
    from collections import defaultdict
    by_date: dict[date_type, list[dict]] = defaultdict(list)
    for ev in parsed:
        by_date[ev["aest_date"]].append(ev)

    merged_events: list[dict] = []

    for aest_date, day_events in sorted(by_date.items()):
        # Union-Find to group duplicates
        n = len(day_events)
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y):
            parent[find(x)] = find(y)

        for i in range(n):
            for j in range(i + 1, n):
                a, b = day_events[i], day_events[j]
                # Exact title match → always merge
                if a["title"].lower() == b["title"].lower():
                    union(i, j)
                    continue
                a_is_timed = a["starts_at"].hour != 0 or a["starts_at"].minute != 0
                b_is_timed = b["starts_at"].hour != 0 or b["starts_at"].minute != 0
                if a_is_timed and b_is_timed:
                    # Exact same start time → always merge (placeholder + invite pattern)
                    if a["starts_at"] == b["starts_at"]:
                        union(i, j)
                        continue
                    # Overlapping time windows → merge if any word in common
                    if _times_overlap(a, b):
                        sim = _title_similarity(a["title"], b["title"])
                        if sim >= FUZZY_THRESHOLD:
                            union(i, j)
                            continue
                # All-day / same-date → only merge on high title similarity
                sim = _title_similarity(a["title"], b["title"])
                if sim >= 0.75:
                    union(i, j)

        # Collect groups
        groups: dict[int, list[dict]] = defaultdict(list)
        for i, ev in enumerate(day_events):
            groups[find(i)].append(ev)

        for group in groups.values():
            if len(group) == 1:
                merged_events.append(group[0])
                continue

            titles = [g["title"] for g in group]
            print(f"  [merge] {aest_date}: {len(group)} duplicates → {titles}")

            # Use earliest starts_at, latest ends_at
            best_start = min(g["starts_at"] for g in group)
            ends = [g["ends_at"] for g in group if g["ends_at"]]
            best_end = max(ends) if ends else None

            merged_title, merged_notes = _llm_merge(group)
            print(f"    → '{merged_title}'")

            merged_events.append({
                "title":      merged_title,
                "starts_at":  best_start,
                "ends_at":    best_end,
                "event_type": group[0]["event_type"],
                "notes":      merged_notes,
                "aest_date":  aest_date,
            })

    print(f"After fuzzy merge: {len(merged_events)} unique events")

    # ── Step 3: Write to personal.event (target date range only) ─────────────
    written = 0
    for ev in sorted(merged_events, key=lambda e: e["aest_date"]):
        if ev["aest_date"] < target_start or ev["aest_date"] > target_end:
            continue
        try:
            cal_event_id = f"graph:rebuild:{ev['title'].lower()[:40]}:{ev['aest_date']}"
            event_id = upsert_event(
                title=ev["title"],
                starts_at=ev["starts_at"],
                ends_at=ev["ends_at"],
                event_type=ev["event_type"] or "calendar_event",
                calendar_source="graph",
                calendar_event_id=cal_event_id,
                notes=ev["notes"] or "",
                ingestor_url=INGESTOR_URL,
            )
            print(f"  [{ev['aest_date']}] {ev['title']} → event {event_id}")
            written += 1
        except Exception as e:
            print(f"  ERROR [{ev['aest_date']}] {ev['title']}: {e}")

    print(f"\nDone. {written} events written to personal.event.")


if __name__ == "__main__":
    main()
