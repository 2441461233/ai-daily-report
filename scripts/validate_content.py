#!/usr/bin/env python3
"""Validate the checked-in inputs used to build the AI daily report site.

The content directory intentionally contains two artifact formats:

* current artifacts, where the artifact object is the JSON document itself;
* legacy Kimi run exports, where the artifact lives at ``run.artifact``.

This validator is deliberately network-free.  A "real" source URL therefore
means a syntactically valid HTTP(S) URL with a non-placeholder public-looking
host, not that the remote server happened to answer during CI.
"""

from __future__ import annotations

import ipaddress
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit


APP_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = APP_ROOT / "content" / "artifacts"
REPORTED_FILE = APP_ROOT / "content" / "reported.md"

DATE_FIELD_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})(?:\s+(?:星期|周)(?P<weekday>[一二三四五六日天]))?$"
)
DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REPORTED_HEADING_RE = re.compile(
    r"^##\s+(?P<date>\d{4}-\d{2}-\d{2})[（(](?P<label>.+?)[）)]\s*$"
)
REPORTED_ITEM_RE = re.compile(
    r"^-\s+(?P<date>\d{4}-\d{2}-\d{2})\s*\|\s*(?P<text>.+?)\s*$"
)
LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.+-]{2,}")

WEEKDAYS = "一二三四五六日"
PLACEHOLDER_HOSTS = {"example.com", "example.net", "example.org", "localhost"}
PLACEHOLDER_SUFFIXES = (".example", ".invalid", ".localhost", ".test")
MAIN_FILENAME_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<sequence>[1-9]\d*)\.json$")
WAYTOAGI_FILENAME_RE = re.compile(r"^waytoagi-(?P<stamp>\d{8})\.json$")
WAYTOAGI_LABEL = "WayToAGI 精选"
WAYTOAGI_SECTION_TITLE = "🧭 WayToAGI 知识库精选"
WAYTOAGI_MIRROR_TEMPLATE = "https://www.waytoagi.com/zh/blog/news-{}"
WAYTOAGI_WIKI_PREFIX = "https://waytoagi.feishu.cn/wiki/"
WAYTOAGI_WIKI_URL_RE = re.compile(
    r"^https://waytoagi\.feishu\.cn/wiki/[A-Za-z0-9]+$"
)
WAYTOAGI_ROLLING_LOG_URL = (
    "https://waytoagi.feishu.cn/wiki/QPe5w5g7UisbEkkow8XcDmOpn8e"
)
MAIN_SECTION_POLICY = (
    ("🔥 AI 重要事件", 3, 5),
    ("🎬 AI 创作 · 视频/音乐/媒体娱乐", 3, 4),
    ("🌍 海外观察", 3, 4),
    ("📄 论文与技术前沿", 2, 3),
    ("💻 GitHub Trending", 4, 6),
    ("🚀 AI 一人公司（OPC）", 2, 3),
)


class Errors:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def add(self, file: Path, location: str, message: str) -> None:
        try:
            display = file.relative_to(APP_ROOT)
        except ValueError:
            display = file
        self.messages.append(f"{display}:{location}: {message}")


@dataclass
class ReportedIssue:
    date: str
    label: str
    items: list[str]
    line: int


