"""Natural language ingestor endpoints."""
import json
import os
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src import db

router = APIRouter(prefix="/ingest", tags=["ingest"])

OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
EXTRACT_MODEL = os.environ.get("EXTRACT_MODEL", "qwen2.5:14b")

GRAPH_SCHEMA_CONTEXT = """
You are a knowledge graph extraction assistant for a family administration system.

Known node labels and their key properties:
- Person: name, dob, email, phone
- HealthPractitioner: name, specialty, provider_number, clinic
- NDISProvider: name, abn, service_type
- NDISPlan: participant, plan_start, plan_end, total_budget
- NDISServiceDelivery: date, provider, hours, amount, category
- NDISReceipt: date, provider, amount, invoice_ref, status
- Property: address, suburb, state, type
- Trust: name, trustee, abn
- LoanFacility: lender, balance, rate, next_payment
- Vehicle: make, model, year, rego, state
- InsurancePolicy: policy_number, insurer, type, premium, renewal_date
- InsuranceClaim: date, amount, status
- RecurringPayment: name, amount, frequency, next_due, entity
- Medication: name, dose, frequency, prescriber
- Appointment: practitioner, specialty, clinic_name, clinic_address, date, time, person, status
- Bill: provider, amount, due_date, status, cost_centre
- Trip: destination, trip_name, start_date, end_date, travellers, status
- Flight: airline, flight_number, departure_airport, departure_time, arrival_airport, arrival_time, booking_ref
- Accommodation: property_name, address, check_in_date, check_out_date, confirmation_number
- School: name, suburb
- Activity: name, day_of_week, term, child

Known relationship types:
- (Person)-[:TREATED_BY]->(HealthPractitioner)
- (Person)-[:PRESCRIBED]->(Medication)
- (NDISPlan)-[:FUNDED_BY]->(NDISProvider)
- (Person)-[:OWNS]->(Property)
- (Person)-[:DRIVES]->(Vehicle)
- (Vehicle)-[:INSURED_BY]->(InsurancePolicy)
- (Person)-[:PAYS]->(RecurringPayment)
- (Person)-[:HAD_APPOINTMENT]->(Appointment)
- (Appointment)-[:WITH]->(HealthPractitioner)
- (Person)-[:PARENT_OF]->(Person)
- (Trip)-[:INCLUDES]->(Flight)
- (Trip)-[:INCLUDES]->(Accommodation)

Rules:
- Only use labels and relationship types from the lists above
- If a label doesn't fit, use the closest match and flag it
- Extract only facts explicitly stated or clearly implied — do not infer
- If an entity likely already exists in the graph, set "match_on" to the properties to use for MERGE
- Return ONLY valid JSON, no explanation, no markdown
"""

CONTEXT_HINTS = {
    "health":    "Prioritise HealthPractitioner, Appointment, Medication labels.",
    "ndis":      "Prioritise NDISPlan, NDISProvider, NDISServiceDelivery, NDISReceipt labels.",
    "finance":   "Prioritise Bill, RecurringPayment, LoanFacility labels.",
    "property":  "Prioritise Property, Trust, OwnershipStatement labels.",
    "insurance": "Prioritise InsurancePolicy, InsuranceClaim labels.",
    "travel":    "Prioritise Trip, Flight, Accommodation labels.",
    "vehicle":   "Prioritise Vehicle, InsurancePolicy labels.",
    "family":    "Prioritise Person, School, Activity labels.",
}

OUTPUT_FORMAT = """
Return a JSON object with this exact shape:

{
  "nodes": [
    {
      "id": "temp_1",
      "label": "HealthPractitioner",
      "properties": {"name": "Dr Sarah Chen", "specialty": "Paediatrician"},
      "match_on": ["name"],
      "confidence": "high",
      "note": ""
    }
  ],
  "edges": [
    {
      "id": "temp_e1",
      "type": "TREATED_BY",
      "from": "temp_2",
      "to": "temp_1",
      "properties": {},
      "confidence": "medium",
      "note": ""
    }
  ]
}

Confidence levels: "high" (explicitly stated), "medium" (clearly implied), "low" (inferred).
temp IDs are arbitrary strings used to link nodes to edges within this response only.
"""


