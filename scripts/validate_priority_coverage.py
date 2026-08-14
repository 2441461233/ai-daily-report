#!/usr/bin/env python3
"""Validate deterministic priority-news input and mandatory report coverage.

The official-source collector writes an immutable JSON input before the daily
agent starts.  This gate first validates that input, then requires every
``required`` candidate to be represented by a major-events item using three
independent signals:

* the item explicitly claims the candidate's stable id in ``priorityIds``;
* one item source exactly matches an official evidence URL after conservative
  URL normalization; and
* every candidate ``matchTerms`` value occurs in the headline/summary using
  token-aware matching.

Committed HEAD artifacts are read from Git rather than from the mutable working
tree.  A strong evidence+terms match there is the deliberately narrow exemption
for an event that was already reported before priority ids existed.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo


APP_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_DIRECTORY = Path("content/artifacts")
IMPORTANT_SECTION = "🔥 AI 重要事件"
NATIVE_ARTIFACT_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<sequence>[1-9]\d*)\.json$"
)
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
ASCII_TERM_RE = re.compile(r"^[a-z0-9][a-z0-9.+_\- ]*$")
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "ref", "source"}
PLACEHOLDER_HOSTS = {"example.com", "example.net", "example.org", "localhost"}
PLACEHOLDER_SUFFIXES = (".example", ".invalid", ".localhost", ".test")
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_WINDOW_HOURS = 168.0
MAX_INPUT_AGE = timedelta(hours=2)
MAX_CLOCK_SKEW = timedelta(minutes=5)


class Errors:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def add(self, location: str, message: str) -> None:
        self.messages.append(f"{location}: {message}")


@dataclass(frozen=True)
class Candidate:
    id: str
    title: str
    url: str
    published_at: datetime
    precision: str
    category: str
    required: bool
    official_source: str
    evidence_urls: frozenset[str]
    match_terms: tuple[str, ...]


@dataclass(frozen=True)
class PriorityInput:
    generated_at: datetime
    window_hours: float
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class ArtifactItem:
    artifact_date: str
    artifact_kind: str
    location: str
    section_title: str
    headline: str
    summary: str
    source_urls: frozenset[str]
    priority_ids: tuple[str, ...]
    is_new: bool


@dataclass(frozen=True)
class CoverageSummary:
    required: int
    covered_today: int
    already_covered: int
    optional: int


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def parse_aware_datetime(value: Any, location: str, errors: Errors) -> Optional[datetime]:
    if not nonempty_string(value):
        errors.add(location, "must be a non-empty ISO-8601 timestamp")
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        errors.add(location, "must be a valid ISO-8601 timestamp")
        return None
    if parsed.utcoffset() is None:
        errors.add(location, "must include an explicit timezone offset")
        return None
    return parsed.astimezone(timezone.utc)


def parse_report_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an exact YYYY-MM-DD calendar date") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("must be an exact YYYY-MM-DD calendar date")
    return value


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def term_matches(term: str, text: str) -> bool:
    """Match CJK terms as phrases and ASCII terms with alphanumeric boundaries."""
    normalized_term = normalize_text(term)
    normalized_text = normalize_text(text)
    if not normalized_term:
        return False
    if not ASCII_TERM_RE.fullmatch(normalized_term):
        return normalized_term in normalized_text
    pattern = re.escape(normalized_term).replace(r"\ ", r"\s+")
    if normalized_term.isalpha():
        # A brand may be immediately followed by its version (``Qwen3.8``),
        # but must not match inside another word such as ``GrokBot``.
        boundary = "a-z"
    elif normalized_term[0].isdigit():
        # A version may follow a brand or ``v`` without whitespace, while the
        # digit boundary keeps 4.6 from matching 14.60.
        boundary = "0-9"
    else:
        boundary = "a-z0-9"
    return re.search(
        rf"(?<![{boundary}]){pattern}(?![{boundary}])", normalized_text
    ) is not None


def normalize_url(value: Any) -> Optional[str]:
    """Conservatively canonicalize a URL without collapsing distinct resources."""
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or hostname is None:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None

    host = hostname.rstrip(".").lower()
    if not host or host in PLACEHOLDER_HOSTS or host.endswith(PLACEHOLDER_SUFFIXES):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.startswith(".") or host.endswith("."):
            return None

    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    bracketed_host = f"[{host}]" if ":" in host else host
    netloc = bracketed_host if port is None or default_port else f"{bracketed_host}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    query_items = [
        (key, item_value)
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in TRACKING_QUERY_KEYS
    ]
    query_items.sort()
    return urlunsplit((scheme, netloc, path, urlencode(query_items, doseq=True), ""))


def require_string(
    value: Any, location: str, errors: Errors, *, identifier: bool = False
) -> Optional[str]:
    if not nonempty_string(value):
        errors.add(location, "must be a non-empty string")
        return None
    result = value.strip()
    if identifier and ID_RE.fullmatch(result) is None:
        errors.add(location, "must be a stable id using only letters, digits, '.', '_', ':', or '-'")
        return None
    return result


def validate_string_array(
    value: Any,
    location: str,
    errors: Errors,
    *,
    normalize_for_uniqueness: bool = False,
) -> list[str]:
    if not isinstance(value, list) or not value:
        errors.add(location, "must be a non-empty array")
        return []
    output: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_location = f"{location}[{index}]"
        if not nonempty_string(item):
            errors.add(item_location, "must be a non-empty string")
            continue
        cleaned = item.strip()
        key = normalize_text(cleaned) if normalize_for_uniqueness else cleaned
        if key in seen:
            errors.add(item_location, f"duplicates {cleaned!r}")
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def validate_candidate(
    value: Any,
    index: int,
    generated_at: Optional[datetime],
    window_hours: Optional[float],
    seen_ids: set[str],
    errors: Errors,
) -> Optional[Candidate]:
    location = f"$.candidates[{index}]"
    if not isinstance(value, dict):
        errors.add(location, "must be a JSON object")
        return None

    candidate_id = require_string(value.get("id"), f"{location}.id", errors, identifier=True)
    if candidate_id is not None:
        if candidate_id in seen_ids:
            errors.add(f"{location}.id", f"duplicate candidate id {candidate_id!r}")
        else:
            seen_ids.add(candidate_id)
    title = require_string(value.get("title"), f"{location}.title", errors)
    category = require_string(value.get("category"), f"{location}.category", errors)
    official_source = require_string(
        value.get("officialSource"), f"{location}.officialSource", errors
    )

    normalized_url = normalize_url(value.get("url"))
    if normalized_url is None:
        errors.add(f"{location}.url", "must be a non-placeholder HTTP(S) URL")

    published_at = parse_aware_datetime(
        value.get("publishedAt"), f"{location}.publishedAt", errors
    )
    precision_value = value.get("precision", "instant")
    if precision_value not in {"day", "instant"}:
        errors.add(f"{location}.precision", "must be either 'day' or 'instant'")
        precision = "instant"
    else:
        precision = precision_value

    required_value = value.get("required")
    if type(required_value) is not bool:
        errors.add(f"{location}.required", "must be a boolean")
        required = False
    else:
        required = required_value

    raw_evidence = validate_string_array(
        value.get("evidenceUrls"), f"{location}.evidenceUrls", errors
    )
    normalized_evidence: set[str] = set()
    for evidence_index, evidence in enumerate(raw_evidence):
        normalized = normalize_url(evidence)
        if normalized is None:
            errors.add(
                f"{location}.evidenceUrls[{evidence_index}]",
                "must be a non-placeholder HTTP(S) URL",
            )
            continue
        if normalized in normalized_evidence:
            errors.add(
                f"{location}.evidenceUrls[{evidence_index}]",
                "duplicates another normalized evidence URL",
            )
            continue
        normalized_evidence.add(normalized)
    if normalized_url is not None:
        normalized_evidence.add(normalized_url)

    match_terms = validate_string_array(
        value.get("matchTerms"),
        f"{location}.matchTerms",
        errors,
        normalize_for_uniqueness=True,
    )

    if published_at is not None and generated_at is not None and window_hours is not None:
        # Date-only sources carry no publication time. Treat them as the whole
        # UTC calendar day and require that interval to overlap the collection
        # window; treating midnight as an exact instant would wrongly discard
        # e.g. an Aug 12 announcement in an Aug 13 02:45 run with a 24h window.
        publication_start = published_at
        publication_end = (
            published_at + timedelta(days=1)
            if precision == "day"
            else published_at
        )
        if publication_start > generated_at:
            errors.add(
                f"{location}.publishedAt",
                f"future publication {published_at.isoformat()} is after generatedAt",
            )
        cutoff = generated_at - timedelta(hours=window_hours)
        if publication_end <= cutoff:
            errors.add(
                f"{location}.publishedAt",
                f"falls outside the {window_hours:g}-hour collection window",
            )

    required_fields = (
        candidate_id,
        title,
        normalized_url,
        published_at,
        category,
        official_source,
    )
    if any(field is None for field in required_fields) or not normalized_evidence or not match_terms:
        return None
    assert candidate_id is not None
    assert title is not None
    assert normalized_url is not None
    assert published_at is not None
    assert category is not None
    assert official_source is not None
    return Candidate(
        id=candidate_id,
        title=title,
        url=normalized_url,
        published_at=published_at,
        precision=precision,
        category=category,
        required=required,
        official_source=official_source,
        evidence_urls=frozenset(normalized_evidence),
        match_terms=tuple(match_terms),
    )


def validate_priority_input(
    document: Any, report_date: str, now: datetime, errors: Errors
) -> Optional[PriorityInput]:
    if not isinstance(document, dict):
        errors.add("$", "priority input must be a JSON object")
        return None
    if document.get("schemaVersion") != 1:
        errors.add("$.schemaVersion", "must be exactly 1")

    generated_at = parse_aware_datetime(document.get("generatedAt"), "$.generatedAt", errors)
    if generated_at is not None:
        if generated_at > now + MAX_CLOCK_SKEW:
            errors.add("$.generatedAt", "is more than five minutes in the future")
        elif now - generated_at > MAX_INPUT_AGE:
            errors.add("$.generatedAt", "is more than two hours old")
        generated_date = generated_at.astimezone(SHANGHAI).date().isoformat()
        if generated_date != report_date:
            errors.add(
                "$.generatedAt",
                f"Shanghai date is {generated_date}, expected report date {report_date}",
            )

    window_value = document.get("windowHours")
    if (
        isinstance(window_value, bool)
        or not isinstance(window_value, (int, float))
        or not 0 < float(window_value) <= MAX_WINDOW_HOURS
    ):
        errors.add("$.windowHours", f"must be a number greater than 0 and at most {MAX_WINDOW_HOURS:g}")
        window_hours: Optional[float] = None
    else:
        window_hours = float(window_value)

    sources = document.get("sources")
    declared_candidate_total = 0
    if not isinstance(sources, list) or not sources:
        errors.add("$.sources", "must be a non-empty array")
    else:
        seen_source_ids: set[str] = set()
        for index, source in enumerate(sources):
            location = f"$.sources[{index}]"
            if not isinstance(source, dict):
                errors.add(location, "must be a JSON object")
                continue
            source_id = require_string(source.get("id"), f"{location}.id", errors, identifier=True)
            if source_id is not None:
                if source_id in seen_source_ids:
                    errors.add(f"{location}.id", f"duplicate source id {source_id!r}")
                seen_source_ids.add(source_id)
            require_string(source.get("name"), f"{location}.name", errors)
            require_string(
                source.get("officialSource"), f"{location}.officialSource", errors
            )
            if type(source.get("critical")) is not bool:
                errors.add(f"{location}.critical", "must be a boolean")
                critical = False
            else:
                critical = source["critical"]
            if type(source.get("coverageSufficient")) is not bool:
                errors.add(f"{location}.coverageSufficient", "must be a boolean")
                coverage_sufficient = False
            else:
                coverage_sufficient = source["coverageSufficient"]
            unresolved_signals = source.get("unresolvedSignals", [])
            if not isinstance(unresolved_signals, list) or any(
                not nonempty_string(value) or ID_RE.fullmatch(value.strip()) is None
                for value in unresolved_signals
            ):
                errors.add(
                    f"{location}.unresolvedSignals",
                    "must be an array of stable ids when present",
                )
                unresolved_signals = []
            elif len(unresolved_signals) != len(set(unresolved_signals)):
                errors.add(f"{location}.unresolvedSignals", "must not contain duplicates")
            if coverage_sufficient and unresolved_signals:
                errors.add(
                    location,
                    "coverage cannot be sufficient while discovery signals are unresolved",
                )
            status = source.get("status")
            if status not in {"ok", "partial", "error"}:
                errors.add(f"{location}.status", "must be 'ok', 'partial', or 'error'")
            candidate_count = source.get("candidateCount")
            if (
                isinstance(candidate_count, bool)
                or not isinstance(candidate_count, int)
                or candidate_count < 0
            ):
                errors.add(f"{location}.candidateCount", "must be a non-negative integer")
                count = 0
            else:
                count = candidate_count
                declared_candidate_total += count
            parse_aware_datetime(source.get("fetchedAt"), f"{location}.fetchedAt", errors)
            if critical and not coverage_sufficient:
                errors.add(location, "critical source coverage is insufficient")

    candidates_value = document.get("candidates")
    if not isinstance(candidates_value, list):
        errors.add("$.candidates", "must be an array")
        candidates_value = []
    candidates: list[Candidate] = []
    seen_candidate_ids: set[str] = set()
    for index, value in enumerate(candidates_value):
        candidate = validate_candidate(
            value,
            index,
            generated_at,
            window_hours,
            seen_candidate_ids,
            errors,
        )
        if candidate is not None:
            candidates.append(candidate)
    if isinstance(sources, list) and declared_candidate_total != len(candidates_value):
        errors.add(
            "$.sources[*].candidateCount",
            f"declared total {declared_candidate_total} does not match {len(candidates_value)} candidates",
        )

    if not isinstance(document.get("errors"), list):
        errors.add("$.errors", "must be an array")

    if generated_at is None or window_hours is None:
        return None
    return PriorityInput(
        generated_at=generated_at,
        window_hours=window_hours,
        candidates=tuple(candidates),
    )


def load_json_file(path: Path, errors: Errors) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.add(str(path), "required input file does not exist")
    except json.JSONDecodeError as exc:
        errors.add(str(path), f"invalid JSON at {exc.lineno}:{exc.colno}: {exc.msg}")
    except (OSError, UnicodeError) as exc:
        errors.add(str(path), f"cannot read UTF-8 JSON: {exc}")
    return None


def git_command(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def load_head_artifacts(root: Path, errors: Errors) -> dict[str, dict[str, Any]]:
    verify = git_command(root, "rev-parse", "--verify", "HEAD")
    if verify.returncode != 0:
        # A repository with no first commit has no historical exemption.
        inside = git_command(root, "rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0:
            errors.add("HEAD", f"cannot inspect Git repository: {inside.stderr.strip()}")
        return {}

    listed = git_command(
        root,
        "ls-tree",
        "-r",
        "--name-only",
        "HEAD",
        "--",
        ARTIFACT_DIRECTORY.as_posix(),
    )
    if listed.returncode != 0:
        errors.add("HEAD", f"cannot list committed artifacts: {listed.stderr.strip()}")
        return {}

    output: dict[str, dict[str, Any]] = {}
    for relative in listed.stdout.splitlines():
        if Path(relative).parent.as_posix() != ARTIFACT_DIRECTORY.as_posix():
            continue
        if NATIVE_ARTIFACT_RE.fullmatch(Path(relative).name) is None:
            continue
        shown = git_command(root, "show", f"HEAD:{relative}")
        if shown.returncode != 0:
            errors.add(f"HEAD:{relative}", f"cannot read artifact: {shown.stderr.strip()}")
            continue
        try:
            document = json.loads(shown.stdout)
        except json.JSONDecodeError as exc:
            errors.add(f"HEAD:{relative}", f"invalid JSON: {exc.msg}")
            continue
        if isinstance(document, dict):
            output[relative] = document
        else:
            errors.add(f"HEAD:{relative}", "artifact must be a JSON object")
    return output


def read_priority_ids(
    value: Any, location: str, is_new: bool, errors: Errors
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        if is_new:
            errors.add(f"{location}.priorityIds", "must be a non-empty array when present")
        return ()
    output: list[str] = []
    seen: set[str] = set()
    for index, candidate_id in enumerate(value):
        item_location = f"{location}.priorityIds[{index}]"
        if not nonempty_string(candidate_id) or ID_RE.fullmatch(candidate_id.strip()) is None:
            if is_new:
                errors.add(item_location, "must be a valid non-empty priority candidate id")
            continue
        cleaned = candidate_id.strip()
        if cleaned in seen:
            if is_new:
                errors.add(item_location, f"duplicate priority id {cleaned!r} in one item")
            continue
        seen.add(cleaned)
        output.append(cleaned)
    return tuple(output)


def extract_artifact_items(
    artifact: dict[str, Any],
    artifact_name: str,
    *,
    is_new: bool,
    errors: Errors,
) -> list[ArtifactItem]:
    filename_match = NATIVE_ARTIFACT_RE.fullmatch(Path(artifact_name).name)
    if filename_match is None:
        return []
    artifact_date = filename_match.group("date")
    declared_date = str(artifact.get("date", "")).split()[0]
    if is_new and declared_date != artifact_date:
        errors.add(artifact_name, f"declares date {declared_date!r}, expected {artifact_date!r}")
    sections = artifact.get("sections")
    if not isinstance(sections, list):
        if is_new:
            errors.add(f"{artifact_name}:$.sections", "must be an array")
        return []

    output: list[ArtifactItem] = []
    for section_index, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        title = section.get("title") if isinstance(section.get("title"), str) else ""
        items = section.get("items")
        if not isinstance(items, list):
            continue
        for item_index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            location = f"{artifact_name}:$.sections[{section_index}].items[{item_index}]"
            headline = item.get("headline") if isinstance(item.get("headline"), str) else ""
            summary = item.get("summary") if isinstance(item.get("summary"), str) else ""
            priority_ids = read_priority_ids(item.get("priorityIds"), location, is_new, errors)
            source_urls: set[str] = set()
            sources = item.get("sources")
            if isinstance(sources, list):
                for source in sources:
                    if not isinstance(source, dict):
                        continue
                    normalized = normalize_url(source.get("url"))
                    if normalized is not None:
                        source_urls.add(normalized)
            output.append(
                ArtifactItem(
                    artifact_date=artifact_date,
                    artifact_kind=(
                        artifact.get("kind")
                        if isinstance(artifact.get("kind"), str)
                        else "main"
                    ),
                    location=location,
                    section_title=title,
                    headline=headline,
                    summary=summary,
                    source_urls=frozenset(source_urls),
                    priority_ids=priority_ids,
                    is_new=is_new,
                )
            )
    return output


def load_new_artifact_items(
    root: Path, head_paths: set[str], errors: Errors
) -> list[ArtifactItem]:
    directory = root / ARTIFACT_DIRECTORY
    if not directory.is_dir():
        errors.add(str(ARTIFACT_DIRECTORY), "artifact directory does not exist")
        return []
    output: list[ArtifactItem] = []
    for path in sorted(directory.glob("*.json")):
        if NATIVE_ARTIFACT_RE.fullmatch(path.name) is None:
            continue
        relative = path.relative_to(root).as_posix()
        if relative in head_paths:
            continue
        document = load_json_file(path, errors)
        if isinstance(document, dict):
            output.extend(
                extract_artifact_items(document, relative, is_new=True, errors=errors)
            )
    return output


def evidence_matches(candidate: Candidate, item: ArtifactItem) -> bool:
    return bool(candidate.evidence_urls & item.source_urls)


def terms_match(candidate: Candidate, item: ArtifactItem) -> bool:
    text = f"{item.headline} {item.summary}"
    return all(term_matches(term, text) for term in candidate.match_terms)


def strong_match(candidate: Candidate, item: ArtifactItem) -> bool:
    return evidence_matches(candidate, item) and terms_match(candidate, item)


def validate_coverage(
    priority_input: PriorityInput,
    report_date: str,
    root: Path,
    errors: Errors,
) -> CoverageSummary:
    candidate_by_id = {candidate.id: candidate for candidate in priority_input.candidates}
    head_artifacts = load_head_artifacts(root, errors)
    head_items: list[ArtifactItem] = []
    for relative, artifact in head_artifacts.items():
        head_items.extend(
            extract_artifact_items(artifact, relative, is_new=False, errors=errors)
        )
    new_items = load_new_artifact_items(root, set(head_artifacts), errors)

    head_strong: dict[str, list[ArtifactItem]] = {candidate_id: [] for candidate_id in candidate_by_id}
    head_today_explicit: dict[str, list[ArtifactItem]] = {
        candidate_id: [] for candidate_id in candidate_by_id
    }
    for candidate in priority_input.candidates:
        for item in head_items:
            if item.section_title != IMPORTANT_SECTION or item.artifact_date > report_date:
                continue
            if strong_match(candidate, item):
                head_strong[candidate.id].append(item)
                if item.artifact_date == report_date and candidate.id in item.priority_ids:
                    head_today_explicit[candidate.id].append(item)

    new_claims: dict[str, list[ArtifactItem]] = {}
    valid_new_today: dict[str, list[ArtifactItem]] = {}
    for item in new_items:
        for candidate_id in item.priority_ids:
            candidate = candidate_by_id.get(candidate_id)
            if candidate is None:
                errors.add(item.location, f"unknown priority id {candidate_id!r} for this input")
                continue
            new_claims.setdefault(candidate_id, []).append(item)
            if item.artifact_kind == "addendum" and not candidate.required:
                errors.add(
                    item.location,
                    f"addendum may only claim an uncovered required candidate, not optional {candidate_id!r}",
                )
                continue
            if item.section_title != IMPORTANT_SECTION:
                errors.add(
                    item.location,
                    f"priority id {candidate_id!r} is outside {IMPORTANT_SECTION!r}",
                )
                continue
            missing: list[str] = []
            if not evidence_matches(candidate, item):
                missing.append("an exact official evidence URL")
            missing_terms = [
                term for term in candidate.match_terms if not term_matches(term, f"{item.headline} {item.summary}")
            ]
            if missing_terms:
                missing.append(f"match terms {missing_terms!r}")
            if missing:
                errors.add(
                    item.location,
                    f"claim {candidate_id!r} is missing " + " and ".join(missing),
                )
                continue
            if item.artifact_date == report_date:
                valid_new_today.setdefault(candidate_id, []).append(item)

    for candidate_id, claims in new_claims.items():
        if len(claims) > 1:
            locations = ", ".join(item.location for item in claims)
            errors.add(
                f"priority:{candidate_id}",
                f"new artifacts claim the same candidate more than once: {locations}",
            )
        historical_matches = head_strong.get(candidate_id, [])
        if historical_matches:
            errors.add(
                claims[0].location,
                f"duplicates candidate {candidate_id!r} already strongly covered in HEAD at "
                f"{historical_matches[0].location}",
            )

    required_count = sum(candidate.required for candidate in priority_input.candidates)
    optional_count = len(priority_input.candidates) - required_count
    covered_today = 0
    already_covered = 0
    for candidate in priority_input.candidates:
        today_explicit = head_today_explicit.get(candidate.id, []) or valid_new_today.get(
            candidate.id, []
        )
        if today_explicit:
            if candidate.required:
                covered_today += 1
            continue

        legacy_matches = head_strong.get(candidate.id, [])
        if legacy_matches:
            if candidate.required:
                if any(item.artifact_date == report_date for item in legacy_matches):
                    covered_today += 1
                else:
                    already_covered += 1
            continue

        if candidate.required:
            near_claims = new_claims.get(candidate.id, [])
            suffix = (
                f"; invalid claim(s): {', '.join(item.location for item in near_claims)}"
                if near_claims
                else ""
            )
            errors.add(
                f"priority:{candidate.id}",
                f"required candidate {candidate.title!r} is not covered by today's main/addendum{suffix}",
            )

    return CoverageSummary(
        required=required_count,
        covered_today=covered_today,
        already_covered=already_covered,
        optional=optional_count,
    )


def print_failure(errors: Errors) -> None:
    print(f"priority coverage validation failed with {len(errors.messages)} error(s):", file=sys.stderr)
    for message in errors.messages:
        print(f"  - {message}", file=sys.stderr)


def run_validation(
    *,
    input_path: Path,
    report_date: str,
    now: datetime,
    input_only: bool,
    root: Path = APP_ROOT,
) -> int:
    errors = Errors()
    document = load_json_file(input_path, errors)
    priority_input = validate_priority_input(document, report_date, now, errors)
    if errors.messages or priority_input is None:
        print_failure(errors)
        return 1

    if input_only:
        required = sum(candidate.required for candidate in priority_input.candidates)
        print(
            "priority input validation passed: "
            f"{len(priority_input.candidates)} candidate(s), {required} required, "
            f"window {priority_input.window_hours:g}h"
        )
        return 0

    summary = validate_coverage(priority_input, report_date, root, errors)
    if errors.messages:
        print_failure(errors)
        return 1
    print(
        "priority coverage validation passed: "
        f"{summary.required} required candidate(s), "
        f"{summary.covered_today} covered today, "
        f"{summary.already_covered} already covered in HEAD, "
        f"{summary.optional} optional"
    )
    return 0


def parse_cli_now(value: str) -> datetime:
    errors = Errors()
    parsed = parse_aware_datetime(value, "--now", errors)
    if parsed is None:
        raise argparse.ArgumentTypeError(errors.messages[0].split(": ", 1)[-1])
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="priority-news JSON input")
    parser.add_argument("--date", required=True, type=parse_report_date, help="Shanghai report date")
    parser.add_argument(
        "--now",
        type=parse_cli_now,
        default=None,
        help="timezone-aware validation time (defaults to current UTC time)",
    )
    parser.add_argument(
        "--input-only",
        action="store_true",
        help="validate the deterministic input without checking report artifacts",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    arguments = build_parser().parse_args(list(argv) if argv is not None else None)
    now = arguments.now or datetime.now(timezone.utc)
    return run_validation(
        input_path=arguments.input,
        report_date=arguments.date,
        now=now,
        input_only=arguments.input_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
