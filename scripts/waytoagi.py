#!/usr/bin/env python3
"""Deterministically collect WayToAGI's dated knowledge-base digests.

The public blog mirror renders each digest into ordinary HTML.  Its index is
the source of truth for which ``news-YYYYMMDD`` pages actually exist; guessed
date URLs are deliberately not probed because unpublished pages return 500.

JSON is the only stdout output.  Human-readable diagnostics go to stderr so
the result can be consumed directly by the daily-report automation.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from pathlib import Path
from time import sleep
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "content" / "artifacts"
INDEX_URL = "https://www.waytoagi.com/zh/blog"
ISSUE_URL = "https://www.waytoagi.com/zh/blog/news-{}"
FEISHU_HOST = "waytoagi.feishu.cn"
ROLLING_LOG_TOKEN = "QPe5w5g7UisbEkkow8XcDmOpn8e"
TIMEOUT = 20
FETCH_ATTEMPTS = 3
RETRYABLE_HTTP = {408, 429, 500, 502, 503, 504, 524}
SHANGHAI = ZoneInfo("Asia/Shanghai")
START_STAMP = "20260728"
ARTIFACT_RE = re.compile(r"^waytoagi-(\d{8})\.json$")
Fetch = Callable[[str], str]


class CollectionError(RuntimeError):
    """Raised when the remote source is unavailable or structurally invalid."""


def normalize_text(value: str) -> str:
    value = value.replace("\u200b", "").replace("\ufeff", "")
    return " ".join(value.split())


def canonical_feishu_url(value: str) -> Optional[str]:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname != FEISHU_HOST:
        return None
    match = re.fullmatch(r"/wiki/([A-Za-z0-9]+)", parsed.path)
    if not match or match.group(1) == ROLLING_LOG_TOKEN:
        return None
    return urllib.parse.urlunsplit(("https", FEISHU_HOST, parsed.path, "", ""))


def parse_stamp(value: str) -> str:
    if not re.fullmatch(r"(?:\d{8}|\d{4}-\d{2}-\d{2})", value):
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYYMMDD or YYYY-MM-DD"
        )
    compact = value.replace("-", "")
    try:
        parsed = datetime.strptime(compact, "%Y%m%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}; expected YYYYMMDD or YYYY-MM-DD") from exc
    return parsed.strftime("%Y%m%d")


def fetch_html(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; ai-daily-report/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    last_message = "unknown fetch failure"
    last_error: Optional[BaseException] = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                if response.status != 200:
                    raise CollectionError(f"GET {url} returned HTTP {response.status}")
                content_type = response.headers.get_content_type()
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    raise CollectionError(
                        f"GET {url} returned unexpected content type {content_type!r}"
                    )
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, "replace")
        except urllib.error.HTTPError as exc:
            last_message = f"GET {url} returned HTTP {exc.code}"
            last_error = exc
            if exc.code not in RETRYABLE_HTTP:
                break
        except urllib.error.URLError as exc:
            last_message = f"GET {url} failed: {exc.reason}"
            last_error = exc
        except http.client.HTTPException as exc:
            last_message = f"GET {url} failed: {type(exc).__name__}: {exc}"
            last_error = exc
        except OSError as exc:
            last_message = f"GET {url} failed: {exc}"
            last_error = exc
        if attempt + 1 < FETCH_ATTEMPTS:
            sleep(2**attempt)
    raise CollectionError(last_message) from last_error


class IndexParser(HTMLParser):
    """Collect issue stamps only from real anchor hrefs in the index."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stamps: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        path = urllib.parse.urlsplit(href).path
        match = re.fullmatch(r"/(?:zh/)?blog/news-(\d{8})/?", path)
        if not match:
            return
        stamp = match.group(1)
        try:
            datetime.strptime(stamp, "%Y%m%d")
        except ValueError:
            return
        if stamp not in self.stamps:
            self.stamps.append(stamp)


