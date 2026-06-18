"""ThemeQueryTool — researcher uses this to find under-covered themes and related content."""
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from src.db import fetch_all
from src.llm import embed
import json


class ThemeQueryInput(BaseModel):
    query: str = Field(description="Natural language query or topic to search themes/frameworks by")
    limit: int = Field(default=5, description="Max results to return")


class ThemeQueryTool(BaseTool):
    name: str = "theme_query"
    description: str = (
        "Search decision_architect themes and frameworks by semantic similarity. "
        "Use this to find which themes are under-covered or most relevant to a given topic."
    )
    args_schema: type[BaseModel] = ThemeQueryInput

    def _run(self, query: str, limit: int = 5) -> str:
        vec = embed(query)
        vec_str = "[" + ",".join(str(v) for v in vec) + "]"

        themes = fetch_all(
            """
            SELECT
                t.id, t.name, t.description, t.priority,
                t.last_published, t.publish_cadence,
                (t.embedding <=> %s::vector) AS distance,
                COUNT(pc.id) FILTER (WHERE pc.status = 'published') AS published_count
            FROM decision_architect.theme t
            LEFT JOIN decision_architect.published_content pc ON pc.theme_id = t.id
            WHERE t.active = true AND t.embedding IS NOT NULL
            GROUP BY t.id
            ORDER BY distance
            LIMIT %s
            """,
            (vec_str, limit),
        )

        if not themes:
            return "No themes found. The embedding index may not be populated yet."

        result = []
        for t in themes:
            result.append({
                "id": t["id"],
                "name": t["name"],
                "description": t["description"],
                "priority": t["priority"],
                "last_published": str(t["last_published"]) if t["last_published"] else "never",
                "published_count": t["published_count"],
                "relevance_score": round(1 - float(t["distance"]), 3),
            })
        return json.dumps(result, indent=2)
