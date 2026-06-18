from crewai import Agent
from src.llm import get_llm
from src.tools.scheduler_tool import SchedulerTool


def make_scheduler() -> Agent:
    return Agent(
        role="Content Scheduler",
        goal=(
            "Check for approved content and mark it as published. "
            "Publish at most one LinkedIn post per run. "
            "Report what was published or confirm the queue is empty."
        ),
        backstory=(
            "You manage the content calendar. You ensure posts go out at the right cadence — "
            "never more than one per day per platform. You call scheduler_tool to do the actual publish step."
        ),
        tools=[SchedulerTool()],
        llm=get_llm(),
        verbose=True,
        max_iter=2,
    )