class IssueParser(HTMLParser):
    """Parse only ``.markdown-body.blog-content > ul > li`` article items."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._tag_stack: list[tuple[str, set[str]]] = []
        self._target_div_depth: Optional[int] = None
        self._target_ul_depth: Optional[int] = None
        self._item_depth: Optional[int] = None
        self._item_text: list[str] = []
        self._link_depth: Optional[int] = None
        self._link_text: list[str] = []
        self._link_url: Optional[str] = None
        self.items: list[dict[str, str]] = []
        self.target_blocks = 0
        self.target_lists = 0
        self.target_li_count = 0
        self.complete_item_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        self._tag_stack.append((tag, classes))
        depth = len(self._tag_stack)

        if (
            self._target_div_depth is None
            and tag == "div"
            and {"markdown-body", "blog-content"}.issubset(classes)
        ):
            self._target_div_depth = depth
            self.target_blocks += 1
            return

        if self._target_div_depth is None:
            return
        if self._target_ul_depth is None and depth == self._target_div_depth + 1 and tag == "ul":
            self._target_ul_depth = depth
            self.target_lists += 1
            return
        if self._target_ul_depth is None:
            return

        if self._item_depth is None and depth == self._target_ul_depth + 1 and tag == "li":
            self._item_depth = depth
            self._item_text = []
            self._link_depth = None
            self._link_text = []
            self._link_url = None
            self.target_li_count += 1
            return

        if self._item_depth is not None and tag == "a" and self._link_url is None:
            link = canonical_feishu_url(values.get("href") or "")
            if link:
                self._link_depth = depth
                self._link_url = link
                self._link_text = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        # Images and line breaks do not contribute to the title/summary.
        return

    def handle_data(self, data: str) -> None:
        if self._item_depth is None:
            return
        self._item_text.append(data)
        if self._link_depth is not None:
            self._link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._tag_stack:
            return
        depth = len(self._tag_stack)

        if self._link_depth == depth and tag == "a":
            self._link_depth = None
        if self._item_depth == depth and tag == "li":
            self._finish_item()
            self._item_depth = None
        if self._target_ul_depth == depth and tag == "ul":
            self._target_ul_depth = None
        if self._target_div_depth == depth and tag == "div":
            self._target_div_depth = None

        self._tag_stack.pop()

    def _finish_item(self) -> None:
        if not self._link_url:
            return
        title = normalize_text("".join(self._link_text))
        full_text = normalize_text("".join(self._item_text))
        if not title or not full_text:
            return
        marker = full_text.find(title)
        if marker < 0:
            return
        summary = full_text[marker + len(title) :].lstrip("》】）)]}：: -—")
        if not summary:
            return
        self.complete_item_count += 1
        self.items.append({"title": title, "summary": summary, "url": self._link_url})


def discover_stamps(html: str) -> list[str]:
    parser = IndexParser()
    parser.feed(html)
    if not parser.stamps:
        raise CollectionError("WayToAGI index contains no linked news-YYYYMMDD issues")
    return sorted(parser.stamps, reverse=True)


def parse_issue(html: str, stamp: str) -> list[dict[str, str]]:
    parser = IssueParser()
    parser.feed(html)
    if parser.target_blocks != 1:
        raise CollectionError(
            f"WayToAGI issue {stamp} expected exactly one .markdown-body.blog-content block; "
            f"found {parser.target_blocks}"
        )
    if parser.target_lists != 1:
        raise CollectionError(
            f"WayToAGI issue {stamp} expected exactly one direct child list; found {parser.target_lists}"
        )
    if parser.target_li_count == 0:
        raise CollectionError(f"WayToAGI issue {stamp} contains no direct list items")
    if not parser.items:
        raise CollectionError(f"WayToAGI issue {stamp} contains no complete Feishu-linked items")
    if parser.complete_item_count != parser.target_li_count:
        raise CollectionError(
            f"WayToAGI issue {stamp} parsed {parser.complete_item_count} complete items from "
            f"{parser.target_li_count} list items"
        )
    return parser.items


def consumed_stamps() -> set[str]:
    """Return attachment-backed consumption state.

    ``waytoagi-consumed.txt`` is a derived cache and may contain stale legacy
    entries.  Attachment filenames are the only authoritative record.
    """
    stamps: set[str] = set()
    if ARTIFACTS.is_dir():
        for path in ARTIFACTS.glob("waytoagi-*.json"):
            match = ARTIFACT_RE.fullmatch(path.name)
            if match:
                stamps.add(match.group(1))
    return stamps


def select_stamps(
    available: Iterable[str],
    requested: Optional[list[str]],
    include_consumed: bool,
    now: datetime,
    refresh_days: int = 14,
) -> list[str]:
    available_set = set(available)
    if requested:
        missing = sorted(set(requested) - available_set)
        if missing:
            raise CollectionError(
                "requested date(s) are not linked by the WayToAGI index: " + ", ".join(missing)
            )
        in_scope = set(requested)
    else:
        today_stamp = now.astimezone(SHANGHAI).date().strftime("%Y%m%d")
        in_scope = {
            stamp for stamp in available_set if START_STAMP <= stamp <= today_stamp
        }

    if include_consumed:
        return sorted(in_scope)

    archived = consumed_stamps()
    missing = in_scope - archived
    if refresh_days == 0:
        return sorted(missing)
    refresh_cutoff = (
        now.astimezone(SHANGHAI).date() - timedelta(days=refresh_days - 1)
    ).strftime("%Y%m%d")
    refresh = {stamp for stamp in in_scope & archived if stamp >= refresh_cutoff}
    return sorted(missing | refresh)


def artifact_snapshot(stamp: str) -> Optional[list[tuple[str, str, str]]]:
    """Return archived title, summary and Feishu URL records in display order."""
    path = ARTIFACTS / f"waytoagi-{stamp}.json"
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text("utf-8"))
        sections = document["sections"]
        stored_items = [item for section in sections for item in section["items"]]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CollectionError(f"cannot compare malformed attachment {path.relative_to(ROOT)}: {exc}") from exc
    records: list[tuple[str, str, str]] = []
    for item in stored_items:
        if not isinstance(item, dict):
            raise CollectionError(f"cannot compare malformed attachment {path.relative_to(ROOT)}: item is not an object")
        sources = item.get("sources")
        if not isinstance(sources, list):
            raise CollectionError(f"cannot compare malformed attachment {path.relative_to(ROOT)}: sources is not a list")
        wiki_urls: list[str] = []
        for source in sources:
            if not isinstance(source, dict):
                continue
            link = canonical_feishu_url(source.get("url", ""))
            if link:
                wiki_urls.append(link)
        headline = item.get("headline")
        summary = item.get("summary")
        if (
            not isinstance(headline, str)
            or not isinstance(summary, str)
            or len(wiki_urls) != 1
        ):
            raise CollectionError(
                f"cannot compare malformed attachment {path.relative_to(ROOT)}: "
                "each item needs a title, summary and one Feishu URL"
            )
        records.append((headline, summary, wiki_urls[0]))
    return records


def issue_record(stamp: str, items: list[dict[str, str]]) -> dict[str, object]:
    issue_date = datetime.strptime(stamp, "%Y%m%d").date().isoformat()
    return {
        "stamp": stamp,
        "date": issue_date,
        "sourceUrl": ISSUE_URL.format(stamp),
        "sourceItemCount": len(items),
        "items": items,
    }


def refresh_error(stamp: str, error: CollectionError) -> dict[str, str]:
    """Describe a non-fatal refresh failure for an already archived issue."""
    issue_date = datetime.strptime(stamp, "%Y%m%d").date().isoformat()
    return {
        "severity": "warning",
        "code": "archived_issue_refresh_failed",
        "stage": "refresh",
        "stamp": stamp,
        "date": issue_date,
        "sourceUrl": ISSUE_URL.format(stamp),
        "message": str(error),
    }


def attachment_for(issue: dict[str, object], generated_at: str) -> dict[str, object]:
    issue_date = str(issue["date"])
    parsed = date.fromisoformat(issue_date)
    source_url = str(issue["sourceUrl"])
    source_name = f"WayToAGI 精选 {parsed.month}/{parsed.day}"
    items = issue["items"]
    assert isinstance(items, list)
    return {
        "date": issue_date,
        "label": "WayToAGI 精选",
        "attachTo": issue_date,
        "generatedAt": generated_at,
        "sections": [
            {
                "title": "🧭 WayToAGI 知识库精选",
                "note": f"来自 WayToAGI 知识库精选 {parsed.month}/{parsed.day}，按原文日期归档。",
                "items": [
                    {
                        "headline": str(item["title"]),
                        "summary": str(item["summary"]),
                        "expanded": False,
                        "sources": [
                            {"name": source_name, "url": source_url},
                            {"name": "原文（飞书）", "url": str(item["url"])},
                        ],
                    }
                    for item in items
                ],
            }
        ],
    }


def write_artifacts(issues: list[dict[str, object]]) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for issue in issues:
        stamp = str(issue["stamp"])
        issue_day = date.fromisoformat(str(issue["date"]))
        generated_at = datetime.combine(issue_day, time(23, 59), SHANGHAI).isoformat(timespec="seconds")
        destination = ARTIFACTS / f"waytoagi-{stamp}.json"
        rendered = json.dumps(attachment_for(issue, generated_at), ensure_ascii=False, indent=2) + "\n"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(rendered, "utf-8")
        temporary.replace(destination)
        try:
            display = destination.relative_to(ROOT)
        except ValueError:
            display = destination
        print(f"wrote {display}", file=sys.stderr)


def collect(
    fetch: Fetch = fetch_html,
    requested: Optional[list[str]] = None,
    include_consumed: bool = False,
    now: Optional[datetime] = None,
    refresh_days: int = 14,
) -> dict[str, object]:
    current = now or datetime.now(SHANGHAI)
    mode = "requested" if requested is not None else (
        "include_consumed" if include_consumed else "automatic"
    )
    index_html = fetch(INDEX_URL)
    available = discover_stamps(index_html)
    selected = select_stamps(
        available, requested, include_consumed, current, refresh_days=refresh_days
    )
    issues: list[dict[str, object]] = []
    refresh_errors: list[dict[str, str]] = []
    for stamp in selected:
        archived = artifact_snapshot(stamp)
        try:
            items = parse_issue(fetch(ISSUE_URL.format(stamp)), stamp)
        except CollectionError as exc:
            if archived is None or mode != "automatic":
                # A linked but unarchived issue is new source data.  Omitting it
                # would falsely describe this run as complete. Explicit replay
                # modes are strict too; only the default automatic refresh of a
                # committed archive may degrade to a structured error.
                raise
            warning = refresh_error(stamp, exc)
            refresh_errors.append(warning)
            print(
                f"warning: skipped archived issue refresh {stamp}: {warning['message']}",
                file=sys.stderr,
            )
            continue
        current_records = [
            (item["title"], item["summary"], item["url"]) for item in items
        ]
        changed = archived is None or archived != current_records
        if include_consumed or changed:
            issues.append(issue_record(stamp, items))
            reason = "new" if archived is None else "refreshed"
            print(f"collected {stamp}: {len(items)} item(s), {reason}", file=sys.stderr)
        else:
            print(f"checked {stamp}: archived attachment is current", file=sys.stderr)
    if not issues:
        print("no unconsumed WayToAGI issues in the selected window", file=sys.stderr)
    return {
        "schemaVersion": 1,
        "sourceIndex": INDEX_URL,
        "generatedAt": current.astimezone(SHANGHAI).isoformat(timespec="seconds"),
        "mode": mode,
        "refreshDays": refresh_days,
        "issues": issues,
        "refreshErrors": refresh_errors,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dates",
        nargs="+",
        type=parse_stamp,
        metavar="YYYYMMDD",
        help="collect these index-linked dates (YYYYMMDD or YYYY-MM-DD)",
    )
    parser.add_argument(
        "--include-consumed",
        action="store_true",
        help="include dates that already have a committed attachment",
    )
    parser.add_argument(
        "--refresh-days",
        type=int,
        default=14,
        metavar="N",
        help="re-check archived issues from the last N days for upstream additions (default: 14)",
    )
    parser.add_argument(
        "--write-artifacts",
        action="store_true",
        help="write complete content/artifacts/waytoagi-YYYYMMDD.json attachments",
    )
    args = parser.parse_args(argv)
    if args.refresh_days < 0 or args.refresh_days > 60:
        parser.error("--refresh-days must be between 0 and 60")
    try:
        payload = collect(
            requested=args.dates,
            include_consumed=args.include_consumed,
            refresh_days=args.refresh_days,
        )
        issues = payload["issues"]
        assert isinstance(issues, list)
        if args.write_artifacts:
            write_artifacts(issues)
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except CollectionError as exc:
        print(f"waytoagi: {exc}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError) as exc:
        print(f"waytoagi: local I/O failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
