"""Family Brain Graph API — FastAPI entry point."""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.routers import graph, ingest, quality, templates, schemas

app = FastAPI(title="Family Brain Graph API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(graph.router)
app.include_router(ingest.router)
app.include_router(quality.router)
app.include_router(templates.router)
app.include_router(schemas.router)


@app.get("/health")
def health():
    return {"status": "ok"}


# Serve built React app at /explorer if dist exists
_dist = "/app/explorer-dist"
if os.path.isdir(_dist):
    app.mount("/explorer", StaticFiles(directory=_dist, html=True), name="explorer")
