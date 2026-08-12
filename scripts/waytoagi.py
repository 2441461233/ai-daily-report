#!/usr/bin/env python3
"""List or mark WayToAGI issues not yet consumed by the daily report.

The mirror commonly lags several days and returns HTTP 500 for an issue that is
not live yet. We therefore probe a rolling window and keep durable state inside
the repository at ``content/waytoagi-consumed.txt``.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "content" / "waytoagi-consumed.txt"
URL = "https://www.waytoagi.com/zh/blog/news-{}"
TIMEOUT = 15
SHANGHAI = ZoneInfo("Asia/Shanghai")


def consumed() -> set[str]:
    if not STATE.is_file():
        return set()
    return {line.strip() for line in STATE.read_text("utf-8").splitlines() if line.strip()}


def is_live(stamp: str) -> tuple[str, bool, str | None]:
    req = urllib.request.Request(
        URL.format(stamp),
        method="HEAD",
        headers={"User-Agent": "Mozilla/5.0 (compatible; ai-daily-report/1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return stamp, response.status == 200, None
    except urllib.error.HTTPError as exc:
        # WayToAGI uses 500 for dates that have not reached the mirror yet.
        if exc.code in {404, 500}:
            return stamp, False, None
        return stamp, False, f"HTTP {exc.code}"
    except Exception as exc:  # network failures are reported, never marked consumed
        return stamp, False, str(exc)


def mark(stamps: list[str]) -> int:
    bad = [stamp for stamp in stamps if len(stamp) != 8 or not stamp.isdigit()]
    if bad:
        print(f"invalid date stamp(s): {', '.join(bad)}", file=sys.stderr)
        return 2

    before = consumed()
    after = before | set(stamps)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_suffix(".tmp")
    temp.write_text("".join(f"{stamp}\n" for stamp in sorted(after)), "utf-8")
    temp.replace(STATE)
    print(f"marked {len(after - before)} new, {len(stamps) - len(after - before)} already recorded")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14, help="rolling window to probe")
    parser.add_argument("--mark", nargs="*", metavar="YYYYMMDD", help="record dates and exit")
    args = parser.parse_args()

    if args.mark is not None:
        if not args.mark:
            print("nothing to mark", file=sys.stderr)
            return 2
        return mark(args.mark)
    if args.days < 1 or args.days > 60:
        print("--days must be between 1 and 60", file=sys.stderr)
        return 2

    have = consumed()
    today = datetime.now(SHANGHAI).date()
    stamps = [
        (today - timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range(args.days)
        if (today - timedelta(days=offset)).strftime("%Y%m%d") not in have
    ]

    with ThreadPoolExecutor(max_workers=min(8, len(stamps) or 1)) as pool:
        results = list(pool.map(is_live, stamps))

    failures = [(stamp, error) for stamp, _live, error in results if error]
    for stamp, error in failures:
        print(f"# probe failed for {stamp}: {error}", file=sys.stderr)

    found = sorted(stamp for stamp, live, _error in results if live)
    for stamp in found:
        print(f"{stamp}\t{URL.format(stamp)}")
    if not found:
        print(f"# no unconsumed WayToAGI issue in the last {args.days} days", file=sys.stderr)

    # A total network failure must fail the run so it cannot be mistaken for
    # "nothing new". Partial failures remain visible but do not block other dates.
    return 1 if stamps and len(failures) == len(stamps) else 0


if __name__ == "__main__":
    raise SystemExit(main())
