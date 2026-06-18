from crewai import Task
from crewai import Agent


def research_task(researcher: Agent) -> Task:
    return Task(
        description=(
            "Search the decision_architect themes for the one that is most overdue for a LinkedIn post "
            "(highest priority + longest since last_published, or never published). "
            "Then query published_content_query to understand what angles have already been covered for that theme. "
            "Finally, check property_deals_query for any recent deals that could ground the content. "
            "Output a structured brief: chosen theme name and ID, key angle NOT yet covered, "
            "any deal data to reference (anonymise suburb to region if needed), and suggested post hook."
        ),
        expected_output=(
            "A content brief with: theme_id, theme_name, suggested_angle, deal_context (or 'none'), hook_suggestion."
        ),
        agent=researcher,
    )


def write_task(writer: Agent, research_task: Task) -> Task:
    return Task(
        description=(
            "Using the researcher's brief, write a LinkedIn post. "
            "Before writing, call published_content_query with the theme to confirm you're not repeating recent content. "
            "The post must: open with a specific hook (not 'I'), make one clear point using the suggested angle, "
            "reference the deal data if provided (anonymised), and close with an insight or question. "
            "Max ~1200 characters. "
            "When done, call content_writer to save the draft — include the theme_id from the brief."
        ),
        expected_output=(
            "Confirmation that the draft was saved (content_writer returns an id). Include the content_id in your output."
        ),
        agent=writer,
        context=[research_task],
    )


def critic_task(critic: Agent, write_task: Task) -> Task:
    return Task(
        description=(
            "Review the draft saved by the writer. The write_task output contains the content_id. "
            "Call published_content_query with the draft text to check for repetition against the last 30 days. "
            "Evaluate: Does it open with a hook? Is it under 1200 characters? Does it make one clear point? "
            "Is the tone direct and practical (not corporate/motivational)? "
            "Call critic_tool with your verdict ('approve' or 'reject') and detailed feedback."
        ),
        expected_output=(
            "Verdict (approve/reject) and the feedback provided to critic_tool."
        ),
        agent=critic,
        context=[write_task],
    )


def schedule_task(scheduler: Agent, critic_task: Task) -> Task:
    return Task(
        description=(
            "If the critic approved the draft, call scheduler_tool for platform='linkedin', max_publish=1. "
            "If the critic rejected it, report that the draft needs revision and skip publishing. "
            "Output a summary of what was published or why publishing was skipped."
        ),
        expected_output=(
            "Either: confirmation of what was published (platform, title, content_id), "
            "or: explanation that publishing was skipped due to rejection."
        ),
        agent=scheduler,
        context=[critic_task],
    )
