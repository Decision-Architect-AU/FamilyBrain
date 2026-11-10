#!/usr/bin/env python3
"""Does claude-project/ still describe the system that exists?

Run after any migration or capability change:

    python scripts/check_project_knowledge.py

Exit code 1 if the documents claim something that is not there.

It checks only what it can check *unambiguously*. An earlier version tried to
infer column names from backticks in prose and produced 67 findings of which
roughly none were real — it could not tell a column from an enum value or a
JSON key, and a checker that cries wolf is worse than no checker, because it
teaches everyone to skip the output.

So the load-bearing columns are declared explicitly in the data model, in a
block this reads. Everything else verified here is unambiguous by its shape:
a schema-qualified table name, a value that exists in the database, a
recurrence the generator supports, a function in maintenance.py, a file path.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROJECT = ROOT / "claude-project"
DOCS = sorted(PROJECT.rglob("*.md")) + [ROOT / "CLAUDE.md"]

# Paths in the docs are written the way a reader would say them, so a
# reference may be relative to the repo or to the service it belongs to.
PATH_ROOTS = [ROOT, ROOT / "dashboard" / "src", ROOT / "wa-agent",
              ROOT / "email-sync", ROOT / "ingestor", ROOT / "dashboard"]


def psql(sql: str) -> set[str]:
    out = subprocess.run(
        ["docker", "exec", "familybrain-postgres",
         "psql", "-U", "geoff", "-d", "openclaw", "-t", "-A", "-c", sql],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        raise SystemExit(f"could not reach the database:\n{out.stderr.strip()}")
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def declared_columns(model: str) -> list[tuple[str, str]]:
    """Columns the data model explicitly asserts, from its own block.

    Written as `- personal.event.generated_title` under the heading
    "## Columns this document depends on".
    """
    block = re.search(r"^## Columns this document depends on\s*\n(.*?)(?=\n## |\Z)",
                      model, re.S | re.M)
    if not block:
        return []
    found = re.findall(r"^-\s+personal\.(\w+)\.(\w+)", block.group(1), re.M)
    return found


def main() -> int:
    text = {doc: doc.read_text(encoding="utf-8") for doc in DOCS if doc.exists()}
    all_text = "\n".join(text.values())
    problems: list[str] = []

    # 1. Schema-qualified tables the documents name.
    live_tables = psql(
        "SELECT table_schema || '.' || table_name FROM information_schema.tables "
        "WHERE table_schema IN ('personal','decision_os') "
        "UNION SELECT table_schema || '.' || table_name FROM information_schema.views "
        "WHERE table_schema IN ('personal','decision_os')"
    )
    for schema, table in sorted(set(re.findall(
            r"\b(personal|decision_os)\.([a-z][a-z_]{2,})\b", all_text))):
        qualified = f"{schema}.{table}"
        if qualified in live_tables:
            continue
        # personal.asset.facts and the like name a column, not a table.
        if any(t.startswith(qualified + ".") or
               f"{schema}.{table}" == t for t in live_tables):
            continue
        if re.search(rf"\b{schema}\.{table}\.\w+", all_text):
            continue
        where = ", ".join(sorted(d.name for d, t in text.items() if qualified in t))
        problems.append(f"table {qualified} is named in {where} but does not exist")

    # 2. The columns the data model says it depends on.
    model_path = PROJECT / "familybrain" / "01-data-model.md"
    live_cols = psql(
        "SELECT table_name || '.' || column_name FROM information_schema.columns "
        "WHERE table_schema = 'personal'"
    )
    declared = declared_columns(text.get(model_path, ""))
    if not declared:
        problems.append("01-data-model.md has no 'Columns this document depends on' "
                        "block, so nothing about its columns can be verified")
    for table, col in declared:
        if f"{table}.{col}" not in live_cols:
            problems.append(f"the data model depends on personal.{table}.{col}, "
                            f"which is not on that table")

    # 3. Values that exist in the database and are mentioned nowhere.
    for label, sql in {
        "asset_type": "SELECT DISTINCT asset_type FROM personal.asset",
        "event status": "SELECT DISTINCT status FROM personal.event",
    }.items():
        for value in sorted(psql(sql)):
            if not re.search(rf"\b{re.escape(value)}\b", all_text):
                problems.append(f"{label} '{value}' exists in the database and is "
                                f"mentioned nowhere in the docs")

    # 4. Recurrences the generator supports.
    maintenance = (ROOT / "wa-agent" / "src" / "maintenance.py").read_text(encoding="utf-8")
    for kind in sorted(set(re.findall(r'recurrence == "([a-z_]+)"', maintenance))):
        if not re.search(rf"\b{kind}\b", all_text):
            problems.append(f"recurrence '{kind}' is supported by the generator "
                            f"and is not documented")

    # 5. Maintenance functions, but only where the docs call them as functions
    #    or import them — task_status is a column and task_sync is a module.
    called = set(re.findall(r"\b(task_[a-z_]+)\s*\(", all_text))
    called |= set(re.findall(r"import\s+(task_[a-z_]+)", all_text))
    for task in sorted(called):
        if f"def {task}(" not in maintenance:
            problems.append(f"{task} is called in the docs but there is no such "
                            f"function in maintenance.py")

    # 6. Files the documents point at. A path is written the way a reader
    #    would say it, so it may be relative to the repo, to the service it
    #    belongs to, or — in a README pointing at its own siblings — to the
    #    directory the document itself is in.
    for doc, body in text.items():
        for path in sorted(set(re.findall(
                r"`([\w./-]+/[\w./-]+\.(?:py|ts|tsx|sql|md))`", body))):
            if "*" in path:
                continue
            roots = PATH_ROOTS + [doc.parent]
            if not any((root / path).exists() for root in roots):
                problems.append(f"{doc.name} points at {path}, which does not "
                                f"exist under the repo, a service directory, "
                                f"or beside the document")

    # 7. Every knowledge file is named in the README, which is the upload list.
    #    A file added here and not added there is silently never uploaded, so
    #    the project keeps answering from the knowledge it had before. Counting
    #    skills in prose does not catch that; naming each file does.
    readme = text.get(PROJECT / "README.md", "")
    for folder in ("familybrain", "commentos"):
        for doc in sorted((PROJECT / folder).glob("*.md")):
            if doc.name == "PROJECT-INSTRUCTIONS.md":
                continue
            if f"{folder}/{doc.name}" not in readme:
                problems.append(f"{folder}/{doc.name} is knowledge but the "
                                f"README does not list it, so it would never "
                                f"be uploaded to the project")

    if problems:
        print(f"{len(problems)} thing(s) the project knowledge claims are no "
              f"longer true:\n")
        for p in problems:
            print(f"  - {p}")
        print("\nUpdate claude-project/ in the same change that moved the system.")
        return 1

    print(f"project knowledge matches the system "
          f"({len(declared)} declared columns, {len(live_tables)} tables checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
