"""Graph CRUD + query endpoints."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Any

from src import db

router = APIRouter(prefix="/graph", tags=["graph"])


class CypherRequest(BaseModel):
    cypher: str


class NodeCreate(BaseModel):
    labels: list[str]
    properties: dict[str, Any] = {}


class NodePatch(BaseModel):
    properties: dict[str, Any]


class EdgeCreate(BaseModel):
    startNode: str
    endNode: str
    type: str
    properties: dict[str, Any] = {}


class EdgePatch(BaseModel):
    properties: dict[str, Any]


@router.post("/query")
def query_graph(req: CypherRequest):
    try:
        return db.cypher_query(req.cypher)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/labels")
def list_labels():
    return db.get_all_labels()


@router.get("/relationship-types")
def list_rel_types():
    return db.get_all_rel_types()


@router.get("/schema/{label}")
def schema_for_label(label: str):
    return db.get_schema_for_label(label)


@router.post("/nodes")
def create_node(body: NodeCreate):
    try:
        return db.create_node(body.labels, body.properties)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/nodes/{node_id}")
def get_node(node_id: str):
    node = db.get_node_by_id(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


@router.patch("/nodes/{node_id}")
def patch_node(node_id: str, body: NodePatch):
    node = db.patch_node(node_id, body.properties)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


@router.delete("/nodes/{node_id}")
def delete_node(node_id: str, force: bool = Query(default=False)):
    try:
        db.delete_node(node_id, force=force)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/edges")
def create_edge(body: EdgeCreate):
    try:
        return db.create_edge(body.startNode, body.endNode, body.type, body.properties)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/edges/{edge_id}")
def get_edge(edge_id: str):
    edge = db.get_edge_by_id(edge_id)
    if not edge:
        raise HTTPException(status_code=404, detail="Edge not found")
    return edge


@router.patch("/edges/{edge_id}")
def patch_edge(edge_id: str, body: EdgePatch):
    edge = db.patch_edge(edge_id, body.properties)
    if not edge:
        raise HTTPException(status_code=404, detail="Edge not found")
    return edge


@router.delete("/edges/{edge_id}")
def delete_edge(edge_id: str):
    try:
        db.delete_edge(edge_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
