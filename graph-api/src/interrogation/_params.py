"""Shared Pydantic param helpers for interrogation primitives."""


def split_list(v):
    """Lets list-typed params accept either a real list (POST /interrogate/execute)
    or a comma-separated string (GET /interrogate/{primitive} query params)."""
    if v is None or isinstance(v, list):
        return v
    return [x.strip() for x in str(v).split(",") if x.strip()]
