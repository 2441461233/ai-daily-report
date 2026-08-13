#!/usr/bin/env python3
"""Guard the file changes made by the headless daily-report agent.

The agent may add immutable main reports, add or source-sync validated WayToAGI
attachments, and update the three derived/state files. Anything else fails the
run before GitHub Actions can commit it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
MAIN_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d+)\.json$")
WAYTOAGI_NAME = re.compile(r"^waytoagi-(\d{8})\.json$")
ALLOWED_FILES = {
    "content/reported.md",
    "content/waytoagi-consumed.txt",
    "public/data/reports.json",
}


def status_entries() -> list[tuple[str, str]]:
    output = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
        text=True,
    )
    entries = []
    for raw in output.splitlines():
        if not raw:
            continue
        status, path = raw[:2], raw[3:]
        entries.append((status, path))
    return entries


def fail(messages: list[str]) -> int:
    print("daily change guard failed:", file=sys.stderr)
    for message in messages:
        print(f"  - {message}", file=sys.stderr)
    return 1


def main() -> int:
    entries = status_entries()
    if not entries:
        print("daily change guard: clean tree (idempotent no-op)")
        return 0

    errors: list[str] = []
    changed = {path for _status, path in entries}
    changed_artifacts: list[Path] = []

    for status, path in entries:
        if " -> " in path or "R" in status or "C" in status:
            errors.append(f"renames/copies are not allowed: {status} {path}")
            continue
        if path.startswith("content/artifacts/") and path.endswith(".json"):
            name = Path(path).name
            is_waytoagi = WAYTOAGI_NAME.fullmatch(name) is not None
            if status == "??" or (is_waytoagi and status.strip() == "M"):
                # Main reports remain immutable. WayToAGI mirrors can publish
                # additions after an issue first appears, so a validated run may
                # repair that issue in place. validate_waytoagi_run.py constrains
                # these edits to the exact structured input fetched this run.
                changed_artifacts.append(ROOT / path)
            else:
                errors.append(f"existing main artifacts are immutable: {status} {path}")
            continue
        if path not in ALLOWED_FILES:
            errors.append(f"agent changed an out-of-scope file: {status} {path}")
        elif "D" in status:
            errors.append(f"state/derived file may not be deleted: {status} {path}")

    if not changed_artifacts:
        errors.append("a non-clean run must add or update at least one artifact")
    new_artifact_paths = {
        path for status, path in entries
        if status == "??" and path.startswith("content/artifacts/") and path.endswith(".json")
    }
    if new_artifact_paths and "content/reported.md" not in changed:
        errors.append("new artifacts require a matching content/reported.md update")
    if changed_artifacts and "public/data/reports.json" not in changed:
        errors.append("artifact changes require a rebuilt public/data/reports.json")

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    main_for_today = False
    for path in changed_artifacts:
        match = MAIN_NAME.fullmatch(path.name)
        try:
            artifact = json.loads(path.read_text("utf-8"))
        except Exception as exc:
            errors.append(f"cannot read changed artifact {path.name}: {exc}")
            continue
        declared = str(artifact.get("date", "")).split()[0]
        if match:
            filename_date = match.group(1)
            if declared != filename_date:
                errors.append(
                    f"{path.name} declares {declared!r}, expected {filename_date!r}"
                )
            if filename_date == today:
                main_for_today = True
        else:
            attachment_match = WAYTOAGI_NAME.fullmatch(path.name)
            if not attachment_match:
                errors.append(f"unexpected artifact filename: {path.name}")
                continue
            stamp_date = datetime.strptime(
                attachment_match.group(1), "%Y%m%d"
            ).date().isoformat()
            attach_to = artifact.get("attachTo")
            if declared != stamp_date or attach_to != stamp_date:
                errors.append(
                    f"{path.name} must declare date and attachTo as {stamp_date!r}"
                )

    tracked_today = subprocess.check_output(
        ["git", "ls-files", f"content/artifacts/{today}-*.json"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    existing_today = bool(tracked_today)
    if existing_today and main_for_today:
        errors.append(f"a committed main report already exists for {today}; retry must be idempotent")
    if not existing_today and not main_for_today:
        errors.append(f"run did not create the required Shanghai-date main report for {today}")

    if errors:
        return fail(errors)
    print(
        f"daily change guard passed: {len(changed_artifacts)} changed artifact(s), "
        f"{len(entries)} changed file(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
