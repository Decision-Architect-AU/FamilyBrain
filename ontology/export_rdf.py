"""
Export a subset of the live personal schema (Postgres) into RDF/Turtle
conforming to familybrain.owl.ttl — assets, routines, events, and the
SKOS thesaurus of routine synonyms.

This is a companion export, not a replacement for AGE/Cypher — the running
system is untouched. Run inside any container with DATABASE_URL set and
rdflib installed (or point --db-url at the exposed Postgres port from the host).

Usage:
    python export_rdf.py --db-url postgresql://user:pass@localhost:5432/openclaw --out familybrain.ttl
"""
import argparse
import psycopg2
import psycopg2.extras
from rdflib import Graph, Namespace, Literal, RDF, RDFS, URIRef
from rdflib.namespace import XSD, SKOS

FB = Namespace("https://familybrain.local/ontology#")
DATA = Namespace("https://familybrain.local/data/")

# personal.asset.asset_type -> OWL class
ASSET_TYPE_CLASS = {
    "medication":         FB.MedicationAsset,
    "property":           FB.PropertyAsset,
    "vehicle":            FB.VehicleAsset,
    "subscription":       FB.SubscriptionAsset,
    "trust":              FB.EntityAsset,
    "company":            FB.EntityAsset,
    "government_support": FB.GovernmentSupportAsset,
    "routine":            FB.RoutineAsset,
}

# personal.event.event_type -> OWL class (best-effort grouping)
EVENT_TYPE_CLASS = {
    "MEDICAL": FB.MedicalEvent, "THERAPY": FB.MedicalEvent, "THERAPY_SESSION": FB.MedicalEvent,
    "MEDICATION_REFILL": FB.MedicalEvent, "MEDICATION_SCRIPT": FB.MedicalEvent,
    "SCHOOL_DAY": FB.SchoolEvent, "SCHOOL": FB.SchoolEvent, "SCHOOL_ACTIVITY": FB.SchoolEvent,
    "ACTIVITY": FB.ActivityEvent, "CELLO_CLASS": FB.ActivityEvent, "DANCING": FB.ActivityEvent,
    "BILL": FB.FinancialEvent, "RENT_PAYMENT": FB.FinancialEvent,
    "SCHOOL_HOLIDAY": FB.ContextEvent, "PUBLIC_HOLIDAY": FB.ContextEvent,
    "HOLIDAY": FB.ContextEvent, "LEAVE": FB.ContextEvent,
}


def asset_uri(asset_id: int) -> URIRef:
    return DATA[f"asset/{asset_id}"]


def person_uri(person_id: int) -> URIRef:
    return DATA[f"person/{person_id}"]


def event_uri(event_id: int) -> URIRef:
    return DATA[f"event/{event_id}"]


def export(db_url: str, limit_events: int | None) -> Graph:
    g = Graph()
    g.bind("fb", FB)
    g.bind("skos", SKOS)
    g.bind("data", DATA)

    conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)

    with conn.cursor() as cur:
        cur.execute("SELECT id, name, relationship FROM personal.person")
        for p in cur.fetchall():
            uri = person_uri(p["id"])
            g.add((uri, RDF.type, FB.Person))
            g.add((uri, RDFS.label, Literal(p["name"])))
            if p.get("relationship"):
                g.add((uri, FB.relationship, Literal(p["relationship"])))

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, asset_type, subtype, status, person_id,
                   facts->>'dose' AS dose, facts->>'frequency' AS frequency, synonyms
            FROM personal.asset
        """)
        for a in cur.fetchall():
            uri = asset_uri(a["id"])
            cls = ASSET_TYPE_CLASS.get(a["asset_type"], FB.Asset)
            g.add((uri, RDF.type, cls))
            g.add((uri, RDFS.label, Literal(a["name"])))
            g.add((uri, FB.status, Literal(a["status"])))
            if a.get("person_id"):
                g.add((uri, FB.hasSubject, person_uri(a["person_id"])))
            if a.get("dose"):
                g.add((uri, FB.doseAmount, Literal(a["dose"])))
            if a.get("frequency"):
                g.add((uri, FB.doseFrequency, Literal(a["frequency"])))

            # SKOS thesaurus — routine name as prefLabel, synonyms as altLabel,
            # asset_type as the broader concept scheme this routine belongs to
            if a["asset_type"] == "routine":
                g.add((uri, RDF.type, SKOS.Concept))
                g.add((uri, SKOS.prefLabel, Literal(a["name"])))
                for syn in (a.get("synonyms") or []):
                    g.add((uri, SKOS.altLabel, Literal(syn)))
                    g.add((uri, FB.hasSynonym, Literal(syn)))
                broader = DATA[f"concept-scheme/{a['asset_type']}"]
                g.add((broader, RDF.type, SKOS.Concept))
                g.add((broader, SKOS.prefLabel, Literal(a["asset_type"])))
                g.add((uri, SKOS.broader, broader))

    with conn.cursor() as cur:
        query = """
            SELECT id, title, event_type, starts_at, ends_at, status,
                   person_id, asset_id, precedence_rank, blocks_person,
                   superseded_by_event_id
            FROM personal.event
            WHERE status NOT IN ('cancelled')
            ORDER BY starts_at DESC
        """
        if limit_events:
            query += f" LIMIT {limit_events}"
        cur.execute(query)
        for e in cur.fetchall():
            uri = event_uri(e["id"])
            cls = EVENT_TYPE_CLASS.get(e["event_type"], FB.Event)
            g.add((uri, RDF.type, cls))
            g.add((uri, RDFS.label, Literal(e["title"] or "")))
            if e.get("starts_at"):
                g.add((uri, FB.startsAt, Literal(e["starts_at"].isoformat(), datatype=XSD.dateTime)))
            if e.get("ends_at"):
                g.add((uri, FB.endsAt, Literal(e["ends_at"].isoformat(), datatype=XSD.dateTime)))
            if e.get("precedence_rank") is not None:
                g.add((uri, FB.precedenceRank, Literal(e["precedence_rank"], datatype=XSD.integer)))
            if e.get("blocks_person") is not None:
                g.add((uri, FB.blocksPerson, Literal(e["blocks_person"], datatype=XSD.boolean)))
            if e.get("person_id"):
                g.add((uri, FB.hasSubject, person_uri(e["person_id"])))
            if e.get("asset_id"):
                g.add((uri, FB.linkedToRoutine, asset_uri(e["asset_id"])))
            if e.get("superseded_by_event_id"):
                g.add((uri, FB.supersedes, event_uri(e["superseded_by_event_id"])))
            # No hasSubject and no linkedToRoutine at all -> flag as UnattachedEvent
            if not e.get("person_id") and not e.get("asset_id"):
                g.add((uri, RDF.type, FB.UnattachedEvent))

    conn.close()
    return g


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--out", default="familybrain-export.ttl")
    ap.add_argument("--limit-events", type=int, default=2000,
                     help="cap events exported (production DB has hundreds of thousands of generated rows)")
    args = ap.parse_args()

    graph = export(args.db_url, args.limit_events)
    graph.serialize(destination=args.out, format="turtle")
    print(f"Exported {len(graph)} triples to {args.out}")
