#!/usr/bin/env python3
"""Select the quality or deadline-safe route for a daily report run."""

from __future__ import annotations

import argparse
from datetime import datetime, time
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
QUALITY_CUTOFF = time(9, 30)
DEADLINE_SCHEDULES = {
    "47 0 * * *",  # 08:47 Asia/Shanghai
    "17 1 * * *",  # 09:17 Asia/Shanghai
    "17 2 * * *",  # 10:17 freshness/addendum check
}


def parse_instant(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    instant = datetime.fromisoformat(normalized)
    if instant.tzinfo is None:
        raise ValueError("--now must include a timezone")
    return instant


def select_route(
    *,
    now: datetime,
    event_name: str,
    requested_route: str,
    event_schedule: str,
) -> str:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if requested_route not in {"quality", "deadline"}:
        raise ValueError("requested_route must be quality or deadline")
    if requested_route == "deadline":
        return "deadline"
    if event_name != "schedule":
        return "quality"

    shanghai_now = now.astimezone(SHANGHAI)
    if event_schedule in DEADLINE_SCHEDULES:
        return "deadline"
    if shanghai_now.time().replace(tzinfo=None) >= QUALITY_CUTOFF:
        return "deadline"
    return "quality"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--requested-route", choices=("quality", "deadline"), required=True)
    parser.add_argument("--event-schedule", default="")
    arguments = parser.parse_args()
    print(
        select_route(
            now=parse_instant(arguments.now),
            event_name=arguments.event_name,
            requested_route=arguments.requested_route,
            event_schedule=arguments.event_schedule,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
