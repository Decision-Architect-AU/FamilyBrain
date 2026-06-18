"""ContentWriterTool — saves a draft to published_content with status='draft'."""
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from src.db import fetch_one, execute
from src.llm import embed
from src import audit


class ContentWriterInput(BaseModel):
    title: str = Field(description="Post title or headline")
    body: str = Field(description="Full post body text")
    platform: str = Field(description="linkedin | podcast | newsletter | twitter")
    content_type: str = Field(description="post | episode | thread | article")
    theme_id: int = Field(description="ID of the primary theme this content addresses")
    framework_id: int = Field(default=0, description="ID of the framework used, or 0 if none")


class ContentWriterTool(BaseTool):
    name: str = "content_writer"
    description: str = (
        "Save a content draft to the database with status='draft'. "
        "Always call this after writing a post — do NOT just return the text."
    )
    args_schema: type[BaseModel] = ContentWriterInput

    def _run(
        self,
        title: str,
        body: str,
        platform: str,
        content_type: str,
        theme_id: int,
        framework_id: int = 0,
    ) -> str:
        embedding = embed(f"{title}\n\n{body}")
        vec_str = "[" + ",".join(str(v) for v in embedding) + "]"

        framework_id_val = framework_id if framework_id > 0 else None

        row = fetch_one(
            """
            INSERT INTO decision_architect.published_content
                (title, body, platform, content_type, theme_id, framework_id, status, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, 'draft', %s::vector)
            RETURNING id
            """,
            (title, body, platform, content_type, theme_id, framework_id_val, vec_str),
        )

        content_id = row["id"]
        audit.log(
            agent="pr_writer",
            action_type="write",
            summary=f"Draft saved: [{platform}] {title[:60]}",
            target_schema="decision_architect",
            target_table="published_content",
            node_id=str(content_id),
            metadata={"theme_id": theme_id, "platform": platform},
        )
        return f"Draft saved with id={content_id}. Status: draft (pending critic review)."
