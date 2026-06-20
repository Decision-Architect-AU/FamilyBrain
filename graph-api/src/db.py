"""Database connection and AGE query helpers."""
import json
import os
import re
from contextlib import contextmanager
from typing import Any

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")
GRAPH_NAME   = os.environ.get("AGE_GRAPH_NAME", "personal_graph")


@contextmanager
def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("LOAD 'age'")
            cur.execute("SET search_path = ag_catalog, \"$user\", public")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _strip_agtype(s: str) -> str:
    """Remove trailing ::vertex / ::edge / ::path agtype suffix."""
    return re.sub(r"::(vertex|edge|path|agtype)$", "", s.strip())


def _parse_agtype(raw) -> dict | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(_strip_agtype(str(raw)))
    except Exception:
        return None


def _node_to_dict(v: dict) -> dict:
    """Convert AGE vertex dict → spec node shape."""
    props = v.get("properties", {})
    return {
        "id":         str(v["id"]),
        "labels":     [v.get("label", "Unknown")],
        "properties": props,
    }


def _edge_to_dict(e: dict) -> dict:
    """Convert AGE edge dict → spec edge shape."""
    return {
        "id":        str(e["id"]),
        "type":      e.get("label", ""),
        "startNode": str(e.get("start_id", "")),
        "endNode":   str(e.get("end_id", "")),
        "properties": e.get("properties", {}),
    }


def _count_return_cols(cypher: str) -> int:
    """Count how many values the RETURN clause emits."""
    m = re.search(r'\bRETURN\b(.+?)(?:\bLIMIT\b|\bORDER\b|\bSKIP\b|\bUNION\b|$)',
                  cypher, re.IGNORECASE | re.DOTALL)
    if not m:
        return 1
    clause = m.group(1).strip()
    depth, count = 0, 1
    for ch in clause:
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif ch == ',' and depth == 0:
            count += 1
    return count


def cypher_query(cypher: str) -> dict:
    """Run a read-only Cypher query. Returns {nodes, edges}."""
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}

    n_cols = _count_return_cols(cypher)
    col_defs = ", ".join(f"c{i} agtype" for i in range(n_cols))

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM cypher(%s, $$ {cypher} $$) AS ({col_defs})",
                (GRAPH_NAME,),
            )
            rows = cur.fetchall()

    for row in rows:
        for cell in row:
            parsed = _parse_agtype(cell)
            if not isinstance(parsed, dict):
                continue
            if "start_id" in parsed:
                e = _edge_to_dict(parsed)
                edges[e["id"]] = e
            elif "label" in parsed and "id" in parsed:
                n = _node_to_dict(parsed)
                nodes[n["id"]] = n

    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def get_all_labels() -> list[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM ag_catalog.ag_label "
                "WHERE graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = %s) "
                "AND kind = 'v' ORDER BY name",
                (GRAPH_NAME,),
            )
            return [r[0] for r in cur.fetchall()]


def get_all_rel_types() -> list[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM ag_catalog.ag_label "
                "WHERE graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = %s) "
                "AND kind = 'e' ORDER BY name",
                (GRAPH_NAME,),
            )
            return [r[0] for r in cur.fetchall()]


def get_node_by_id(node_id: str) -> dict | None:
    result = cypher_query(f"MATCH (n) WHERE id(n) = {node_id} RETURN n")
    return result["nodes"][0] if result["nodes"] else None


def get_edge_by_id(edge_id: str) -> dict | None:
    result = cypher_query(f"MATCH ()-[r]->() WHERE id(r) = {edge_id} RETURN r")
    return result["edges"][0] if result["edges"] else None


def create_node(labels: list[str], properties: dict) -> dict:
    label = labels[0] if labels else "Node"
    props_json = json.dumps(properties)
    result = cypher_query(
        f"CREATE (n:{label} {props_json}) RETURN n"
    )
    return result["nodes"][0]


def patch_node(node_id: str, properties: dict) -> dict | None:
    set_parts = ", ".join(f"n.{k} = {json.dumps(v)}" for k, v in properties.items())
    if not set_parts:
        return get_node_by_id(node_id)
    result = cypher_query(
        f"MATCH (n) WHERE id(n) = {node_id} SET {set_parts} RETURN n"
    )
    return result["nodes"][0] if result["nodes"] else None


def delete_node(node_id: str, force: bool = False) -> bool:
    if force:
        cypher_query(f"MATCH (n) WHERE id(n) = {node_id} DETACH DELETE n")
    else:
        cypher_query(f"MATCH (n) WHERE id(n) = {node_id} DELETE n")
    return True


def create_edge(from_id: str, to_id: str, rel_type: str, properties: dict) -> dict:
    props_json = json.dumps(properties) if properties else "{}"
    result = cypher_query(
        f"MATCH (a), (b) WHERE id(a) = {from_id} AND id(b) = {to_id} "
        f"CREATE (a)-[r:{rel_type} {props_json}]->(b) RETURN r"
    )
    return result["edges"][0]


def patch_edge(edge_id: str, properties: dict) -> dict | None:
    set_parts = ", ".join(f"r.{k} = {json.dumps(v)}" for k, v in properties.items())
    if not set_parts:
        return get_edge_by_id(edge_id)
    result = cypher_query(
        f"MATCH ()-[r]->() WHERE id(r) = {edge_id} SET {set_parts} RETURN r"
    )
    return result["edges"][0] if result["edges"] else None


def delete_edge(edge_id: str) -> bool:
    cypher_query(f"MATCH ()-[r]->() WHERE id(r) = {edge_id} DELETE r")
    return True


def get_schema_for_label(label: str) -> dict:
    """Sample property keys from existing nodes of this label."""
    result = cypher_query(f"MATCH (n:{label}) RETURN n LIMIT 50")
    keys: set[str] = set()
    for node in result["nodes"]:
        keys.update(node.get("properties", {}).keys())
    return {"label": label, "propertyKeys": sorted(keys)}
