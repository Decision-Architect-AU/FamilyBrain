"""CriticTool — approves or rejects a draft, writes feedback, optionally updates status."""
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from src.db import fetch_one, execute
from src import audit


class CriticInput(BaseModel):
    content_id: int = Field(description="ID of the published_content row to review")
    verdict: str = Field(description="'approve' or 'reject'")
    feedback: str = Field(description="Detailed feedback explaining the verdict")


class CriticTool(BaseTool):
    name: str = "critic_tool"
    description: str = (
        "Approve or reject a content draft. "
        "Approved drafts move to status='approved' and are queued for publishing. "
        "Rejected drafts are flagged with feedback for the writer to revise."
    )
    args_schema: type[BaseModel] = CriticInput

    def _run(self, content_id: int, verdict: str, feedback: str) -> str:
        if verdict not in ("approve", "reject"):
            return "Error: verdict must be 'approve' or 'reject'."

        row = fetch_one(
            "SELECT id, title, platform, status FROM decision_architect.published_content WHERE id = %s",
            (content_id,),
        )
        if not row:
            return f"Error: no content found with id={content_id}."
        if row["status"] != "draft":
            return f"Content id={content_id} is not a draft (status={row['status']}). Nothing changed."

        new_status = "approved" if verdict == "approve" else "draft"
        notes_col  = "notes" if verdict == "reject" else None

        if verdict == "approve":
            execute(
                """UPDATE decision_architect.published_content
                   SET status = 'approved', approved_at = now(), approved_by = 'pr_critic'
                   WHERE id = %s""",
                (content_id,),
            )
        else:
            # Store rejection feedback in metadata by updating the row's performance jsonb
            execute(
                """UPDATE decision_architect.published_content
                   SET performance = performance || %s::jsonb
                   WHERE id = %s""",
                ('{"critic_feedback": ' + f'"{feedback[:500]}"' + '}', content_id),
            )

        audit.log(
            agent="pr_critic",
            action_type="approve" if verdict == "approve" else "reject",
            summary=f"{'Approved' if verdict == 'approve' else 'Rejected'}: [{row['platform']}] {row['title'][:60]}",
            target_schema="decision_architect",
            target_table="published_content",
            node_id=str(content_id),
            metadata={"verdict": verdict, "feedback_preview": feedback[:200]},
        )

        if verdict == "approve":
            return f"Draft id={content_id} approved. Ready for scheduling."
        else:
            return f"Draft id={content_id} rejected. Feedback stored: {feedback[:200]}"