@dataclass
class Stats:
    artifact_files: int = 0
    legacy_files: int = 0
    main_artifacts: int = 0
    attachments: int = 0
    sections: int = 0
    artifact_items: int = 0
    reported_issues: int = 0
    reported_items: int = 0


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def calendar_date(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def validate_artifact_date(
    value: Any, file: Path, location: str, errors: Errors
) -> Optional[str]:
    if not isinstance(value, str):
        errors.add(file, location, "must be a string in YYYY-MM-DD format")
        return None
    match = DATE_FIELD_RE.fullmatch(value.strip())
    if match is None:
        errors.add(
            file,
            location,
            "must be YYYY-MM-DD, optionally followed by a Chinese weekday",
        )
        return None
    canonical = match.group("date")
    parsed = calendar_date(canonical)
    if parsed is None:
        errors.add(file, location, f"is not a real calendar date: {canonical!r}")
        return None
    weekday = match.group("weekday")
    if weekday is not None:
        normalized_weekday = "日" if weekday == "天" else weekday
        expected = WEEKDAYS[parsed.weekday()]
        if normalized_weekday != expected:
            errors.add(
                file,
                location,
                f"weekday is {weekday!r}, but {canonical} is 星期{expected}",
            )
    return canonical


def validate_plain_date(
    value: Any, file: Path, location: str, errors: Errors
) -> Optional[str]:
    if not isinstance(value, str) or DATE_ONLY_RE.fullmatch(value.strip()) is None:
        errors.add(file, location, "must be a string in exact YYYY-MM-DD format")
        return None
    canonical = value.strip()
    if calendar_date(canonical) is None:
        errors.add(file, location, f"is not a real calendar date: {canonical!r}")
        return None
    return canonical


def is_real_http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        # Accessing port also catches malformed values such as :not-a-number.
        _ = parsed.port
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return False

    host = hostname.rstrip(".").lower()
    if host in PLACEHOLDER_HOSTS or host.endswith(PLACEHOLDER_SUFFIXES):
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        # A dot rules out bare hostnames such as "news" and "localhost" while
        # still allowing ordinary domains and internationalized hostnames.
        return "." in host and not host.startswith(".") and not host.endswith(".")


def validate_sources(
    value: Any, file: Path, location: str, errors: Errors
) -> None:
    if not isinstance(value, list) or not value:
        errors.add(
            file,
            location,
            "must be a non-empty array containing at least one HTTP(S) source URL",
        )
        return

    valid_urls = 0
    for index, source in enumerate(value):
        source_location = f"{location}[{index}]"
        if not isinstance(source, dict):
            errors.add(file, source_location, "source must be a JSON object")
            continue
        if not nonempty_string(source.get("name")):
            errors.add(file, f"{source_location}.name", "must be a non-empty string")
        elif "单一来源" in source["name"] and not source["name"].endswith(
            "（单一来源）"
        ):
            errors.add(
                file,
                f"{source_location}.name",
                "single-source marker must be the exact suffix （单一来源）",
            )

        if "url" not in source:
            # Name-only secondary citations are accepted, provided this item has
            # at least one other source carrying a usable URL.
            continue
        if is_real_http_url(source.get("url")):
            valid_urls += 1
        else:
            errors.add(
                file,
                f"{source_location}.url",
                "must be a real http:// or https:// URL with a valid host",
            )

    if valid_urls == 0:
        errors.add(
            file,
            location,
            "item must contain at least one source with a real HTTP(S) URL",
        )


def validate_sections(
    value: Any,
    file: Path,
    base: str,
    legacy: bool,
    errors: Errors,
    stats: Stats,
) -> None:
    if not isinstance(value, list) or not value:
        errors.add(file, f"{base}.sections", "must be a non-empty array")
        return

    for section_index, section in enumerate(value):
        section_location = f"{base}.sections[{section_index}]"
        if not isinstance(section, dict):
            errors.add(file, section_location, "section must be a JSON object")
            continue
        stats.sections += 1
        if not nonempty_string(section.get("title")):
            errors.add(file, f"{section_location}.title", "must be a non-empty string")

        items = section.get("items")
        if not isinstance(items, list) or not items:
            errors.add(file, f"{section_location}.items", "must be a non-empty array")
            continue
        for item_index, item in enumerate(items):
            item_location = f"{section_location}.items[{item_index}]"
            if not isinstance(item, dict):
                errors.add(file, item_location, "item must be a JSON object")
                continue
            stats.artifact_items += 1
            for field in ("headline", "summary"):
                if not nonempty_string(item.get(field)):
                    errors.add(
                        file,
                        f"{item_location}.{field}",
                        "must be a non-empty string",
                    )

            if "expanded" not in item:
                # Older Kimi exports omitted false values.  New native artifacts
                # must always make the display state explicit.
                if not legacy:
                    errors.add(
                        file,
                        f"{item_location}.expanded",
                        "is required and must be a boolean",
                    )
            elif not isinstance(item.get("expanded"), bool):
                errors.add(file, f"{item_location}.expanded", "must be a boolean")

            validate_sources(item.get("sources"), file, f"{item_location}.sources", errors)


def validate_generated_at(
    value: Any, file: Path, location: str, errors: Errors
) -> None:
    if not nonempty_string(value):
        errors.add(file, location, "must be a non-empty ISO-8601 timestamp")
        return
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        errors.add(file, location, "must be a valid ISO-8601 timestamp")
        return
    if parsed.utcoffset() is None or not value.endswith("+08:00"):
        errors.add(file, location, "must use an explicit +08:00 offset")


def validate_waytoagi_artifact(
    artifact: dict[str, Any],
    file: Path,
    base: str,
    stamp: str,
    errors: Errors,
) -> None:
    """Apply the stricter source contract for native WayToAGI attachments."""
    expected_date = datetime.strptime(stamp, "%Y%m%d").date().isoformat()
    if artifact.get("date") != expected_date:
        errors.add(file, f"{base}.date", f"must be exactly {expected_date!r}")
    if artifact.get("attachTo") != expected_date:
        errors.add(file, f"{base}.attachTo", f"must be exactly {expected_date!r}")
    if artifact.get("label") != WAYTOAGI_LABEL:
        errors.add(file, f"{base}.label", f"must be exactly {WAYTOAGI_LABEL!r}")

    sections = artifact.get("sections")
    if not isinstance(sections, list) or len(sections) != 1:
        errors.add(file, f"{base}.sections", "WayToAGI artifact must contain exactly one section")
        return
    section = sections[0]
    if not isinstance(section, dict):
        return
    if section.get("title") != WAYTOAGI_SECTION_TITLE:
        errors.add(
            file,
            f"{base}.sections[0].title",
            f"must be exactly {WAYTOAGI_SECTION_TITLE!r}",
        )

    items = section.get("items")
    if not isinstance(items, list):
        return
    expected_mirror = WAYTOAGI_MIRROR_TEMPLATE.format(stamp)
    for item_index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        sources = item.get("sources")
        if not isinstance(sources, list):
            continue
        urls = {
            source.get("url")
            for source in sources
            if isinstance(source, dict) and isinstance(source.get("url"), str)
        }
        item_location = f"{base}.sections[0].items[{item_index}].sources"
        mirror_urls = sorted(
            url
            for url in urls
            if url.startswith("https://www.waytoagi.com/zh/blog/news-")
        )
        if mirror_urls != [expected_mirror]:
            errors.add(
                file,
                item_location,
                f"WayToAGI mirror URL set must be exactly {[expected_mirror]!r}",
            )

        wiki_urls = sorted(url for url in urls if url.startswith(WAYTOAGI_WIKI_PREFIX))
        if WAYTOAGI_ROLLING_LOG_URL in wiki_urls:
            errors.add(
                file,
                item_location,
                "the rolling seven-day log cannot stand in for an item-specific Feishu URL",
            )
        specific_urls = [
            url
            for url in wiki_urls
            if url != WAYTOAGI_ROLLING_LOG_URL
            and WAYTOAGI_WIKI_URL_RE.fullmatch(url)
        ]
        if len(specific_urls) != 1:
            errors.add(
                file,
                item_location,
                "must contain exactly one item-specific WayToAGI Feishu wiki URL",
            )
            continue
        wiki_url = specific_urls[0]
        ordered_urls = [
            source.get("url")
            for source in sources
            if isinstance(source, dict) and isinstance(source.get("url"), str)
        ]
        if ordered_urls != [expected_mirror, wiki_url] or len(sources) != 2:
            errors.add(
                file,
                item_location,
                "must contain exactly two sources in order: issue mirror, then item-specific Feishu URL",
            )


def validate_current_main_policy(
    artifact: dict[str, Any], file: Path, base: str, errors: Errors
) -> None:
    sections = artifact.get("sections")
    if not isinstance(sections, list):
        return
    if len(sections) != len(MAIN_SECTION_POLICY):
        errors.add(
            file,
            f"{base}.sections",
            f"current main report must contain exactly {len(MAIN_SECTION_POLICY)} sections",
        )

    total_items = 0
    expanded_items = 0
    for index, (expected_title, minimum, maximum) in enumerate(MAIN_SECTION_POLICY):
        if index >= len(sections) or not isinstance(sections[index], dict):
            continue
        section = sections[index]
        if section.get("title") != expected_title:
            errors.add(
                file,
                f"{base}.sections[{index}].title",
                f"must be {expected_title!r} and remain in the canonical order",
            )
        items = section.get("items")
        if not isinstance(items, list):
            continue
        count = len(items)
        total_items += count
        expanded_items += sum(
            1 for item in items if isinstance(item, dict) and item.get("expanded") is True
        )
        if not minimum <= count <= maximum:
            errors.add(
                file,
                f"{base}.sections[{index}].items",
                f"must contain {minimum}–{maximum} items, found {count}",
            )

    if not 20 <= total_items <= 28:
        errors.add(
            file,
            f"{base}.sections",
            f"current main report must contain 20–28 items, found {total_items}",
        )
    if not 1 <= expanded_items <= 2:
        errors.add(
            file,
            f"{base}.sections",
            f"current main report must contain 1–2 expanded items, found {expanded_items}",
        )

    one_liner = artifact.get("oneLiner")
    if nonempty_string(one_liner) and not one_liner.startswith("📌 今日一句话："):
        errors.add(
            file,
            f"{base}.oneLiner",
            "must start with '📌 今日一句话：'",
        )


def parse_reported(errors: Errors, stats: Stats) -> list[ReportedIssue]:
    try:
        text = REPORTED_FILE.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.add(REPORTED_FILE, "1", f"cannot read UTF-8 markdown: {exc}")
        return []

    issues: list[ReportedIssue] = []
    current: Optional[ReportedIssue] = None
    seen_headings: dict[tuple[str, str], int] = {}

    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line.startswith("## "):
            match = REPORTED_HEADING_RE.fullmatch(line)
            if match is None:
                errors.add(
                    REPORTED_FILE,
                    str(line_number),
                    "malformed issue heading; expected '## YYYY-MM-DD（label）'",
                )
                current = None
                continue
            date = validate_plain_date(
                match.group("date"), REPORTED_FILE, str(line_number), errors
            )
            label = match.group("label").strip()
            if not label:
                errors.add(REPORTED_FILE, str(line_number), "issue label cannot be empty")
            if date is None:
                current = None
                continue
            key = (date, label)
            if key in seen_headings:
                errors.add(
                    REPORTED_FILE,
                    str(line_number),
                    f"duplicate issue heading {date} + {label!r}; first seen on line {seen_headings[key]}",
                )
            else:
                seen_headings[key] = line_number
            current = ReportedIssue(date=date, label=label, items=[], line=line_number)
            issues.append(current)
            continue

        if not line.startswith("- "):
            continue
        match = REPORTED_ITEM_RE.fullmatch(line)
        if match is None:
            errors.add(
                REPORTED_FILE,
                str(line_number),
                "malformed archive item; expected '- YYYY-MM-DD | event text'",
            )
            continue
        if current is None:
            errors.add(REPORTED_FILE, str(line_number), "archive item has no issue heading")
            continue
        item_date = validate_plain_date(
            match.group("date"), REPORTED_FILE, str(line_number), errors
        )
        if item_date is not None and item_date != current.date:
            errors.add(
                REPORTED_FILE,
                str(line_number),
                f"item date {item_date} does not match heading date {current.date}",
            )
        text_value = match.group("text").strip()
        if not text_value:
            errors.add(REPORTED_FILE, str(line_number), "archive item text cannot be empty")
        else:
            current.items.append(text_value)
            stats.reported_items += 1

    for issue in issues:
        if not issue.items:
            errors.add(
                REPORTED_FILE,
                str(issue.line),
                f"issue {issue.date} + {issue.label!r} has no archive items",
            )

    stats.reported_issues = len(issues)
    return issues


def latin_tokens(text: str) -> set[str]:
    return {token.lower() for token in LATIN_TOKEN_RE.findall(text)}


def infer_legacy_label(
    artifact: dict[str, Any], date: Optional[str], reported: list[ReportedIssue]
) -> Optional[str]:
    """Resolve an old run's missing label the same way as build_data.py.

    Legacy exports predate the explicit ``label`` field.  Their label is stored
    in reported.md, so accepting them does not mean silently accepting an
    unlabeled main issue.
    """
    if date is None:
        return None
    artifact_tokens: set[str] = set()
    sections = artifact.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict) or not isinstance(section.get("items"), list):
                continue
            for item in section["items"]:
                if not isinstance(item, dict):
                    continue
                headline = item.get("headline") if isinstance(item.get("headline"), str) else ""
                summary = item.get("summary") if isinstance(item.get("summary"), str) else ""
                artifact_tokens.update(latin_tokens(f"{headline} {summary}"))

    scored: list[tuple[int, ReportedIssue]] = []
    for issue in reported:
        if issue.date != date:
            continue
        issue_tokens: set[str] = set()
        for item in issue.items:
            issue_tokens.update(latin_tokens(item))
        scored.append((len(artifact_tokens & issue_tokens), issue))
    if not scored:
        return None
    best_score = max(score for score, _ in scored)
    winners = [issue for score, issue in scored if score == best_score]
    if best_score < 3 or len(winners) != 1:
        return None
    return winners[0].label


