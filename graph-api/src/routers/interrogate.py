"""
Interrogation-layer plan execution + individual primitive endpoints
(Interrogation Layer, Increment 2). Every primitive is deterministic
SQL/Cypher, read-only, no LLM calls inside any primitive — see
src/interrogation/__init__.py's REGISTRY.
"""
import os
from typing import Any

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

from src.interrogation import REGISTRY

router = APIRouter(prefix="/interrogate", tags=["interrogate"])

DATABASE_URL = os.environ.get("DATABASE_URL", "")
MAX_STEPS = 8


def _conn():
    # Matches every other graph-api router's convention (routers/quality.py,
    # routers/schemas.py): no pooling, no Depends() DI, a fresh connection per
    # call. LOAD 'age' is included since variance_pack's routine half and any
    # future primitive may need Cypher access on the same connection.
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    with conn.cursor() as cur:
        cur.execute("LOAD 'age'; SET search_path = ag_catalog, \"$user\", public;")
    conn.commit()
    return conn


class PlanStep(BaseModel):
    primitive: str
    params: dict[str, Any] = {}


class ExecuteRequest(BaseModel):
    plan: list[PlanStep]


def _validate_plan(plan: list[PlanStep]) -> list[tuple[PlanStep, BaseModel]]:
    """
    Validates every step before executing any of them — primitive name must
    exist in the registry, params must match that primitive's Pydantic model,
    max 8 steps. Raises HTTPException(422, <machine-readable body>) on the
    first failure, per spec 2.3 (the Increment 3 planner uses this shape to
    retry or fall back to wa-agent's existing generic search). Returns the
    validated (step, params_model) pairs so execute_plan doesn't re-validate.
    """
    if len(plan) > MAX_STEPS:
        raise HTTPException(
            status_code=422,
            detail={"error": "too_many_steps", "max_steps": MAX_STEPS, "given": len(plan)},
        )
    validated = []
    for step in plan:
        if step.primitive not in REGISTRY:
            raise HTTPException(
                status_code=422,
                detail={"error": "unknown_primitive", "primitive": step.primitive, "known": sorted(REGISTRY)},
            )
        _, ParamsModel, _ = REGISTRY[step.primitive]
        try:
            params = ParamsModel(**step.params)
        except ValidationError as e:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_params", "primitive": step.primitive, "errors": e.errors()},
            )
        validated.append((step, params))
    return validated


@router.post("/execute")
def execute_plan(req: ExecuteRequest):
    validated = _validate_plan(req.plan)

    results = []
    conn = _conn()
    try:
        for step, params in validated:
            run_fn, _, pack_fn = REGISTRY[step.primitive]
            try:
                result = run_fn(conn, params)
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail={"error": "primitive_failed", "primitive": step.primitive, "message": str(e)},
                )
            results.append({
                "primitive": step.primitive,
                "params": step.params,
                "result": result,
                "pack_text": pack_fn(result),
            })
    finally:
        conn.close()

    return {"steps": results}


@router.get("/{primitive}")
def get_primitive(primitive: str, request: Request):
    """Single-primitive GET for testing/dashboard use — POST /execute above is
    the interface the planner (Increment 3) actually drives."""
    if primitive not in REGISTRY:
        raise HTTPException(
            status_code=422,
            detail={"error": "unknown_primitive", "primitive": primitive, "known": sorted(REGISTRY)},
        )
    run_fn, ParamsModel, pack_fn = REGISTRY[primitive]
    query_params = dict(request.query_params)
    try:
        params = ParamsModel(**query_params)
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_params", "primitive": primitive, "errors": e.errors()},
        )

    conn = _conn()
    try:
        result = run_fn(conn, params)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": "primitive_failed", "primitive": primitive, "message": str(e)},
        )
    finally:
        conn.close()

    return {"primitive": primitive, "params": query_params, "result": result, "pack_text": pack_fn(result)}
