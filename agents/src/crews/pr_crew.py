from crewai import Crew, Process
from src.agents.researcher import make_researcher
from src.agents.writer import make_writer
from src.agents.critic import make_critic
from src.agents.scheduler import make_scheduler
from src.tasks.pr_tasks import research_task, write_task, critic_task, schedule_task
from src import audit
import traceback


def run_pr_crew() -> dict:
    """Run one full PR content cycle. Returns a summary dict."""
    audit.log(
        agent="pr_crew",
        action_type="write",
        summary="PR crew run started",
        target_schema="decision_architect",
    )

    researcher = make_researcher()
    writer     = make_writer()
    critic     = make_critic()
    scheduler  = make_scheduler()

    t_research = research_task(researcher)
    t_write    = write_task(writer, t_research)
    t_critic   = critic_task(critic, t_write)
    t_schedule = schedule_task(scheduler, t_critic)

    crew = Crew(
        agents=[researcher, writer, critic, scheduler],
        tasks=[t_research, t_write, t_critic, t_schedule],
        process=Process.sequential,
        verbose=True,
        memory=False,  # We manage embeddings directly — disable CrewAI's internal memory
    )

    try:
        result = crew.kickoff()
        audit.log(
            agent="pr_crew",
            action_type="write",
            summary="PR crew run completed",
            target_schema="decision_architect",
            metadata={"result_preview": str(result)[:300]},
        )
        return {"status": "ok", "result": str(result)}
    except Exception as e:
        tb = traceback.format_exc()
        audit.log(
            agent="pr_crew",
            action_type="write",
            summary=f"PR crew run failed: {e}",
            target_schema="decision_architect",
            metadata={"traceback": tb[:500]},
        )
        return {"status": "error", "error": str(e)}