def load_json_object(file: Path, errors: Errors) -> Optional[dict[str, Any]]:
    try:
        document = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.add(file, f"{exc.lineno}:{exc.colno}", f"invalid JSON: {exc.msg}")
        return None
    except (OSError, UnicodeError) as exc:
        errors.add(file, "1", f"cannot read UTF-8 JSON: {exc}")
        return None
    if not isinstance(document, dict):
        errors.add(file, "$", "JSON document must be an object")
        return None
    return document


def validate_artifact_file(
    file: Path,
    reported: list[ReportedIssue],
    errors: Errors,
    stats: Stats,
) -> Optional[tuple[str, str, Path]]:
    document = load_json_object(file, errors)
    if document is None:
        return None

    wrapped_legacy = "run" in document
    # During migration, old run exports may either retain their ``run.artifact``
    # wrapper or be normalized into a top-level ``legacy_*.json`` file.  Both
    # still carry the old convention where false ``expanded`` values can be
    # omitted.
    legacy = wrapped_legacy or file.name.startswith("legacy_")
    if legacy:
        stats.legacy_files += 1
    if wrapped_legacy:
        run = document.get("run")
        if not isinstance(run, dict):
            errors.add(file, "$.run", "legacy run must be a JSON object")
            return None
        artifact = run.get("artifact")
        if not isinstance(artifact, dict):
            errors.add(file, "$.run.artifact", "legacy artifact must be a JSON object")
            return None
        base = "$.run.artifact"
    else:
        artifact = document
        base = "$"

    date = validate_artifact_date(artifact.get("date"), file, f"{base}.date", errors)
    has_attach_to = "attachTo" in artifact
    attach_to: Optional[str] = None
    if has_attach_to:
        stats.attachments += 1
        attach_to = validate_plain_date(
            artifact.get("attachTo"), file, f"{base}.attachTo", errors
        )
    else:
        stats.main_artifacts += 1

    if not legacy:
        validate_generated_at(artifact.get("generatedAt"), file, f"{base}.generatedAt", errors)
        if has_attach_to:
            match = WAYTOAGI_FILENAME_RE.fullmatch(file.name)
            if match is None:
                errors.add(
                    file,
                    "$filename",
                    "attachment filename must be waytoagi-YYYYMMDD.json",
                )
            elif attach_to is not None and match.group("stamp") != attach_to.replace("-", ""):
                errors.add(
                    file,
                    "$filename",
                    f"filename date does not match attachTo {attach_to}",
                )
            if date is not None and attach_to is not None and date != attach_to:
                errors.add(file, f"{base}.date", "must match attachTo for WayToAGI artifacts")
            if match is not None:
                validate_waytoagi_artifact(
                    artifact, file, base, match.group("stamp"), errors
                )
        else:
            match = MAIN_FILENAME_RE.fullmatch(file.name)
            if match is None:
                errors.add(
                    file,
                    "$filename",
                    "current main filename must be YYYY-MM-DD-N.json",
                )
            elif date is not None and match.group("date") != date:
                errors.add(file, "$filename", f"filename date does not match {date}")

    if "label" in artifact and not nonempty_string(artifact.get("label")):
        errors.add(file, f"{base}.label", "must be a non-empty string when present")
    if "oneLiner" in artifact and not nonempty_string(artifact.get("oneLiner")):
        errors.add(file, f"{base}.oneLiner", "must be a non-empty string when present")

    validate_sections(artifact.get("sections"), file, base, legacy, errors, stats)

    if has_attach_to:
        return None

    if not legacy:
        validate_current_main_policy(artifact, file, base, errors)

    one_liner = artifact.get("oneLiner")
    if not nonempty_string(one_liner):
        errors.add(file, f"{base}.oneLiner", "main artifact requires a non-empty string")

    label: Optional[str]
    if nonempty_string(artifact.get("label")):
        label = artifact["label"].strip()
    elif legacy:
        label = infer_legacy_label(artifact, date, reported)
        if label is None:
            errors.add(
                file,
                f"{base}.label",
                "main artifact requires a label; legacy label could not be uniquely inferred from reported.md",
            )
    else:
        label = None
        errors.add(file, f"{base}.label", "main artifact requires a non-empty string")

    if date is None or label is None:
        return None
    return (date, label, file)


