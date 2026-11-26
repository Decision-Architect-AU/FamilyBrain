#!/usr/bin/env python3
"""Who does this household actually deal with? Derive it from the mail.

    python scripts/derive_contacts.py                 # the report
    python scripts/derive_contacts.py --json out.json  # the candidates, as data
    python scripts/derive_contacts.py --sender caity@centreofmovement.com.au

Reads only. Writes nothing to the database, and nothing to anybody's address
book — see *16-skill-contacts* for why the write path is a separate decision.

## Why this exists

`audience._provider_audience` decides which child a provider's notice is
about by asking the register: *who holds an active asset naming this sender
as their provider?* That is the fix for two separate faults — a household
electricity bill filed as a child's item because her name was in the body,
and every strings-teacher email attributed to both children because the
cello routine carried no `provider_email`.

Both fixes depend on the register knowing the sender. Four assets carry a
provider email. The brain reads mail from hundreds of senders a month. So the
register is the bottleneck, and this is what fills it.

## What counts as a candidate

A sender the brain **routinely** ingests, which is not the same as one it has
ever ingested. 1,069 addresses have had at least one message ingested; most
are newsletters that happened to carry a date once. The test is two or more
ingested messages *and* a majority of that sender's mail ingested — the
shape of somebody the household corresponds with rather than somebody who
mails it.

Three sets are included whatever their volume, because the system has already
made a judgement about them:

  * an address or domain on `sender_allow` / `sender_tentative`
  * an address named as a provider on an asset (the keys
    `_provider_audience` reads, so this script and that function can never
    disagree about what a provider email is)
  * an address already on a `personal.person` row

## Last contact

From the source email's `received_at`, never `updated_at`. `updated_at` moves
whenever anything internal touches a row, and measured by it 76 of 78 stale
jobs once looked freshly active because somebody had been working on the
pipeline that day. See *20-traps*.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The extraction itself lives with the other decomposers in email-sync/src,
# because the ingest path calls it per message; this script only runs it over
# history. Loaded by path so the two cannot drift apart.
_spec = importlib.util.spec_from_file_location(
    "contact_extract", ROOT / "email-sync" / "src" / "contact_extract.py")
contact_extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contact_extract)

# The facts keys audience._provider_audience reads. Kept in one place here so
# a key added there is added here in the same change.
PROVIDER_FACT_KEYS = ("provider_email", "contact_email", "sender_address",
                      "billing_email")

MIN_INGESTED = 2
MIN_RATIO = 0.5
BODIES_PER_SENDER = 4
BODY_CHARS = 8000


def psql_json(sql: str) -> list[dict]:
    """One JSON document out of psql, so no value needs field-splitting."""
    # Explicit utf-8: email bodies carry emoji, curly quotes and non-breaking
    # spaces, and Python on Windows decodes a subprocess as cp1252 by
    # default, which dies on the first one of them.
    out = subprocess.run(
        ["docker", "exec", "familybrain-postgres", "psql", "-U", "geoff",
         "-d", "openclaw", "-t", "-A", "-c", sql],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )
    if out.returncode != 0:
        raise SystemExit(f"could not reach the database:\n{out.stderr.strip()}")
    text = out.stdout.strip()
    return json.loads(text) if text and text != "null" else []


CANDIDATE_SQL = f"""
WITH sender AS (
  SELECT lower(from_address) AS addr,
         count(*) FILTER (WHERE ingest_status = 'ingested') AS ingested,
         count(*)                                           AS total,
         -- received_at, not updated_at: see 20-traps
         max(received_at) FILTER (WHERE ingest_status = 'ingested') AS last_ingested_at,
         max(received_at)                                           AS last_seen_at,
         mode() WITHIN GROUP (ORDER BY from_name)                   AS from_name
    FROM personal.email_message
   WHERE from_address LIKE '%@%'
   GROUP BY 1
),
allowed AS (
  SELECT lower(value) AS val, filter_type
    FROM personal.email_filter
   WHERE enabled AND filter_type IN ('sender_allow', 'sender_tentative')
),
provider_fact AS (
  SELECT DISTINCT lower(f.value) AS addr, a.id AS asset_id, a.name AS asset_name,
         a.asset_type, a.person_id, a.status, f.key AS fact_key
    FROM personal.asset a, jsonb_each_text(a.facts) AS f(key, value)
   WHERE f.key IN ({", ".join("'" + k + "'" for k in PROVIDER_FACT_KEYS)})
     AND f.value LIKE '%@%'
),
known_person AS (
  SELECT lower(email) AS addr, id, name, groups, relationship,
         organisation, speciality, phone
    FROM personal.person
   WHERE email LIKE '%@%'
),
candidate AS (
  SELECT s.*,
         (SELECT array_agg(DISTINCT a.filter_type) FROM allowed a
           WHERE a.val = s.addr OR s.addr LIKE '%@' || a.val
              OR split_part(s.addr, '@', 2) LIKE '%.' || a.val) AS filters,
         (SELECT jsonb_agg(DISTINCT jsonb_build_object(
                   'asset_id', p.asset_id, 'asset_name', p.asset_name,
                   'asset_type', p.asset_type, 'person_id', p.person_id,
                   'status', p.status, 'fact_key', p.fact_key))
            FROM provider_fact p WHERE p.addr = s.addr)              AS assets,
         (SELECT jsonb_build_object('id', k.id, 'name', k.name,
                   'groups', k.groups, 'relationship', k.relationship,
                   'organisation', k.organisation, 'speciality', k.speciality,
                   'phone', k.phone)
            FROM known_person k WHERE k.addr = s.addr LIMIT 1)       AS person
    FROM sender s
)
SELECT jsonb_pretty(jsonb_agg(c ORDER BY c.ingested DESC))
  FROM (
    SELECT addr, ingested, total, last_ingested_at, last_seen_at, from_name,
           filters, assets, person
      FROM candidate
     WHERE (ingested >= {MIN_INGESTED}
            AND ingested::numeric / NULLIF(total, 0) >= {MIN_RATIO})
        OR filters IS NOT NULL
        OR assets  IS NOT NULL
        OR person  IS NOT NULL
  ) c;
