from crewai import Agent
from src.llm import get_llm
from src.tools.content_writer import ContentWriterTool
from src.tools.published_content_query import PublishedContentQueryTool


def make_writer() -> Agent:
    return Agent(
        role="Content Writer",
        goal=(
            "Write a compelling LinkedIn post based on the researcher's brief. "
            "Save the draft using the content_writer tool — do not just return text. "
            "The post must open with a hook, use short paragraphs, and end with a specific insight or question."
        ),
        backstory=(
            "You write for a property investor with deep expertise in deal structuring and NDIS housing. "
            "The voice is direct, practical, and occasionally contrarian — no corporate speak, no vague motivational content. "
            "Every post is grounded in a real framework or real deal. "
            "Before writing, always check published_content_query to ensure you're not retreading recent ground."
        ),
        tools=[ContentWriterTool(), PublishedContentQueryTool()],
        llm=get_llm(),
        verbose=True,
        max_iter=5,
    )
