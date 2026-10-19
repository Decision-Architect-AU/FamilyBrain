"""
Google Tasks API integration (Increment 5).

Mirrors gmail.py's per-provider-API-module shape — reuses its
_cached_service()/_creds() machinery directly rather than duplicating OAuth
plumbing a third time (Gmail, Calendar, now Tasks all go through the same
account credentials, same googleapiclient.discovery.build() pattern).

All tasks live in a single dedicated "FamilyBrain" task list, not the
account's default list — same separation-by-purpose convention already used
for calendar (Bills/Family/Holidays are their own calendars, not dumped into
primary).
"""
from .gmail import _cached_service

TASK_LIST_TITLE = "FamilyBrain"

_list_id_cache: dict[int, str] = {}   # account_id -> tasklist id


def _tasks_service(account: dict):
    return _cached_service(account, "tasks", "v1")


def _ensure_task_list(account: dict, svc) -> str:
    """Find-or-create the dedicated FamilyBrain task list. Cached per account
    for the process lifetime — mirrors gmail.py's _label_cache idiom."""
    account_id = account["id"]
    if account_id in _list_id_cache:
        return _list_id_cache[account_id]

    resp = svc.tasklists().list(maxResults=100).execute()
    for tl in resp.get("items", []):
        if tl.get("title") == TASK_LIST_TITLE:
            _list_id_cache[account_id] = tl["id"]
            return tl["id"]

    created = svc.tasklists().insert(body={"title": TASK_LIST_TITLE}).execute()
    _list_id_cache[account_id] = created["id"]
    print(f"[google_tasks] Created task list '{TASK_LIST_TITLE}' ({created['id']}) for {account['email_address']}")
    return created["id"]


def push_task(account: dict, title: str, notes: str = "", due_date=None,
              task_status: str = "open", existing_task_id: str | None = None) -> tuple[str, str]:
    """
    Create a new Google Task, or patch an existing one (existing_task_id
    given) with the same fields. Returns (list_id, task_id).
    due_date, if given, is a date — Google Tasks' `due` field is a full
    RFC3339 timestamp but only the date portion is ever shown/used by the
    API; midnight UTC is the conventional way every Google Tasks client
    writes a date-only due date.
    """
    svc = _tasks_service(account)
    list_id = _ensure_task_list(account, svc)

    body: dict = {"title": title, "notes": notes or ""}
    if due_date:
        body["due"] = f"{due_date.isoformat()}T00:00:00.000Z"
    body["status"] = "completed" if task_status == "done" else "needsAction"

    if existing_task_id:
        result = svc.tasks().patch(tasklist=list_id, task=existing_task_id, body=body).execute()
    else:
        result = svc.tasks().insert(tasklist=list_id, body=body).execute()
    return list_id, result["id"]


def pull_tasks(account: dict) -> list[dict]:
    """
    List every (non-deleted) task in the FamilyBrain list, including
    completed ones (showCompleted — the sync loop needs to see completions,
    not just open items). Returns raw Google Tasks resources.
    """
    svc = _tasks_service(account)
    list_id = _ensure_task_list(account, svc)

    items: list[dict] = []
    page_token = None
    while True:
        resp = svc.tasks().list(
            tasklist=list_id, showCompleted=True, showHidden=True,
            maxResults=100, pageToken=page_token,
        ).execute()
        items.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items
