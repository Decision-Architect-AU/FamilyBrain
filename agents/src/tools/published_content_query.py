"""PublishedContentQueryTool — critic and researcher use this to check what's been said before."""
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from src.db import fetch_all
from src.llm import embed
import json


class PublishedContentInput(BaseModel):
    query: str = Field(description="Topic or draft text to check for similarity against published content")
    platform: str = Field(default="", description="Filter by platform: linkedin, podcast, newsletter, twitter. Leave empty for all.")
    limit: int = Field(default=8, description="Max results")


class PublishedContentQueryTool(BaseTool):
    name: str = "published_content_query"
    description: str = (
        "Semantic search over previously published content. "
        "Use to check for repetition before writing a new draft, or to find prior framings of a topic."
    )
    args_schema: type[BaseModel] = PublishedContentInput

    def _run(self, query: str, platform: str = "", limit: int = 8) -> str:
        vec = embed(query)
        vec_str = "[" + ",".join(str(v) for v in vec) + "]"

        platform_clause = "AND pc.platform = %s" if platform else ""
        params = [vec_str, vec_str]
        if platform:
            params.append(platform)
        params.append(limit)

        rows = fetch_all(
            f"""
            SELECT
                pc.id, pc.platform, pc.content_type, pc.title,
                LEFT(pc.body, 300) AS body_preview,
                pc.published_at,
                t.name AS theme_name,
                (pc.embedding <=> %s::vector) AS distance
            FROM decision_architect.published_content pc
            LEFT JOIN decision_architect.theme t ON t.id = pc.theme_id
            WHERE pc.status = 'published' AND pc.embedding IS NOT NULL
            {platform_clause}
            ORDER BY (pc.embedding <=> %s::vector)
            LIMIT %s
            """,
            params,
        )

        if not rows:
            return "No published content found — safe to write fresh."

        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "platform": r["platform"],
                "type": r["content_type"],
                "title": r["title"],
                "preview": r["body_preview"],
                "published": str(r["published_at"]) if r["published_at"] else "unknown",
                "theme": r["theme_name"],
                "similarity": round(1 - float(r["distance"]), 3),
            })
        return json.dumps(result, indent=2)
