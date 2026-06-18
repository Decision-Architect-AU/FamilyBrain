from crewai import Agent
from src.llm import get_llm
from src.tools.theme_query import ThemeQueryTool
from src.tools.published_content_query import PublishedContentQueryTool
from src.tools.property_deals_query import PropertyDealsQueryTool


def make_researcher() -> Agent:
    return Agent(
        role="Content Researcher",
        goal=(
            "Identify the single highest-priority theme that is under-covered or overdue for publication. "
            "Pull relevant deal data and prior content to brief the writer with concrete context."
        ),
        backstory=(
            "You are a strategic content researcher for a property investor's LinkedIn presence. "
            "You understand which topics build authority in the property and NDIS housing space. "
            "You never invent data — you surface real themes and real deals to ground every piece of content."
        ),
        tools=[ThemeQueryTool(), PublishedContentQueryTool(), PropertyDealsQueryTool()],
        llm=get_llm(),
        verbose=True,
        max_iter=4,
    )
