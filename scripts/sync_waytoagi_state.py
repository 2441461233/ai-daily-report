#!/usr/bin/env python3
"""Derive WayToAGI consumption state from committed attachment artifacts."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "content" / "artifacts"
STATE = ROOT / "content" / "waytoagi-consumed.txt"
NAME = re.compile(r"^waytoagi-(\d{8})\.json$")


def main() -> int:
    existing = set()
    if STATE.is_file():
        existing = {line.strip() for line in STATE.read_text("utf-8").splitlines() if line.strip()}
    attached = {
        match.group(1)
        for path in ARTIFACTS.glob("waytoagi-*.json")
        if (match := NAME.fullmatch(path.name))
    }
    merged = existing | attached
    rendered = "".join(f"{stamp}\n" for stamp in sorted(merged))
    if not STATE.is_file() or STATE.read_text("utf-8") != rendered:
        temp = STATE.with_suffix(".tmp")
        temp.write_text(rendered, "utf-8")
        temp.replace(STATE)
        print(f"synced WayToAGI state: {len(merged)} consumed date(s)")
    else:
        print(f"WayToAGI state already current: {len(merged)} consumed date(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
