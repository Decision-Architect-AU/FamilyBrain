"""
Create and update personal.asset rows and their AGE graph nodes.
Called from the ingest pipeline after asset classification.
"""
import os
import json
import psycopg2
import psycopg2.extras

from .audit import log as audit_log
from .graph import write_asset_node

DB_URL = os.environ.get("DATABASE_URL")


def _conn():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


# ── Fact schemas ──────────────────────────────────────────────────────────────
# Keys validated at write time. Unknown keys are stored but logged.

ASSET_FACT_SCHEMAS = {
    "vehicle": {
        "required": ["make", "model", "year", "rego", "rego_state", "rego_expiry"],
        "optional": ["colour", "vin", "odometer_km", "fuel_type",
                     "insurance_provider", "insurance_expiry", "insurance_policy_no"],
    },
    "medication": {
        "required": ["drug_name", "dose", "frequency", "prescriber"],
        "optional": ["script_number", "pharmacy", "days_supply",
                     "last_filled_date", "repeats_remaining", "pbs_code"],
    },
    "property": {
        "required": ["address", "lot_plan"],
        "optional": ["council", "rates_cycle", "insurance_provider",
                     "insurance_expiry", "strata_manager", "body_corp_levy_cycle"],
    },
    "subscription": {
        "required": ["provider", "plan", "renewal_date", "renewal_period_days"],
        "optional": ["cost", "payment_method", "auto_renews", "account_email"],
    },
    "person": {
        "required": ["full_name"],
        "optional": ["passport_expiry", "passport_number", "drivers_licence_expiry",
                     "drivers_licence_state", "medicare_expiry", "ndis_plan_end",
                     "ndis_plan_type"],
    },
    "device": {
        "required": ["make", "model", "serial_number"],
        "optional": ["purchase_date", "warranty_expiry", "applecare_expiry",
                     "imei", "os_version"],
    },
    "pet": {
        "required": ["name", "species", "breed"],
        "optional": ["dob", "microchip_number", "registration_expiry",
                     "vaccination_due", "vet_name", "desexed"],
    },
}


# ── Default rules per asset type ──────────────────────────────────────────────

