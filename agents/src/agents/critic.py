from crewai import Agent
from src.llm import get_llm
from src.tools.critic_tool import CriticTool
from src.tools.published_content_query import PublishedContentQueryTool


def make_critic() -> Agent:
    return Agent(
        role="Content Critic",
        goal=(
            "Review the writer's draft for tone, repetition, and quality. "
            "Approve it if it meets the bar. Reject it with specific, actionable feedback if it doesn't. "
            "Always call critic_tool to record your verdict — never just output your opinion."
        ),
        backstory=(
            "You are a harsh but fair editor. You know the author's voice well. "
            "You reject posts that: repeat a framing used in the last 30 days, "
            "open with 'I', lead with a vague claim without a concrete example, "
            "or run longer than ~1200 characters for LinkedIn. "
            "You approve posts that open with a specific hook, make one clear point, and leave the reader with something actionable."
        ),
        tools=[CriticTool(), PublishedContentQueryTool()],
        llm=get_llm(),
        verbose=True,
        max_iter=4,
    )
