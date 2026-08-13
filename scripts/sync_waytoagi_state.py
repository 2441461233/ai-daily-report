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
    attached = {
        match.group(1)
        for path in ARTIFACTS.glob("waytoagi-*.json")
        if (match := NAME.fullmatch(path.name))
    }
    rendered = "".join(f"{stamp}\n" for stamp in sorted(attached))
    if not STATE.is_file() or STATE.read_text("utf-8") != rendered:
        temp = STATE.with_suffix(".tmp")
        temp.write_text(rendered, "utf-8")
        temp.replace(STATE)
        print(f"synced WayToAGI state: {len(attached)} consumed date(s)")
    else:
        print(f"WayToAGI state already current: {len(attached)} consumed date(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