def default_rules_for_type(asset_type: str) -> list:
    defaults = {
        "vehicle": [
            {
                "name": "Rego renewal",
                "event_type": "REGO_RENEWAL",
                "event_label": "Vehicle registration due",
                "trigger_source": "facts.rego_expiry",
                "lead_time_days": 30,
                "recurrence": "annual",
                "auto_create": True,
                "collision_aware": False,
                "attendance_mode": "IN_PERSON",
                "travel_buffer_before_min": 15,
                "travel_buffer_after_min": 30,
                "severity_if_missing": "HIGH",
                "enabled": True,
            },
            {
                "name": "Insurance renewal",
                "event_type": "INSURANCE_RENEWAL",
                "event_label": "Vehicle insurance renewal due",
                "trigger_source": "facts.insurance_expiry",
                "lead_time_days": 21,
                "recurrence": "annual",
                "auto_create": True,
                "collision_aware": False,
                "attendance_mode": "ONLINE",
                "severity_if_missing": "HIGH",
                "enabled": True,
            },
            {
                "name": "Car service",
                "event_type": "SERVICE",
                "event_label": "Car service due",
                "trigger_source": "last_event_date",
                "lead_time_days": 14,
                "recurrence": "interval",
                "recurrence_days": 180,
                "auto_create": False,
                "collision_aware": True,
                "attendance_mode": "IN_PERSON",
                "travel_buffer_before_min": 15,
                "travel_buffer_after_min": 60,
                "severity_if_missing": "MEDIUM",
                "enabled": True,
            },
        ],
        "medication": [
            {
                "name": "Script renewal",
                "event_type": "SCRIPT_RENEWAL",
                "event_label": "Script renewal due",
                "trigger_source": "last_event_date",
                "lead_time_days": 7,
                "recurrence": "interval",
                "recurrence_days": None,
                "auto_create": True,
                "collision_aware": False,
                "attendance_mode": "IN_PERSON",
                "severity_if_missing": "HIGH",
                "enabled": True,
            },
        ],
        "subscription": [
            {
                "name": "Renewal reminder",
                "event_type": "RENEWAL",
                "event_label": "Subscription renewal due",
                "trigger_source": "facts.renewal_date",
                "lead_time_days": 14,
                "recurrence": "interval",
                "recurrence_days": None,
                "auto_create": True,
                "collision_aware": False,
                "attendance_mode": "ONLINE",
                "severity_if_missing": "MEDIUM",
                "enabled": True,
            },
        ],
        "person": [
            {
                "name": "Passport renewal",
                "event_type": "PASSPORT_RENEWAL",
                "event_label": "Passport expiring",
                "trigger_source": "facts.passport_expiry",
                "lead_time_days": 180,
                "recurrence": "once",
                "auto_create": True,
                "collision_aware": False,
                "attendance_mode": "IN_PERSON",
                "severity_if_missing": "HIGH",
                "enabled": True,
            },
            {
                "name": "NDIS plan review",
                "event_type": "NDIS_REVIEW",
                "event_label": "NDIS plan review due",
                "trigger_source": "facts.ndis_plan_end",
                "lead_time_days": 60,
                "recurrence": "once",
                "auto_create": False,
                "collision_aware": True,
                "attendance_mode": "IN_PERSON",
                "travel_buffer_before_min": 30,
                "travel_buffer_after_min": 30,
                "severity_if_missing": "HIGH",
                "enabled": True,
            },
        ],
        "device": [
            {
                "name": "Warranty expiry",
                "event_type": "WARRANTY_EXPIRY",
                "event_label": "Warranty expiring",
                "trigger_source": "facts.warranty_expiry",
                "lead_time_days": 30,
                "recurrence": "once",
                "auto_create": True,
                "collision_aware": False,
                "attendance_mode": "ONLINE",
                "severity_if_missing": "LOW",
                "enabled": True,
            },
        ],
        "pet": [
            {
                "name": "Vaccination due",
                "event_type": "VACCINATION",
                "event_label": "Pet vaccination due",
                "trigger_source": "facts.vaccination_due",
                "lead_time_days": 14,
                "recurrence": "annual",
                "auto_create": False,
                "collision_aware": True,
                "attendance_mode": "IN_PERSON",
                "travel_buffer_before_min": 15,
                "travel_buffer_after_min": 15,
                "severity_if_missing": "MEDIUM",
                "enabled": True,
            },
        ],
        "property": [
            {
                "name": "Insurance renewal",
                "event_type": "INSURANCE_RENEWAL",
                "event_label": "Property insurance renewal due",
                "trigger_source": "facts.insurance_expiry",
                "lead_time_days": 21,
                "recurrence": "annual",
                "auto_create": True,
                "collision_aware": False,
                "attendance_mode": "ONLINE",
                "severity_if_missing": "HIGH",
                "enabled": True,
            },
        ],
    }
    return defaults.get(asset_type, [])


# ── Fact building ─────────────────────────────────────────────────────────────

def build_asset_facts(asset_type: str, extracted_fields: dict) -> dict:
    schema    = ASSET_FACT_SCHEMAS.get(asset_type, {})
    known     = set(schema.get("required", []) + schema.get("optional", []))
    facts     = {}
    for k, v in extracted_fields.items():
        if v is not None and v != "":
            facts[k] = v
            if k not in known:
                audit_log("UNKNOWN_FACT_KEY", f"{asset_type}: unknown key '{k}' stored")
    return facts


def _derive_asset_name(asset_type: str, facts: dict, fields: dict) -> str:
    all_fields = {**fields, **facts}
    if asset_type == "vehicle":
        year  = all_fields.get("year", "")
        make  = all_fields.get("make", "")
        model = all_fields.get("model", "")
        rego  = all_fields.get("rego", "")
        name  = " ".join(p for p in [str(year), make, model] if p).strip()
        if rego:
            name += f" ({rego})"
        return name or "Unknown vehicle"
    if asset_type == "medication":
        drug  = all_fields.get("drug_name", all_fields.get("name", ""))
        dose  = all_fields.get("dose", "")
        return f"{drug} {dose}".strip() or "Unknown medication"
    if asset_type == "property":
        return all_fields.get("address", "Unknown property")
    if asset_type == "subscription":
        provider = all_fields.get("provider", "")
        plan     = all_fields.get("plan", "")
        return f"{provider} {plan}".strip() or "Unknown subscription"
    if asset_type == "person":
        return all_fields.get("full_name", all_fields.get("name", "Unknown person"))
    if asset_type == "device":
        make  = all_fields.get("make", "")
        model = all_fields.get("model", "")
        return f"{make} {model}".strip() or "Unknown device"
    if asset_type == "pet":
        return all_fields.get("name", "Unknown pet")
    return all_fields.get("name", f"Unknown {asset_type}")


