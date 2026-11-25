#!/usr/bin/env python3
"""What does Glenn's address book know that FamilyBrain does not, and vice versa?

    python scripts/compare_contacts.py
    python scripts/compare_contacts.py --outlook-json cached.json   # skip the fetch
    python scripts/compare_contacts.py --json findings.json

Reads only. Writes nothing to the database and **nothing to any address
book** — see *16-skill-contacts*, "The address book is downstream".

## Why compare at all

FamilyBrain derives its register from mail it has already read
(`scripts/derive_contacts.py`). Glenn's Outlook address book is the same
household's contacts, kept by hand. Where the two disagree, one of them is
wrong, and which one it is tells you something different each time:

  * **Outlook has a field FamilyBrain does not** — the extraction missed it.
    That is a bug in `contact_extract`, and the fix belongs there, not in the
    row. A good direct write fixes one case and hides that the pipeline would
    have got the next twenty wrong (*15-skill-knowledge-and-pickup*).
  * **FamilyBrain has a field Outlook does not** — the brain learned
    something from the mail that the address book never recorded. That is the
    system working, and it is the argument for the ingest path.
  * **They disagree on the same field** — somebody has moved, changed
    practice or renamed, and the one with the later evidence wins. The mail
    carries a date; an address-book row does not.

The disagreements are the output. Counts alone cannot tell you which of the
three cases you are looking at.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


contact_extract = _load("contact_extract",
                        ROOT / "email-sync" / "src" / "contact_extract.py")
derive_contacts = _load("derive_contacts", ROOT / "scripts" / "derive_contacts.py")


def fetch_outlook(account: int = 1) -> dict:
    """The address book, via the container that holds the refresh token."""
    out = subprocess.run(
        ["docker", "exec", "-w", "/app", "familybrain-email-sync",
         "python", "-m", "scripts.dump_outlook_contacts", "--account", str(account)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    if out.returncode != 0:
        raise SystemExit(
            "could not read the Outlook address book:\n"
            + (out.stderr or "").strip()
            + "\n\nIf this says Contacts.Read is missing, the account has to "
              "sign in again:\n  docker exec -it familybrain-email-sync "
              "python -m src.auth_helper --outlook --save-account 1")
    return json.loads(out.stdout)


# ── Comparable shapes ────────────────────────────────────────────────────────

def _norm_name(value: str | None) -> str:
    """A name reduced to what two records would have to share to be the same.

    Case, punctuation and honorifics go; word order does not, except that
    "Surname, Given" is turned round, because Outlook files a contact that
    way and a signature does not.
    """
    name = re.sub(r'[^\w\s,]', " ", (value or "")).strip()
    if "," in name:
        surname, _, given = name.partition(",")
        name = f"{given.strip()} {surname.strip()}"
    name = re.sub(r'(?<!\w)(?:mr|mrs|ms|miss|dr|prof|master)(?!\w)', " ",
                  name, flags=re.I)
    return re.sub(r'\s+', " ", name).strip().casefold()


def _norm_org(value: str | None) -> str:
    """An organisation reduced the same way, with the legal suffix dropped.

    "Centre of Movement" and "Centre of Movement Pty Ltd" are one
    organisation, and reporting them as a disagreement would bury the real
    ones.
    """
    org = re.sub(r'[^\w\s]', " ", (value or "")).casefold()
    org = re.sub(r'(?<!\w)(?:pty|ltd|limited|inc|llc|the)(?!\w)', " ", org)
    return re.sub(r'\s+', " ", org).strip()


def _phones(*values: str | None) -> list[str]:
    """Every Australian number in these values, normalised one way.

    Outlook stores "+61 436 614 955", "0436614955" and "0436 614 955" as the
    whim of whoever typed them; a signature does the same. Both sides go
    through `contact_extract.find_phones` so a disagreement is a real
    difference and not two spellings of one number.
    """
    found: list[str] = []
    for value in values:
        if not value:
            continue
        if isinstance(value, list):
            text = " , ".join(str(v) for v in value)
        else:
            text = str(value)
        phones = contact_extract.find_phones(text)
        for kind in ("mobile", "landline", "national"):
            for p in phones[kind]:
                if p not in found:
                    found.append(p)
    return found


def _outlook_rows(payload: dict) -> list[dict]:
    rows = []
    for c in payload.get("contacts", []):
        emails = [(e.get("address") or "").lower().strip()
                  for e in (c.get("emailAddresses") or [])]
        emails = [e for e in emails if "@" in e]
        addr = c.get("businessAddress") or c.get("homeAddress") or {}
        rows.append({
            "source": "outlook",
            "id": c.get("id"),
            "emails": emails,
            "name": c.get("displayName") or " ".join(
                x for x in (c.get("givenName"), c.get("surname")) if x) or None,
            "organisation": c.get("companyName") or None,
            "role": c.get("jobTitle") or c.get("department") or None,
            "mobile": (_phones(c.get("mobilePhone")) or [None])[0],
            "phones": _phones(c.get("mobilePhone"), c.get("businessPhones"),
                              c.get("homePhones")),
            "address": ", ".join(
                x for x in (addr.get("street"), addr.get("city"),
                            addr.get("state"), addr.get("postalCode")) if x) or None,
            "categories": c.get("categories") or [],
            "notes": (c.get("personalNotes") or "")[:200] or None,
            "last_modified": c.get("lastModifiedDateTime"),
        })
    return rows


def _fb_rows(candidates: list[dict]) -> list[dict]:
    return [{
        "source": "familybrain",
        "emails": [c["email"]],
        "name": c.get("person_name"),
        "organisation": c.get("organisation"),
        "role": c.get("role"),
        "mobile": c.get("mobile"),
        "phones": _phones(c.get("mobile"), c.get("landline"), c.get("national")),
        "address": c.get("address"),
        "last_contact": c.get("last_contact"),
        "ingested": c.get("ingested"),
        "group": c.get("group"),
        "on_person_row": bool(c.get("person")),
        "person": c.get("person"),
        "assets": c.get("assets") or [],
        "org_evidence": (c.get("evidence") or {}).get("organisation"),
    } for c in candidates]


# ── The comparison ───────────────────────────────────────────────────────────

FIELDS = ("name", "organisation", "role", "address")


def compare(fb: list[dict], ol: list[dict]) -> dict:
    by_email: dict[str, dict] = {}
    for row in ol:
        for e in row["emails"]:
            by_email.setdefault(e, row)

    matched, fb_only = [], []
    seen_outlook: set[str] = set()
    for row in fb:
        hit = by_email.get(row["emails"][0])
        if hit:
            seen_outlook.add(hit["id"])
            matched.append((row, hit))
        else:
            fb_only.append(row)

    ol_only = [r for r in ol if r["id"] not in seen_outlook]
    no_email = [r for r in ol_only if not r["emails"]]

    disagreements, fb_missing, ol_missing = [], [], []
    for row, hit in matched:
        for field in FIELDS:
            mine, theirs = row.get(field), hit.get(field)
            if field == "name":
                same = _norm_name(mine) == _norm_name(theirs)
            elif field == "organisation":
                same = _norm_org(mine) == _norm_org(theirs)
            else:
                same = (mine or "").casefold() == (theirs or "").casefold()
            if mine and theirs and not same:
                disagreements.append({"email": row["emails"][0], "field": field,
                                      "familybrain": mine, "outlook": theirs,
                                      "org_evidence": row.get("org_evidence")})
            elif theirs and not mine:
                fb_missing.append({"email": row["emails"][0], "field": field,
                                   "outlook": theirs})
            elif mine and not theirs:
                ol_missing.append({"email": row["emails"][0], "field": field,
                                   "familybrain": mine})

        mine_p, theirs_p = set(row["phones"]), set(hit["phones"])
        if mine_p and theirs_p and mine_p != theirs_p:
            disagreements.append({
                "email": row["emails"][0], "field": "phone",
                "familybrain": sorted(mine_p), "outlook": sorted(theirs_p)})
        elif theirs_p - mine_p and not mine_p:
            fb_missing.append({"email": row["emails"][0], "field": "phone",
                               "outlook": sorted(theirs_p)})
        elif mine_p - theirs_p and not theirs_p:
            ol_missing.append({"email": row["emails"][0], "field": "phone",
                               "familybrain": sorted(mine_p)})

    return {"matched": matched, "fb_only": fb_only, "ol_only": ol_only,
            "ol_no_email": no_email, "disagreements": disagreements,
            "fb_missing": fb_missing, "ol_missing": ol_missing}


def report(result: dict, fb: list[dict], ol: list[dict]) -> None:
    print(f"FamilyBrain derived {len(fb)} candidate senders; "
          f"Outlook holds {len(ol)} contacts.")
    print(f"  {len(result['matched']):4d} in both (matched on an email address)")
    print(f"  {len(result['ol_only']):4d} Outlook only"
          f" — of which {len(result['ol_no_email']):d} carry no email address at all")
    print(f"  {len(result['fb_only']):4d} FamilyBrain only")
    print()

    print(f"=== {len(result['disagreements'])} disagreements ===")
    print("Both sides state a value and the values differ. The mail carries a "
          "date; an address-book row does not.")
    for d in result["disagreements"]:
        note = f"  [fb organisation came from: {d['org_evidence']}]" if d.get(
            "org_evidence") and d["field"] == "organisation" else ""
        print(f"\n  {d['email']}  ({d['field']})")
        print(f"      FamilyBrain: {d['familybrain']}")
        print(f"      Outlook:     {d['outlook']}{note}")

    print(f"\n\n=== {len(result['fb_missing'])} fields Outlook has and "
          f"FamilyBrain missed ===")
    print("Each of these is a question for contact_extract: was it in the "
          "signature and not read, or never in the mail at all?")
    for m in sorted(result["fb_missing"], key=lambda m: m["field"]):
        print(f"  {m['email']:46s} {m['field']:13s} {m['outlook']}")

    print(f"\n\n=== {len(result['ol_missing'])} fields FamilyBrain has and "
          f"Outlook does not ===")
    print("What the brain learned from the mail that the address book never "
          "recorded. This is the case for the ingest path.")
    for m in sorted(result["ol_missing"], key=lambda m: m["field"])[:60]:
        print(f"  {m['email']:46s} {m['field']:13s} {m['familybrain']}")
    if len(result["ol_missing"]) > 60:
        print(f"  ... and {len(result['ol_missing']) - 60} more")

    named = [r for r in result["ol_only"] if r["emails"]]
    print(f"\n\n=== {len(named)} Outlook contacts with an email address that "
          f"FamilyBrain has never derived ===")
    print("Either the household does not correspond with them by mail, or the "
          "sender never cleared the ingest threshold.")
    for r in sorted(named, key=lambda r: (r["name"] or ""))[:40]:
        print(f"  {(r['name'] or '')[:28]:28s} {(r['organisation'] or '')[:24]:24s} "
              f"{(r['mobile'] or ''):14s} {r['emails'][0]}")
    if len(named) > 40:
        print(f"  ... and {len(named) - 40} more")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", type=int, default=1)
    ap.add_argument("--outlook-json", metavar="PATH",
                    help="a dump from a previous run, instead of fetching")
    ap.add_argument("--save-outlook", metavar="PATH",
                    help="write the fetched address book here")
    ap.add_argument("--json", metavar="PATH", help="write the findings as JSON")
    args = ap.parse_args()

    payload = (json.loads(pathlib.Path(args.outlook_json).read_text(encoding="utf-8"))
               if args.outlook_json else fetch_outlook(args.account))
    if args.save_outlook:
        pathlib.Path(args.save_outlook).write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8")

    ol = _outlook_rows(payload)
    fb = _fb_rows(derive_contacts.derive())
    result = compare(fb, ol)
    report(result, fb, ol)

    if args.json:
        serialisable = {k: v for k, v in result.items() if k != "matched"}
        serialisable["matched"] = [{"familybrain": a, "outlook": b}
                                   for a, b in result["matched"]]
        pathlib.Path(args.json).write_text(
            json.dumps(serialisable, indent=2, default=str), encoding="utf-8")
        print(f"\nfindings written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
