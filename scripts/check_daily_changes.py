#!/usr/bin/env python3
"""Guard the file changes made by the headless daily-report agent.

The agent may add an immutable daily main report, append sequential same-day
addenda, add or source-sync validated WayToAGI attachments, and update the three
derived/state files. Anything else fails before GitHub Actions can commit it.
"""

from __future__ import annotations

import argparse
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
ADDENDUM_KIND = "addendum"
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        help="frozen Shanghai report date (defaults to the current Shanghai date)",
    )
    return parser.parse_args(argv)


def resolve_report_date(value: str | None) -> str:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    parsed = datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    if parsed != value:
        raise ValueError("must be an exact YYYY-MM-DD value")
    return parsed


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    entries = status_entries()

    errors: list[str] = []
    changed = {path for _status, path in entries}
    changed_artifacts: list[Path] = []

    for status, path in entries:
        if " -> " in path or "R" in status or "C" in status:
            errors.append(f"renames/copies are not allowed: {status} {path}")
            continue
        if path.startswith("content/artifacts/") and path.endswith(".json"):
            relative = Path(path)
            if relative.parent.as_posix() != "content/artifacts":
                errors.append(f"artifact JSON must be directly under content/artifacts: {status} {path}")
                continue
            name = Path(path).name
            is_waytoagi = WAYTOAGI_NAME.fullmatch(name) is not None
            if status == "??" or (is_waytoagi and status.strip() == "M"):
                # Main reports and addenda remain immutable. WayToAGI mirrors
                # can publish additions after an issue first appears, so a
                # validated run may repair that issue in place. The dedicated
                # run validator constrains edits to the fetched structured input.
                changed_artifacts.append(ROOT / path)
            else:
                errors.append(f"existing report artifacts are immutable: {status} {path}")
            continue
        if path not in ALLOWED_FILES:
            errors.append(f"agent changed an out-of-scope file: {status} {path}")
        elif "D" in status:
            errors.append(f"state/derived file may not be deleted: {status} {path}")

    if entries and not changed_artifacts:
        errors.append("a non-clean run must add or update at least one artifact")
    new_artifact_paths = {
        path for status, path in entries
        if status == "??" and path.startswith("content/artifacts/") and path.endswith(".json")
    }
    if new_artifact_paths and "content/reported.md" not in changed:
        errors.append("new artifacts require a matching content/reported.md update")
    if changed_artifacts and "public/data/reports.json" not in changed:
        errors.append("artifact changes require a rebuilt public/data/reports.json")
    if "content/reported.md" in changed and (ROOT / "content/reported.md").is_file():
        try:
            committed_reported = subprocess.check_output(
                ["git", "show", "HEAD:content/reported.md"],
                cwd=ROOT,
                text=True,
            )
            current_reported = (ROOT / "content/reported.md").read_text("utf-8")
            if not current_reported.startswith(committed_reported):
                errors.append(
                    "content/reported.md is append-only; committed history must remain an exact prefix"
                )
        except Exception as exc:
            errors.append(f"cannot verify append-only content/reported.md: {exc}")

    try:
        today = resolve_report_date(arguments.date)
    except ValueError:
        return fail([f"--date must be an exact calendar date, got {arguments.date!r}"])
    new_daily: list[tuple[Path, int, str]] = []
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
            sequence = int(match.group(2))
            if declared != filename_date:
                errors.append(
                    f"{path.name} declares {declared!r}, expected {filename_date!r}"
                )
            if filename_date != today:
                errors.append(
                    f"daily report additions must use Shanghai date {today}, got {path.name}"
                )
            kind = str(artifact.get("kind") or "main")
            new_daily.append((path, sequence, kind))
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

    tracked_today_names = subprocess.check_output(
        ["git", "ls-files", f"content/artifacts/{today}-*.json"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    tracked_today: list[tuple[Path, int, str]] = []
    for relative in tracked_today_names:
        path = ROOT / relative
        match = MAIN_NAME.fullmatch(path.name)
        if match is None:
            continue
        try:
            # Read the immutable committed object, not a mutable worktree copy.
            # An attempted edit is already rejected above; using HEAD here also
            # prevents that edit from influencing sequence/kind decisions.
            artifact = json.loads(
                subprocess.check_output(
                    ["git", "show", f"HEAD:{relative}"],
                    cwd=ROOT,
                    text=True,
                )
            )
        except Exception as exc:
            errors.append(f"cannot read committed report {path.name}: {exc}")
            continue
        declared = str(artifact.get("date", "")).split()[0]
        if declared != today:
            errors.append(
                f"committed report {path.name} declares {declared!r}, expected {today!r}"
            )
        tracked_today.append(
            (
                path,
                int(match.group(2)),
                str(artifact.get("kind") or "main"),
            )
        )

    if tracked_today:
        tracked_sequences = sorted(sequence for _path, sequence, _kind in tracked_today)
        expected_tracked = list(range(1, max(tracked_sequences) + 1))
        if tracked_sequences != expected_tracked:
            errors.append(
                f"committed {today} report sequences must be contiguous from 1: "
                f"found {tracked_sequences}"
            )
        for path, sequence, kind in tracked_today:
            if sequence == 1 and kind != "main":
                errors.append(f"committed first report must be a main report: {path.name}")
            if sequence > 1 and kind != ADDENDUM_KIND:
                errors.append(
                    f"committed same-day report {path.name} must declare kind 'addendum'"
                )

        if new_daily:
            new_sequences = sorted(sequence for _path, sequence, _kind in new_daily)
            expected_new = list(
                range(max(tracked_sequences) + 1, max(tracked_sequences) + 1 + len(new_daily))
            )
            if new_sequences != expected_new:
                errors.append(
                    f"new addendum sequence must continue after {max(tracked_sequences)} "
                    f"without duplicates or gaps; found {new_sequences}"
                )
            for path, _sequence, kind in new_daily:
                if kind != ADDENDUM_KIND:
                    errors.append(
                        f"a committed main report already exists for {today}; "
                        f"{path.name} must declare kind 'addendum'"
                    )
    else:
        new_daily.sort(key=lambda record: record[1])
        new_sequences = [sequence for _path, sequence, _kind in new_daily]
        if not new_daily or new_sequences != list(range(1, len(new_daily) + 1)):
            errors.append(
                f"run must create a contiguous Shanghai-date report sequence starting at "
                f"{today}-1.json; found {new_sequences}"
            )
        else:
            first_path, _first_sequence, first_kind = new_daily[0]
            if first_kind == ADDENDUM_KIND:
                errors.append(f"{first_path.name} must be a main report, not an addendum")
            for path, sequence, kind in new_daily[1:]:
                if kind != ADDENDUM_KIND:
                    errors.append(
                        f"same-day report {path.name} at sequence {sequence} must declare "
                        "kind 'addendum'"
                    )

    if errors:
        return fail(errors)
    if not entries:
        print("daily change guard: clean tree with committed report (idempotent no-op)")
        return 0
    print(
        f"daily change guard passed: {len(changed_artifacts)} changed artifact(s), "
        f"{len(entries)} changed file(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