# ── DB helpers ────────────────────────────────────────────────────────────────

def _insert_asset(row: dict, conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO personal.asset
                (name, asset_type, subtype, status, facts, rules, ref, notes, event_gen_enabled)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
            RETURNING id
            """,
            (
                row["name"], row["asset_type"], row.get("subtype"),
                row.get("status", "active"),
                json.dumps(row.get("facts", {})),
                json.dumps(row.get("rules", [])),
                row.get("ref"), row.get("notes"), True,
            ),
        )
        return cur.fetchone()["id"]


def _update_asset_facts(asset_id: int, facts: dict, conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE personal.asset SET facts = %s::jsonb, updated_at = now() WHERE id = %s",
            (json.dumps(facts), asset_id),
        )


def _fetch_asset(asset_id: int, conn) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM personal.asset WHERE id = %s", (asset_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def find_existing_asset(asset_type: str, facts: dict, conn) -> dict | None:
    """
    Try to match incoming content to an existing asset row.
    1. Unique identifier match (rego, script_number, serial_number, lot_plan)
    2. Name similarity match via pg_trgm
    Returns the asset dict or None.
    """
    uid_keys = {
        "vehicle":      "rego",
        "medication":   "script_number",
        "device":       "serial_number",
        "property":     "lot_plan",
    }
    uid_key = uid_keys.get(asset_type)
    if uid_key and facts.get(uid_key):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM personal.asset WHERE asset_type = %s AND facts->>%s = %s",
                (asset_type, uid_key, str(facts[uid_key])),
            )
            row = cur.fetchone()
            if row:
                return dict(row)

    # Name similarity via pg_trgm
    candidate = _derive_asset_name(asset_type, facts, {})
    if candidate:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *, similarity(name, %s) AS sim
                FROM personal.asset
                WHERE asset_type = %s AND status = 'active'
                  AND similarity(name, %s) > 0.6
                ORDER BY sim DESC
                LIMIT 1
                """,
                (candidate, asset_type, candidate),
            )
            row = cur.fetchone()
            if row:
                return dict(row)

    return None


# ── Main upsert ───────────────────────────────────────────────────────────────

def upsert_asset(asset_route: dict, extracted_fields: dict, source: str) -> dict | None:
    """
    Create or update a personal.asset row and its graph node.
    Returns the asset dict (with id) or None if asset_type could not be determined.
    """
    asset_type = asset_route.get("asset_type")
    if not asset_type:
        return None

    facts = build_asset_facts(asset_type, extracted_fields)

    with _conn() as conn:
        existing = find_existing_asset(asset_type, facts, conn)

        if existing and asset_route.get("is_update"):
            merged = {**existing["facts"], **{k: v for k, v in facts.items() if v}}
            _update_asset_facts(existing["id"], merged, conn)
            conn.commit()
            asset = _fetch_asset(existing["id"], conn)
            audit_log("UPDATE_ASSET", f"Asset {existing['id']} ({asset_type}) updated from {source}")
        elif existing:
            asset = existing
        else:
            asset_id = _insert_asset({
                "name":       _derive_asset_name(asset_type, facts, extracted_fields),
                "asset_type": asset_type,
                "subtype":    asset_route.get("asset_subtype"),
                "status":     "active",
                "facts":      facts,
                "rules":      default_rules_for_type(asset_type),
            }, conn)
            conn.commit()
            asset = _fetch_asset(asset_id, conn)
            audit_log("CREATE_ASSET", f"New {asset_type} asset {asset_id} from {source}")

    if asset:
        write_asset_node(asset)

    return asset


def update_asset_event_dates(asset_id: int, event_date: str | None) -> None:
    """Update last_event_date on the asset after an event is matched or completed."""
    if not event_date:
        return
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE personal.asset
                SET last_event_date = GREATEST(last_event_date, %s::date),
                    updated_at = now()
                WHERE id = %s
                """,
                (event_date, asset_id),
            )
        conn.commit()