def validate_duplicate_main_issues(
    records: list[tuple[str, str, Path]], errors: Errors
) -> None:
    grouped: defaultdict[tuple[str, str], list[Path]] = defaultdict(list)
    for date, label, file in records:
        grouped[(date, label)].append(file)

    for (date, label), files in grouped.items():
        if len(files) < 2:
            continue
        names = ", ".join(file.name for file in files)
        for file in files:
            errors.add(
                file,
                "$.date+label",
                f"duplicate main issue {date} + {label!r} across files: {names}",
            )


def validate_main_archives(
    records: list[tuple[str, str, Path]],
    reported: list[ReportedIssue],
    errors: Errors,
) -> None:
    archived = {(issue.date, issue.label) for issue in reported}
    for date, label, file in records:
        if (date, label) not in archived:
            errors.add(
                file,
                "$.date+label",
                f"main issue {date} + {label!r} is missing from content/reported.md",
            )


def main() -> int:
    errors = Errors()
    stats = Stats()
    reported = parse_reported(errors, stats)

    if not ARTIFACT_DIR.is_dir():
        errors.add(ARTIFACT_DIR, "$", "artifact directory does not exist")
        artifact_files: list[Path] = []
    else:
        artifact_files = sorted(ARTIFACT_DIR.glob("*.json"))
        if not artifact_files:
            errors.add(ARTIFACT_DIR, "$", "no artifact JSON files found")

    stats.artifact_files = len(artifact_files)
    main_records: list[tuple[str, str, Path]] = []
    for file in artifact_files:
        record = validate_artifact_file(file, reported, errors, stats)
        if record is not None:
            main_records.append(record)
    validate_duplicate_main_issues(main_records, errors)
    validate_main_archives(main_records, reported, errors)

    if errors.messages:
        print(f"content validation failed with {len(errors.messages)} error(s):", file=sys.stderr)
        for message in errors.messages:
            print(f"  - {message}", file=sys.stderr)
        return 1

    print(
        "content validation passed: "
        f"{stats.artifact_files} artifact file(s) "
        f"({stats.main_artifacts} main, {stats.attachments} attachment, "
        f"{stats.legacy_files} legacy), "
        f"{stats.sections} section(s), {stats.artifact_items} sourced item(s); "
        f"reported.md has {stats.reported_issues} issue(s) and "
        f"{stats.reported_items} archived item(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
