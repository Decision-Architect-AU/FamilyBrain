"""SchedulerTool — marks approved content as 'published' and updates theme.last_published."""
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from src.db import fetch_all, fetch_one, execute
from src import audit
import json


class SchedulerInput(BaseModel):
    platform: str = Field(
        default="linkedin",
        description="Which platform queue to process: linkedin | podcast | newsletter | twitter",
    )
    max_publish: int = Field(
        default=1,
        description="Max number of approved items to mark as published in this run",
    )


class SchedulerTool(BaseTool):
    name: str = "scheduler_tool"
    description: str = (
        "Dequeue approved content and mark it as published. "
        "Updates theme.last_published so the researcher knows what's been covered. "
        "In production this would also push to the actual platform via API."
    )
    args_schema: type[BaseModel] = SchedulerInput

    def _run(self, platform: str = "linkedin", max_publish: int = 1) -> str:
        rows = fetch_all(
            """SELECT id, title, theme_id, platform
               FROM decision_architect.published_content
               WHERE status = 'approved' AND platform = %s
               ORDER BY created_at
               LIMIT %s""",
            (platform, max_publish),
        )

        if not rows:
            return f"No approved content queued for {platform}."

        published = []
        for row in rows:
            execute(
                """UPDATE decision_architect.published_content
                   SET status = 'published', published_at = now()
                   WHERE id = %s""",
                (row["id"],),
            )
            if row["theme_id"]:
                execute(
                    "UPDATE decision_architect.theme SET last_published = now() WHERE id = %s",
                    (row["theme_id"],),
                )
            audit.log(
                agent="pr_scheduler",
                action_type="publish",
                summary=f"Published: [{platform}] {row['title'][:60]}",
                target_schema="decision_architect",
                target_table="published_content",
                node_id=str(row["id"]),
                metadata={"platform": platform},
            )
            published.append({"id": row["id"], "title": row["title"]})

        return f"Published {len(published)} item(s) to {platform}: " + json.dumps(published)