class ExtractRequest(BaseModel):
    text: str
    context_hint: str = "auto"


class CommitNode(BaseModel):
    id: str
    label: str
    properties: dict[str, Any]
    match_on: list[str] = []
    confidence: str = "high"
    note: str = ""


class CommitEdge(BaseModel):
    id: str
    type: str
    from_: str
    to: str
    properties: dict[str, Any] = {}
    confidence: str = "high"
    note: str = ""

    class Config:
        populate_by_name = True
        fields = {"from_": {"alias": "from"}}


class CommitRequest(BaseModel):
    nodes: list[CommitNode]
    edges: list[CommitEdge]


@router.post("/extract")
def extract(req: ExtractRequest):
    t0 = time.time()

    hint_text = CONTEXT_HINTS.get(req.context_hint.lower(), "")
    system = GRAPH_SCHEMA_CONTEXT
    if hint_text:
        system += f"\n\nDomain focus: {hint_text}"
    system += "\n\n" + OUTPUT_FORMAT

    prompt = f"Extract entities and relationships from this text:\n\n{req.text}"

    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": EXTRACT_MODEL, "prompt": prompt, "system": system, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")

    # Parse JSON from LLM response
    try:
        # Strip markdown fences if present
        clean = raw.strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.split("\n")[1:])
            if clean.endswith("```"):
                clean = clean[:-3].strip()
        proposal = json.loads(clean)
    except Exception:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {raw[:200]}")

    duration_ms = int((time.time() - t0) * 1000)
    return {
        "nodes":            proposal.get("nodes", []),
        "edges":            proposal.get("edges", []),
        "raw_llm_response": raw,
        "model":            EXTRACT_MODEL,
        "duration_ms":      duration_ms,
    }


@router.post("/commit")
def commit(req: CommitRequest):
    temp_to_age: dict[str, str] = {}
    created_nodes: list[dict] = []
    merged_nodes:  list[dict] = []
    created_edges: list[dict] = []
    errors: list[str] = []

    for node in req.nodes:
        try:
            if node.match_on:
                match_props = {k: node.properties[k] for k in node.match_on if k in node.properties}
                props_json = json.dumps(match_props)
                set_parts = ", ".join(f"n.{k} = {json.dumps(v)}" for k, v in node.properties.items())
                if set_parts:
                    cypher = (f"MERGE (n:{node.label} {props_json}) "
                              f"ON CREATE SET {set_parts} ON MATCH SET {set_parts} RETURN n")
                else:
                    cypher = f"MERGE (n:{node.label} {props_json}) RETURN n"
                result = db.cypher_query(cypher)
                if result["nodes"]:
                    age_id = result["nodes"][0]["id"]
                    temp_to_age[node.id] = age_id
                    merged_nodes.append({"temp_id": node.id, "age_id": age_id})
            else:
                result = db.create_node([node.label], node.properties)
                age_id = result["id"]
                temp_to_age[node.id] = age_id
                created_nodes.append({"temp_id": node.id, "age_id": age_id})
        except Exception as e:
            errors.append(f"Node {node.id}: {e}")

    for edge in req.edges:
        try:
            from_id = temp_to_age.get(edge.from_, edge.from_)
            to_id   = temp_to_age.get(edge.to, edge.to)
            result = db.create_edge(from_id, to_id, edge.type, edge.properties)
            created_edges.append({"temp_id": edge.id, "age_id": result["id"]})
        except Exception as e:
            errors.append(f"Edge {edge.id}: {e}")

    return {
        "created_nodes": created_nodes,
        "merged_nodes":  merged_nodes,
        "created_edges": created_edges,
        "errors":        errors,
    }
