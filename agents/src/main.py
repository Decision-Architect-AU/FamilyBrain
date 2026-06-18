"""
PR Agent scheduler — runs the PR crew on a configurable cron.
Default: daily at 07:00.
"""
import os
import schedule
import time
from src.crews.pr_crew import run_pr_crew

PR_CRON_TIME = os.environ.get("PR_CRON_TIME", "07:00")   # HH:MM daily
RUN_ON_START = os.environ.get("PR_RUN_ON_START", "false").lower() == "true"

print(f"[pr-agents] Scheduled at {PR_CRON_TIME} daily. RUN_ON_START={RUN_ON_START}")

if RUN_ON_START:
    print("[pr-agents] Running crew immediately (PR_RUN_ON_START=true)")
    result = run_pr_crew()
    print(f"[pr-agents] Startup run: {result['status']}")

schedule.every().day.at(PR_CRON_TIME).do(lambda: (
    print("[pr-agents] Starting scheduled PR crew run"),
    run_pr_crew(),
))

while True:
    schedule.run_pending()
    time.sleep(30)
