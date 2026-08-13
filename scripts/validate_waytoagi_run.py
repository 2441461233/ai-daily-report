#!/usr/bin/env python3
"""Verify that every WayToAGI issue discovered for this run was archived whole."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "content" / "artifacts"
INPUT_DEFAULT = Path("/tmp/waytoagi.json")
MANIFEST_DEFAULT = Path("/tmp/waytoagi.changed")
STAMP_RE = re.compile(r"^\d{8}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WIKI_PREFIX = "https://waytoagi.feishu.cn/wiki/"
WIKI_URL_RE = re.compile(r"^https://waytoagi\.feishu\.cn/wiki/[A-Za-z0-9]+$")
ROLLING_LOG_URL = "https://waytoagi.feishu.cn/wiki/QPe5w5g7UisbEkkow8XcDmOpn8e"


@dataclass
class Failure:
    location: str
    message: str


def load_object(path: Path, failures: list[Failure]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        failures.append(Failure(str(path), "input file does not exist"))
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        failures.append(Failure(str(path), f"cannot read JSON: {exc}"))
        return None
    if not isinstance(value, dict):
        failures.append(Failure(str(path), "JSON document must be an object"))
        return None
    return value


def valid_date(value: Any) -> bool:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def input_wiki_urls(
    issue: dict[str, Any], location: str, failures: list[Failure]
) -> list[str]:
    items = issue.get("items")
    expected_count = issue.get("sourceItemCount")
    if not isinstance(items, list):
        failures.append(Failure(f"{location}.items", "must be an array"))
        return []
    if not isinstance(expected_count, int) or isinstance(expected_count, bool) or expected_count < 1:
        failures.append(Failure(f"{location}.sourceItemCount", "must be a positive integer"))
    elif expected_count != len(items):
        failures.append(
            Failure(
                f"{location}.sourceItemCount",
                f"declares {expected_count}, but input contains {len(items)} item(s)",
            )
        )

    urls: list[str] = []
    for index, item in enumerate(items):
        item_location = f"{location}.items[{index}]"
        if not isinstance(item, dict):
            failures.append(Failure(item_location, "must be an object"))
            continue
        for field in ("title", "summary"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                failures.append(Failure(f"{item_location}.{field}", "must be a non-empty string"))
        url = item.get("url")
        if not isinstance(url, str) or WIKI_URL_RE.fullmatch(url) is None:
            failures.append(
                Failure(f"{item_location}.url", "must be a WayToAGI Feishu wiki URL")
            )
            continue
        if url == ROLLING_LOG_URL:
            failures.append(
                Failure(f"{item_location}.url", "rolling seven-day log is not an item URL")
            )
            continue
        urls.append(url)
    return urls


def artifact_contract(
    path: Path, stamp: str, date: str, failures: list[Failure]
) -> tuple[int, list[str], set[str], list[tuple[str, str, str]]]:
    artifact = load_object(path, failures)
    if artifact is None:
        return 0, [], set(), []
    location = path.relative_to(ROOT).as_posix()
    if artifact.get("date") != date:
        failures.append(Failure(f"{location}:$.date", f"must be exactly {date!r}"))
    if artifact.get("attachTo") != date:
        failures.append(Failure(f"{location}:$.attachTo", f"must be exactly {date!r}"))
    if artifact.get("label") != "WayToAGI 精选":
        failures.append(Failure(f"{location}:$.label", "must be exactly 'WayToAGI 精选'"))

    sections = artifact.get("sections")
    if not isinstance(sections, list) or len(sections) != 1:
        failures.append(Failure(f"{location}:$.sections", "must contain exactly one section"))
        return 0, [], set(), []
    section = sections[0]
    if not isinstance(section, dict):
        failures.append(Failure(f"{location}:$.sections[0]", "must be an object"))
        return 0, [], set(), []
    if section.get("title") != "🧭 WayToAGI 知识库精选":
        failures.append(
            Failure(
                f"{location}:$.sections[0].title",
                "must be exactly '🧭 WayToAGI 知识库精选'",
            )
        )
    items = section.get("items")
    if not isinstance(items, list):
        failures.append(Failure(f"{location}:$.sections[0].items", "must be an array"))
        return 0, [], set(), []

    mirrors: set[str] = set()
    wiki_urls: list[str] = []
    records: list[tuple[str, str, str]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            failures.append(
                Failure(f"{location}:$.sections[0].items[{index}]", "must be an object")
            )
            continue
        sources = item.get("sources")
        if not isinstance(sources, list):
            failures.append(
                Failure(
                    f"{location}:$.sections[0].items[{index}].sources",
                    "must be an array",
                )
            )
            continue
        urls = {
            source.get("url")
            for source in sources
            if isinstance(source, dict) and isinstance(source.get("url"), str)
        }
        mirrors.update(url for url in urls if "www.waytoagi.com/zh/blog/news-" in url)
        wiki = [url for url in urls if url.startswith(WIKI_PREFIX)]
        if ROLLING_LOG_URL in wiki:
            failures.append(
                Failure(
                    f"{location}:$.sections[0].items[{index}].sources",
                    "rolling seven-day log cannot stand in for an item URL",
                )
            )
        specific = [
            url
            for url in wiki
            if WIKI_URL_RE.fullmatch(url) is not None and url != ROLLING_LOG_URL
        ]
        if len(specific) != 1:
            failures.append(
                Failure(
                    f"{location}:$.sections[0].items[{index}].sources",
                    "must contain exactly one item-specific WayToAGI Feishu wiki URL",
                )
            )
            continue
        wiki_url = specific[0]
        wiki_urls.append(wiki_url)
        headline = item.get("headline")
        summary = item.get("summary")
        if isinstance(headline, str) and isinstance(summary, str):
            records.append((headline, summary, wiki_url))
    return len(items), wiki_urls, mirrors, records


def validate_run(
    input_path: Path, changed_manifest: Path = MANIFEST_DEFAULT
) -> list[Failure]:
    failures: list[Failure] = []
    document = load_object(input_path, failures)
    if document is None:
        return failures
    if document.get("schemaVersion") != 1:
        failures.append(Failure("$.schemaVersion", "must be 1"))
    if document.get("sourceIndex") != "https://www.waytoagi.com/zh/blog":
        failures.append(
            Failure(
                "$.sourceIndex",
                "must be exactly 'https://www.waytoagi.com/zh/blog'",
            )
        )
    if not isinstance(document.get("generatedAt"), str) or not document["generatedAt"].strip():
        failures.append(Failure("$.generatedAt", "must be a non-empty ISO-8601 timestamp"))
    else:
        try:
            generated_at = datetime.fromisoformat(document["generatedAt"])
        except ValueError:
            failures.append(Failure("$.generatedAt", "must be a valid ISO-8601 timestamp"))
        else:
            if generated_at.utcoffset() is None or not document["generatedAt"].endswith("+08:00"):
                failures.append(Failure("$.generatedAt", "must use an explicit +08:00 offset"))

    issues = document.get("issues")
    if not isinstance(issues, list):
        failures.append(Failure("$.issues", "must be an array"))
        return failures

    seen_stamps: set[str] = set()
    for index, issue in enumerate(issues):
        location = f"$.issues[{index}]"
        if not isinstance(issue, dict):
            failures.append(Failure(location, "must be an object"))
            continue
        stamp = issue.get("stamp")
        date = issue.get("date")
        source_url = issue.get("sourceUrl")
        if not isinstance(stamp, str) or STAMP_RE.fullmatch(stamp) is None:
            failures.append(Failure(f"{location}.stamp", "must be YYYYMMDD"))
            continue
        if stamp in seen_stamps:
            failures.append(Failure(f"{location}.stamp", f"duplicate issue stamp {stamp}"))
        seen_stamps.add(stamp)
        if not valid_date(date) or date.replace("-", "") != stamp:
            failures.append(Failure(f"{location}.date", "must match stamp as YYYY-MM-DD"))
            continue
        expected_source_url = f"https://www.waytoagi.com/zh/blog/news-{stamp}"
        if source_url != expected_source_url:
            failures.append(
                Failure(f"{location}.sourceUrl", f"must be exactly {expected_source_url!r}")
            )
        expected_urls = input_wiki_urls(issue, location, failures)
        input_items = issue.get("items")
        expected_records = [
            (item["title"], item["summary"], item["url"])
            for item in input_items
            if isinstance(item, dict)
            and isinstance(item.get("title"), str)
            and isinstance(item.get("summary"), str)
            and isinstance(item.get("url"), str)
        ] if isinstance(input_items, list) else []

        artifact_path = ARTIFACTS / f"waytoagi-{stamp}.json"
        if not artifact_path.is_file():
            failures.append(
                Failure(
                    str(artifact_path.relative_to(ROOT)),
                    f"missing artifact for input issue {stamp}",
                )
            )
            continue
        actual_count, actual_urls, mirror_urls, actual_records = artifact_contract(
            artifact_path, stamp, date, failures
        )
        expected_count = issue.get("sourceItemCount")
        if actual_count != expected_count:
            failures.append(
                Failure(
                    str(artifact_path.relative_to(ROOT)),
                    f"contains {actual_count} item(s), expected {expected_count}",
                )
            )
        if actual_urls != expected_urls:
            missing = sorted(set(expected_urls) - set(actual_urls))
            extra = sorted(set(actual_urls) - set(expected_urls))
            failures.append(
                Failure(
                    str(artifact_path.relative_to(ROOT)),
                    "Feishu URL sequence mismatch; "
                    f"missing={missing}, extra={extra}, order_must_match_source=true",
                )
            )
        if actual_records != expected_records:
            failures.append(
                Failure(
                    str(artifact_path.relative_to(ROOT)),
                    "headline, summary, and item URL sequence must exactly match "
                    "the deterministic source input",
                )
            )
        if mirror_urls != {source_url}:
            failures.append(
                Failure(
                    str(artifact_path.relative_to(ROOT)),
                    f"mirror URL set must be exactly {[source_url]!r}, found {sorted(mirror_urls)!r}",
                )
            )

    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=ROOT,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        failures.append(Failure("git status", f"failed with exit code {exc.returncode}"))
        return failures
    changed_stamps: set[str] = set()
    for raw in status.splitlines():
        if not raw:
            continue
        path_text = raw[3:]
        if " -> " in path_text:
            path_text = path_text.rsplit(" -> ", 1)[1]
        match = re.fullmatch(r"content/artifacts/waytoagi-(\d{8})\.json", path_text)
        if match is not None:
            changed_stamps.add(match.group(1))
    unexpected = sorted(changed_stamps - seen_stamps)
    if unexpected:
        failures.append(
            Failure(
                "git status",
                f"changed WayToAGI artifact stamp(s) were not present in run input: {unexpected}",
            )
        )
    expected_changed: set[str] = set()
    try:
        expected_changed = {
            line.strip()
            for line in changed_manifest.read_text("utf-8").splitlines()
            if line.strip()
        }
    except FileNotFoundError:
        if changed_stamps:
            failures.append(
                Failure(
                    str(changed_manifest),
                    "changed manifest is required when WayToAGI artifacts changed",
                )
            )
    except (OSError, UnicodeError) as exc:
        failures.append(Failure(str(changed_manifest), f"cannot read changed manifest: {exc}"))
    actual_changed = {
        f"content/artifacts/waytoagi-{stamp}.json" for stamp in changed_stamps
    }
    if actual_changed != expected_changed:
        failures.append(
            Failure(
                "git status",
                "WayToAGI changed-file set differs from the immutable pre-Agent manifest; "
                f"expected={sorted(expected_changed)}, actual={sorted(actual_changed)}",
            )
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=INPUT_DEFAULT)
    parser.add_argument("--changed-manifest", type=Path, default=MANIFEST_DEFAULT)
    args = parser.parse_args()
    failures = validate_run(args.input, args.changed_manifest)
    if failures:
        print(f"WayToAGI run validation failed with {len(failures)} error(s):", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure.location}: {failure.message}", file=sys.stderr)
        return 1
    print(f"WayToAGI run validation passed: {args.input}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