"""


def bodies_sql(addresses: list[str]) -> str:
    """The most recent ingested bodies for each candidate.

    The head of the body, not the tail: a signature sits above the quoted
    history, and `contact_extract.strip_quoted` needs the top of the message
    to find where that history starts.
    """
    values = ", ".join("(" + _lit(a) + ")" for a in addresses)
    return f"""
WITH want(addr) AS (VALUES {values})
SELECT jsonb_pretty(jsonb_agg(x))
  FROM (
    SELECT w.addr,
           jsonb_agg(jsonb_build_object(
             'email_id', b.id, 'received_at', b.received_at,
             'from_name', b.from_name, 'subject', b.subject,
             'body', left(b.body, {BODY_CHARS})) ORDER BY b.received_at DESC) AS bodies
      FROM want w
      CROSS JOIN LATERAL (
        SELECT m.id, m.received_at, m.from_name, m.subject, n.body
          FROM personal.email_message m
          JOIN personal.note n ON n.id = m.note_id
         WHERE lower(m.from_address) = w.addr
           AND m.ingest_status = 'ingested'
           AND length(n.body) > 120
         ORDER BY m.received_at DESC
         LIMIT {BODIES_PER_SENDER}
      ) b
     GROUP BY w.addr
  ) x;
"""


def _lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _merge(into: dict, found: dict) -> None:
    """Fill a field from a newer message only if no older message said it.

    Messages arrive newest first, so first-seen wins. A provider who changed
    phone number states the new one in the newest email, and an old signature
    must not overwrite it.
    """
    for key in ("person_name", "role", "organisation", "mobile", "landline",
                "national", "address", "website"):
        if not into.get(key) and found.get(key):
            into[key] = found[key]
            into["evidence"][key] = found["_email_id"]


HOUSEHOLD_SQL = """
SELECT jsonb_agg(w) FROM (
  SELECT DISTINCT unnest(string_to_array(name, ' ')) AS w
    FROM personal.person
   WHERE groups && ARRAY['family', 'extended']
) t WHERE length(w) > 2;
"""


def household_names() -> tuple[str, ...]:
    """The family's own names, to be kept out of every extracted field.

    Passed to `contact_extract.extract_contact` exactly as `resolve_audience`
    takes them, and for the same reason: a name appearing in a body says
    nothing about who sent it.
    """
    return tuple(psql_json(HOUSEHOLD_SQL))


def derive(one_sender: str | None = None) -> list[dict]:
    exclude = household_names()
    rows = psql_json(CANDIDATE_SQL)
    if one_sender:
        rows = [r for r in rows if r["addr"] == one_sender.lower()]
        if not rows:
            raise SystemExit(f"{one_sender} is not a candidate sender")
    if not rows:
        return []

    bodies: dict[str, list[dict]] = {}
    batch = 60
    addrs = [r["addr"] for r in rows]
    for i in range(0, len(addrs), batch):
        for entry in psql_json(bodies_sql(addrs[i:i + batch])):
            bodies[entry["addr"]] = entry["bodies"]

    out = []
    for row in rows:
        cand = {
            "email": row["addr"],
            "ingested": row["ingested"],
            "total": row["total"],
            # received_at of the newest message actually ingested, which is
            # what "when did we last hear from them" means.
            "last_contact": row["last_ingested_at"] or row["last_seen_at"],
            "person_name": None, "role": None, "organisation": None,
            "mobile": None, "landline": None, "national": None,
            "address": None, "website": None,
            "filters": row.get("filters") or [],
            "assets": row.get("assets") or [],
            "person": row.get("person"),
            "bodies_read": 0,
            "evidence": {},
        }
        for msg in bodies.get(row["addr"], []):
            found = contact_extract.extract_contact(
                msg["body"],
                from_name=msg.get("from_name") or row.get("from_name") or "",
                from_address=row["addr"], exclude_names=exclude)
            found["_email_id"] = msg["email_id"]
            cand["bodies_read"] += 1
            _merge(cand, found)
        if not cand["person_name"]:
            cand["person_name"] = contact_extract._display_name(
                row.get("from_name") or "", exclude)
        if not cand["organisation"]:
            # The website a signature links to names the organisation better
            # than the sending domain does: Education Queensland mails from
            # eq.edu.au and links to varsitycollege.eq.edu.au, and only the
            # second says which school. Both are marked as derived, so
            # nothing downstream mistakes either for something a sender said.
            host = re.sub(r'^(?:https?://)?(?:www\.)?', "",
                          cand["website"] or "").split("/")[0]
            cand["organisation"] = (
                contact_extract.organisation_from_host(host)
                or contact_extract.organisation_from_domain(row["addr"]))
            cand["evidence"]["organisation"] = "website host" if (
                host and cand["organisation"]) else "sender domain"
        cand["group"] = _group_for(cand)
        out.append(cand)
    return out


def _group_for(cand: dict) -> str:
    """provider, contact or unknown.

    A sender with an asset against their name is a provider by definition —
    that is what the asset says. A sender the household corresponds with by
    name, and whose signature carries a role, is also a provider. Everything
    else is a contact: somebody the household hears from, which is worth
    recording and is not a claim that they are engaged to do anything.
    """
    if cand["assets"]:
        return "provider"
    if cand["person"] and cand["person"].get("groups"):
        return cand["person"]["groups"][0]
    if cand["role"]:
        return "provider"
    return "contact"


def report(cands: list[dict]) -> None:
    n = len(cands)
    known = [c for c in cands if c["person"]]
    with_asset = [c for c in cands if c["assets"]]
    print(f"{n} candidate senders "
          f"({MIN_INGESTED}+ ingested and {int(MIN_RATIO * 100)}%+ ingested, "
          f"plus every allow-listed, asset-named and already-known address)")
    print(f"  {len(known):4d} already on personal.person")
    print(f"  {n - len(known):4d} not")
    print(f"  {len(with_asset):4d} named as a provider on an asset")
    print()
    for field, label in (("person_name", "a person's name"),
                         ("role", "a role"),
                         ("mobile", "a mobile"),
                         ("landline", "a landline"),
                         ("national", "a 13/1300/1800 number"),
                         ("address", "a street address"),
                         ("website", "a website")):
        got = sum(1 for c in cands if c.get(field))
        print(f"  {got:4d} with {label}")
    phone_any = sum(1 for c in cands
                    if c["mobile"] or c["landline"] or c["national"])
    print(f"  {phone_any:4d} with any phone number")
    print(f"  {sum(c['bodies_read'] for c in cands):4d} bodies read")
    print()
    groups = Counter(c["group"] for c in cands)
    print("  by group: " + ", ".join(f"{k} {v}" for k, v in groups.most_common()))
    print()

    named = [c for c in cands if c["person_name"]]
    print(f"The {min(40, len(named))} highest-volume senders that name a person:")
    for c in sorted(named, key=lambda c: -c["ingested"])[:40]:
        phone = c["mobile"] or c["landline"] or c["national"] or ""
        print(f"  {c['ingested']:4d}  {(c['person_name'] or '')[:26]:26s} "
              f"{(c['organisation'] or '')[:30]:30s} {phone:14s} "
              f"{(c['role'] or '')[:28]:28s} {str(c['last_contact'])[:10]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", metavar="PATH", help="write the candidates as JSON")
    ap.add_argument("--sender", help="derive one address only, and print its evidence")
    args = ap.parse_args()

    cands = derive(args.sender)
    if args.sender:
        print(json.dumps(cands, indent=2, default=str))
        return 0
    report(cands)
    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(cands, indent=2, default=str), encoding="utf-8")
        print(f"\n{len(cands)} candidates written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
