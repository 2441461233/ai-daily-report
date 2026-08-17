#!/usr/bin/env python3
"""Persist the trusted Artificial Analysis current snapshot after validation."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = Path("/tmp/artificial-analysis.json")
DEFAULT_OUTPUT = ROOT / "content" / "artificial-analysis-snapshot.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def sync(input_path: Path, output_path: Path) -> bool:
    document = load_object(input_path)
    snapshot = document.get("currentSnapshot")
    if not isinstance(snapshot, dict) or snapshot.get("schemaVersion") != 1:
        raise ValueError("currentSnapshot must be a schemaVersion 1 JSON object")

    rendered = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    existing = output_path.read_text(encoding="utf-8") if output_path.is_file() else None
    if existing == rendered:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", dir=output_path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output_path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return True


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        changed = sync(arguments.input, arguments.output)
    except ValueError as exc:
        print(f"Artificial Analysis state sync failed: {exc}")
        return 1
    status = "updated" if changed else "already current"
    print(f"Artificial Analysis snapshot {status}: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
