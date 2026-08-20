"""
One-off backfill (Interrogation Layer 1.2): set epistemic_{key}='known' for every
existing fact_{key} property in the AGE graph that doesn't already have one — run
once, not a standing task. No retrospective inference classification (spec): every
pre-existing fact is simply marked 'known', matching what set_node_facts()'s new
default parameter would have written had it existed at extraction time.

Confirmed live before writing: only 66 nodes in personal_graph have any fact_*
property today (all :Asset), found via:
  MATCH (n) WHERE size([k IN keys(n) WHERE k STARTS WITH 'fact_']) > 0
  RETURN label(n), count(*)
so this is scoped to :Asset, not a blanket unlabeled-node scan (the codebase's own
convention: unlabeled MATCH has no index support and scans the whole graph).
"""
import re

from src.graph import _conn, _parse_vertex, _build_set, _cypher_val

GRAPH = "personal_graph"


def _strip_agtype_suffix(s: str) -> str:
    return re.sub(r"::(vertex|edge|path|agtype)$", "", s.strip())


def backfill() -> dict:
    conn = _conn()
    updated_nodes = 0
    updated_facts = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM cypher('{GRAPH}', $$ "
                f"MATCH (n:Asset) WHERE size([k IN keys(n) WHERE k STARTS WITH 'fact_']) > 0 "
                f"RETURN n "
                f"$$) AS (n agtype)"
            )
            rows = cur.fetchall()

        for row in rows:
            vertex = _parse_vertex(row["n"])
            if not vertex:
                continue
            props = vertex.get("properties", {})
            ref = props.get("ref")
            if not ref:
                continue

            sets = {}
            for k in props:
                if not k.startswith("fact_"):
                    continue
                bare = k[len("fact_"):]
                if f"epistemic_{bare}" not in props:
                    sets[f"epistemic_{bare}"] = "known"
            if not sets:
                continue

            set_clause = _build_set("n", sets)
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM cypher('{GRAPH}', $$ "
                    f"MATCH (n:Asset {{ref: {_cypher_val('ref', ref)}}}) "
                    f"SET {set_clause} "
                    f"RETURN n "
                    f"$$) AS (n agtype)"
                )
            updated_nodes += 1
            updated_facts += len(sets)

        conn.commit()
    finally:
        conn.close()

    return {"updated_nodes": updated_nodes, "updated_facts": updated_facts}


if __name__ == "__main__":
    result = backfill()
    print(f"[backfill_epistemic_known] {result}")
