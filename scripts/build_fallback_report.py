#!/usr/bin/env python3
"""Build a compact daily report or required-candidate addendum without an LLM.

This is an availability fallback, not a replacement for the researched main
edition.  It consumes the trusted inputs already collected by the workflow,
keeps only item-specific public URLs, and labels its output as an automatic
recovery edition so readers can distinguish it from editorial synthesis.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = ROOT / "content" / "artifacts"
REPORTED_FILE = ROOT / "content" / "reported.md"

SECTION_ORDER = (
    "🔥 AI 重要事件",
    "🎬 AI 创作 · 视频/音乐/媒体娱乐",
    "🌍 海外观察",
    "📄 论文与技术前沿",
    "🚀 AI 一人公司（OPC）",
    "💻 GitHub Trending",
)
RECOVERY_SUFFIX = "·自动恢复版"
LABEL_RE = re.compile(r"^第([\u96f6一二两三四五六七八九十百]+)期")
URL_RE = re.compile(r"https?://\S+|www\.\S+|t\.co/\S+", re.IGNORECASE)
SPACE_RE = re.compile(r"\s+")
LATIN_RE = re.compile(r"[A-Za-z]")
CHINESE_RE = re.compile(r"[\u3400-\u9fff]")

IMPORTANT_HANDLES = {
    "sama",
    "openai",
    "anthropicai",
    "claudeai",
    "googlelabs",
    "googledeepmind",
    "demishassabis",
    "ylecun",
}
CREATIVE_KEYWORDS = {
    "audio",
    "cinema",
    "creative",
    "design",
    "film",
    "image",
    "media",
    "movie",
    "music",
    "song",
    "thumbnail",
    "video",
    "voice",
    "youtube",
    "创作",
    "图像",
    "影视",
    "视频",
    "音乐",
}
TECH_KEYWORDS = {
    "alignment",
    "benchmark",
    "context",
    "eval",
    "inference",
    "model",
    "reasoning",
    "research",
    "rl",
    "safety",
    "security",
    "simulation",
    "token",
    "training",
    "安全",
    "模型",
    "研究",
    "训练",
}
OPC_KEYWORDS = {
    "arr",
    "business",
    "customer",
    "founder",
    "growth",
    "launch",
    "onboarding",
    "pmf",
    "product",
    "revenue",
    "saas",
    "ship",
    "startup",
    "team",
    "workflow",
    "产品",
    "创业",
    "增长",
    "营收",
}
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Shanghai report date, YYYY-MM-DD")
    parser.add_argument("--builders", type=Path, required=True)
    parser.add_argument("--priority", type=Path)
    parser.add_argument("--waytoagi", type=Path)
    parser.add_argument("--generated-at", required=True, help="ISO-8601 collector time")
    parser.add_argument("--artifact-dir", type=Path, default=ARTIFACT_DIR)
    parser.add_argument("--reported", type=Path, default=REPORTED_FILE)
    parser.add_argument("--max-items", type=int, default=14)
    parser.add_argument("--min-items", type=int, default=3)
    parser.add_argument(
        "--archive-only",
        action="store_true",
        help="Only archive validated WayToAGI items when today's main report already exists",
    )
    return parser.parse_args(argv)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text("utf-8"))
    except Exception as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def atomic_write_text(path: Path, value: str) -> None:
    """Replace one UTF-8 file atomically, leaving no partial JSON/Markdown."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    without_urls = URL_RE.sub("", value)
    return SPACE_RE.sub(" ", without_urls).strip(" -\t\r\n")


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip(" ,.;:!，。；：！") + "…"


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in keywords)


def classify(candidate: dict[str, Any]) -> str:
    if candidate.get("kind") == "priority":
        return SECTION_ORDER[0]
    text = str(candidate.get("text") or "")
    if contains_any(text, CREATIVE_KEYWORDS):
        return SECTION_ORDER[1]
    if contains_any(text, OPC_KEYWORDS):
        return SECTION_ORDER[4]
    if contains_any(text, TECH_KEYWORDS):
        return SECTION_ORDER[3]
    return SECTION_ORDER[2]


def has_signal(text: str) -> bool:
    if len(text) < 36 or text.startswith("@"):
        return False
    alphanumeric = sum(character.isalnum() for character in text)
    return alphanumeric >= 24 and bool(LATIN_RE.search(text) or CHINESE_RE.search(text))


def candidate_score(candidate: dict[str, Any]) -> float:
    text = str(candidate.get("text") or "")
    score = float(candidate.get("likes") or 0)
    score += 2.5 * float(candidate.get("retweets") or 0)
    score += 0.5 * float(candidate.get("replies") or 0)
    score += min(len(text), 500) / 5
    if candidate.get("kind") == "priority":
        score += 1_000_000
    elif str(candidate.get("handle") or "").lower() in IMPORTANT_HANDLES:
        score += 400
    return score


def existing_source_urls(artifact_dir: Path) -> set[str]:
    urls: set[str] = set()
    for path in artifact_dir.glob("*.json"):
        try:
            document = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        if isinstance(document, dict) and isinstance(document.get("run"), dict):
            document = document["run"].get("artifact")
        if not isinstance(document, dict):
            continue
        for section in document.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for item in section.get("items") or []:
                if not isinstance(item, dict):
                    continue
                for source in item.get("sources") or []:
                    if isinstance(source, dict) and isinstance(source.get("url"), str):
                        urls.add(source["url"])
    return urls


def existing_priority_ids(artifact_dir: Path) -> set[str]:
    priority_ids: set[str] = set()
    for path in artifact_dir.glob("*.json"):
        try:
            document = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        if isinstance(document, dict) and isinstance(document.get("run"), dict):
            document = document["run"].get("artifact")
        if not isinstance(document, dict):
            continue
        for section in document.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for item in section.get("items") or []:
                if not isinstance(item, dict):
                    continue
                for candidate_id in item.get("priorityIds") or []:
                    if isinstance(candidate_id, str) and candidate_id:
                        priority_ids.add(candidate_id)
    return priority_ids


def priority_candidates(
    document: dict[str, Any], covered_ids: set[str]
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in document.get("candidates") or []:
        if not isinstance(raw, dict) or raw.get("required") is not True:
            continue
        candidate_id = raw.get("id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError("required priority candidate is missing a stable id")
        if candidate_id in covered_ids or candidate_id in seen_ids:
            continue
        seen_ids.add(candidate_id)
        url = raw.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        title = clean_text(raw.get("title") or raw.get("headline"))
        if not title:
            match_terms = [str(value) for value in raw.get("matchTerms") or [] if value]
            title = " / ".join(match_terms) or candidate_id
        summary = clean_text(raw.get("summary") or raw.get("details"))
        candidates.append(
            {
                "kind": "priority",
                "text": title,
                "headline": truncate(title, 110),
                "summary": truncate(summary or "官方候选源已收录，请通过原文核对完整信息。", 360),
                "url": url,
                "sourceName": str(raw.get("officialSource") or "官方发布"),
                "priorityIds": [candidate_id],
            }
        )
    return candidates


def builder_candidates(document: dict[str, Any], used_urls: set[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_texts: set[str] = set()
    for builder in document.get("x") or []:
        if not isinstance(builder, dict):
            continue
        name = clean_text(builder.get("name")) or clean_text(builder.get("handle"))
        handle = clean_text(builder.get("handle"))
        for tweet in builder.get("tweets") or []:
            if not isinstance(tweet, dict):
                continue
            url = tweet.get("url")
            text = clean_text(tweet.get("text"))
            if (
                not isinstance(url, str)
                or not url.startswith(("http://", "https://"))
                or url in used_urls
                or url in seen_urls
                or text in seen_texts
                or not has_signal(text)
            ):
                continue
            seen_urls.add(url)
            seen_texts.add(text)
            candidate = {
                "kind": "tweet",
                "name": name or "AI builder",
                "handle": handle,
                "text": text,
                "url": url,
                "createdAt": tweet.get("createdAt"),
                "likes": tweet.get("likes") or 0,
                "retweets": tweet.get("retweets") or 0,
                "replies": tweet.get("replies") or 0,
            }
            candidates.append(candidate)
    return candidates


def select_candidates(
    candidates: list[dict[str, Any]], minimum: int, maximum: int
) -> list[dict[str, Any]]:
    required = [candidate for candidate in candidates if candidate.get("kind") == "priority"]
    if len(required) > maximum:
        raise ValueError(f"{len(required)} required priority candidates exceed --max-items={maximum}")

    grouped: dict[str, list[dict[str, Any]]] = {title: [] for title in SECTION_ORDER}
    for candidate in candidates:
        candidate["section"] = classify(candidate)
        grouped[candidate["section"]].append(candidate)
    for values in grouped.values():
        values.sort(key=candidate_score, reverse=True)

    quotas = {
        SECTION_ORDER[0]: 3,
        SECTION_ORDER[1]: 2,
        SECTION_ORDER[2]: 4,
        SECTION_ORDER[3]: 2,
        SECTION_ORDER[4]: 3,
        SECTION_ORDER[5]: 0,
    }
    selected: list[dict[str, Any]] = []
    selected_urls: set[str] = set()
    for title in SECTION_ORDER:
        for candidate in grouped[title]:
            if candidate.get("url") in selected_urls:
                continue
            if candidate.get("kind") != "priority" and sum(
                1 for chosen in selected if chosen.get("section") == title
            ) >= quotas[title]:
                continue
            selected.append(candidate)
            selected_urls.add(str(candidate.get("url")))
            if len(selected) >= maximum:
                break
        if len(selected) >= maximum:
            break

    if len(selected) < minimum:
        remaining = sorted(candidates, key=candidate_score, reverse=True)
        for candidate in remaining:
            url = str(candidate.get("url"))
            if url in selected_urls:
                continue
            selected.append(candidate)
            selected_urls.add(url)
            if len(selected) >= minimum or len(selected) >= maximum:
                break

    if len(selected) < minimum:
        raise ValueError(
            f"only {len(selected)} unique source-linked signals remain; need at least {minimum}"
        )
    return selected[:maximum]


def candidate_to_item(candidate: dict[str, Any]) -> dict[str, Any]:
    if candidate.get("kind") == "priority":
        item = {
            "headline": candidate["headline"],
            "summary": candidate["summary"],
            "expanded": False,
            "priorityIds": candidate["priorityIds"],
            "sources": [
                {
                    "name": f"{candidate['sourceName']}（单一来源）",
                    "url": candidate["url"],
                }
            ],
        }
        return item

    name = str(candidate.get("name") or "AI builder")
    text = str(candidate.get("text") or "")
    created_at = candidate.get("createdAt")
    date_note = ""
    if isinstance(created_at, str):
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            date_note = parsed.astimezone(timezone(timedelta(hours=8))).strftime("%m/%d %H:%M")
        except ValueError:
            pass
    language_note = "原文为英文" if LATIN_RE.search(text) and not CHINESE_RE.search(text) else "保留原文措辞"
    return {
        "headline": f"{name} 公开动态：{truncate(text, 86)}",
        "summary": (
            f"自动恢复版一手信号"
            f"{f'，发布于 {date_note}' if date_note else ''}；{language_note}，"
            "未调用付费模型做二次改写。请通过原文链接核对完整上下文。"
        ),
        "expanded": False,
        "sources": [{"name": f"X / {name}（单一来源）", "url": candidate["url"]}],
    }


def chinese_to_int(value: str) -> int:
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    total = 0
    current = 0
    for character in value:
        if character in digits:
            current = digits[character]
        elif character == "十":
            total += (current or 1) * 10
            current = 0
        elif character == "百":
            total += (current or 1) * 100
            current = 0
        else:
            raise ValueError(f"unsupported Chinese number {value!r}")
    return total + current


def int_to_chinese(value: int) -> str:
    if not 1 <= value <= 999:
        raise ValueError(f"issue number out of supported range: {value}")
    digits = "零一二三四五六七八九"
    if value < 10:
        return digits[value]
    if value < 20:
        return "十" + (digits[value % 10] if value % 10 else "")
    if value < 100:
        return digits[value // 10] + "十" + (digits[value % 10] if value % 10 else "")
    hundreds, remainder = divmod(value, 100)
    result = digits[hundreds] + "百"
    if not remainder:
        return result
    if remainder < 10:
        return result + "零" + digits[remainder]
    return result + int_to_chinese(remainder)


def next_issue_number(artifact_dir: Path) -> int:
    highest = 0
    for path in artifact_dir.glob("*.json"):
        try:
            document = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        if isinstance(document, dict) and isinstance(document.get("run"), dict):
            document = document["run"].get("artifact")
        if not isinstance(document, dict):
            continue
        label = document.get("label")
        if not isinstance(label, str):
            continue
        match = LABEL_RE.match(label)
        if match:
            try:
                highest = max(highest, chinese_to_int(match.group(1)))
            except ValueError:
                continue
    return highest + 1


def next_label(artifact_dir: Path) -> str:
    return f"第{int_to_chinese(next_issue_number(artifact_dir))}期{RECOVERY_SUFFIX}"


def next_addendum_label(artifact_dir: Path) -> str:
    return f"第{int_to_chinese(next_issue_number(artifact_dir))}期·自动恢复补刊"


def normalize_generated_at(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"--generated-at must be ISO-8601, got {value!r}") from exc
    if parsed.utcoffset() is None:
        raise ValueError("--generated-at must include a timezone")
    return parsed.astimezone(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def pending_waytoagi_archive_items(
    input_document: dict[str, Any], artifact_dir: Path, reported_text: str
) -> list[str]:
    pending: list[str] = []
    for issue in input_document.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        stamp = str(issue.get("stamp") or "")
        issue_date = str(issue.get("date") or "")
        if not re.fullmatch(r"\d{8}", stamp) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", issue_date):
            raise ValueError("WayToAGI input contains an invalid issue stamp/date")
        artifact_path = artifact_dir / f"waytoagi-{stamp}.json"
        artifact = load_json(artifact_path)
        sections = artifact.get("sections")
        if not isinstance(sections, list) or len(sections) != 1:
            raise ValueError(f"{artifact_path.name} must contain exactly one section")
        artifact_items = sections[0].get("items") if isinstance(sections[0], dict) else None
        source_items = issue.get("items")
        if not isinstance(artifact_items, list) or not isinstance(source_items, list):
            raise ValueError(f"WayToAGI issue {stamp} has malformed items")
        artifact_headlines = [
            item.get("headline") if isinstance(item, dict) else None for item in artifact_items
        ]
        source_headlines = [
            item.get("title") if isinstance(item, dict) else None for item in source_items
        ]
        if artifact_headlines != source_headlines:
            raise ValueError(f"{artifact_path.name} does not match the trusted WayToAGI input")
        for title in source_headlines:
            archive_text = f"WayToAGI {issue_date}：{title}"
            if archive_text not in reported_text and archive_text not in pending:
                pending.append(archive_text)
    return pending


def append_reported(
    path: Path,
    date_value: str,
    label: str,
    headlines: list[str],
    archive_items: list[str],
) -> None:
    text = path.read_text("utf-8") if path.exists() else ""
    heading = f"## {date_value}（{label}）"
    if heading in text.splitlines():
        raise ValueError(f"reported archive already contains {heading}")
    all_items = headlines + archive_items
    block = "\n".join([heading, "", *[f"- {date_value} | {headline}" for headline in all_items]])
    separator = "\n\n" if text and not text.endswith("\n\n") else ""
    atomic_write_text(path, text + separator + block + "\n")


def artifact_headlines(document: dict[str, Any], path: Path) -> list[str]:
    headlines: list[str] = []
    for section in document.get("sections") or []:
        if not isinstance(section, dict):
            raise ValueError(f"{path.name} contains a malformed section")
        for item in section.get("items") or []:
            headline = item.get("headline") if isinstance(item, dict) else None
            if not isinstance(headline, str) or not headline.strip():
                raise ValueError(f"{path.name} contains an item without a headline")
            headlines.append(headline)
    if not headlines:
        raise ValueError(f"{path.name} contains no report headlines")
    return headlines


def same_day_artifacts(artifact_dir: Path, date_value: str) -> list[tuple[int, Path]]:
    matches: list[tuple[int, Path]] = []
    pattern = re.compile(rf"^{re.escape(date_value)}-([1-9][0-9]*)\.json$")
    for path in artifact_dir.glob(f"{date_value}-*.json"):
        match = pattern.fullmatch(path.name)
        if match:
            matches.append((int(match.group(1)), path))
    matches.sort()
    if matches and [sequence for sequence, _path in matches] != list(
        range(1, matches[-1][0] + 1)
    ):
        raise ValueError(f"same-day artifact sequence for {date_value} is not contiguous")
    return matches


def repair_missing_same_day_archives(
    artifact_dir: Path, reported: Path, date_value: str
) -> int:
    """Repair the safe crash state: complete artifact exists, heading does not."""
    text = reported.read_text("utf-8") if reported.exists() else ""
    lines = text.splitlines()
    date_prefix = f"## {date_value}（"
    same_day_headings = [line for line in lines if line.startswith(date_prefix)]
    repaired = 0
    for sequence, path in same_day_artifacts(artifact_dir, date_value):
        document = load_json(path)
        label = document.get("label")
        if not isinstance(label, str) or not label:
            raise ValueError(f"{path.name} is missing its issue label")
        heading = f"## {date_value}（{label}）"
        if heading in lines:
            continue
        expected_sequence = len(same_day_headings) + 1
        if sequence != expected_sequence:
            raise ValueError(
                f"cannot repair {path.name}: expected archive position {expected_sequence}"
            )
        append_reported(
            reported,
            date_value,
            label,
            artifact_headlines(document, path),
            [],
        )
        repaired += 1
        text = reported.read_text("utf-8")
        lines = text.splitlines()
        same_day_headings.append(heading)
    return repaired


def append_archive_items_to_current_issue(
    path: Path, date_value: str, archive_items: list[str]
) -> None:
    if not archive_items:
        return
    text = path.read_text("utf-8") if path.exists() else ""
    last_heading = next(
        (line for line in reversed(text.splitlines()) if line.startswith("## ")),
        None,
    )
    if not isinstance(last_heading, str) or not last_heading.startswith(
        f"## {date_value}（"
    ):
        raise ValueError(
            "cannot append deterministic archive items because the current date "
            "is not the last reported.md issue"
        )
    separator = "" if not text or text.endswith("\n") else "\n"
    addition = "".join(f"- {date_value} | {item}\n" for item in archive_items)
    atomic_write_text(path, text + separator + addition)


def write_recovery_addenda(
    arguments: argparse.Namespace,
    report_day: datetime,
    candidates: list[dict[str, Any]],
) -> list[Path]:
    written: list[Path] = []
    remaining = list(candidates)
    while remaining:
        chunk, remaining = remaining[:5], remaining[5:]
        sequence = same_day_artifacts(arguments.artifact_dir, arguments.date)[-1][0] + 1
        output = arguments.artifact_dir / f"{arguments.date}-{sequence}.json"
        label = next_addendum_label(arguments.artifact_dir)
        items = [candidate_to_item(candidate) for candidate in chunk]
        document = {
            "date": f"{arguments.date} 星期{'一二三四五六日'[report_day.weekday()]}",
            "kind": "addendum",
            "label": label,
            "generatedAt": normalize_generated_at(arguments.generated_at),
            "oneLiner": "📌 补刊：模型稿未通过生产门，自动补录已验证的官方重大发布。",
            "sections": [{"title": SECTION_ORDER[0], "items": items}],
        }
        atomic_write_text(
            output, json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        )
        append_reported(
            arguments.reported,
            arguments.date,
            label,
            [item["headline"] for item in items],
            [],
        )
        written.append(output)
        print(
            f"fallback report: wrote {output.name} with {len(items)} required candidate(s)"
        )
    return written


def build_report(arguments: argparse.Namespace) -> Optional[Path]:
    try:
        report_day = datetime.strptime(arguments.date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("--date must be an exact calendar date in YYYY-MM-DD format") from exc
    if arguments.min_items < 3 or arguments.max_items > 20 or arguments.min_items > arguments.max_items:
        raise ValueError("item limits must satisfy 3 <= min-items <= max-items <= 20")

    arguments.artifact_dir.mkdir(parents=True, exist_ok=True)
    output = arguments.artifact_dir / f"{arguments.date}-1.json"
    reported_text = arguments.reported.read_text("utf-8") if arguments.reported.exists() else ""
    waytoagi = load_json(arguments.waytoagi) if arguments.waytoagi else {"issues": []}
    archive_items = pending_waytoagi_archive_items(
        waytoagi, arguments.artifact_dir, reported_text
    )
    if getattr(arguments, "archive_only", False) and not output.exists():
        print(
            f"fallback report: {output.name} does not exist; archive-only mode is a no-op"
        )
        return None
    if output.exists():
        repaired = repair_missing_same_day_archives(
            arguments.artifact_dir, arguments.reported, arguments.date
        )
        if repaired:
            print(f"fallback report: repaired {repaired} missing archive heading(s)")
        if archive_items:
            append_archive_items_to_current_issue(
                arguments.reported, arguments.date, archive_items
            )
            print(f"fallback report: appended {len(archive_items)} WayToAGI archive item(s)")
        if getattr(arguments, "archive_only", False):
            if not archive_items and not repaired:
                print(f"fallback report: {output.name} already exists; no-op")
            return None

        priority = load_json(arguments.priority) if arguments.priority else {"candidates": []}
        uncovered_priority = priority_candidates(
            priority, existing_priority_ids(arguments.artifact_dir)
        )
        if uncovered_priority:
            write_recovery_addenda(arguments, report_day, uncovered_priority)
        elif not archive_items and not repaired:
            print(f"fallback report: {output.name} already exists; no-op")
        return None

    builders = load_json(arguments.builders)
    priority = load_json(arguments.priority) if arguments.priority else {"candidates": []}
    used_urls = existing_source_urls(arguments.artifact_dir)
    covered_ids = existing_priority_ids(arguments.artifact_dir)
    candidates = priority_candidates(priority, covered_ids)
    candidates.extend(builder_candidates(builders, used_urls))
    selected = select_candidates(candidates, arguments.min_items, arguments.max_items)

    grouped: dict[str, list[dict[str, Any]]] = {title: [] for title in SECTION_ORDER}
    for candidate in selected:
        grouped[str(candidate["section"])].append(candidate_to_item(candidate))
    sections = [
        {"title": title, "items": grouped[title]}
        for title in SECTION_ORDER
        if grouped[title]
    ]
    if not sections:
        raise ValueError("selected signals do not cover a canonical section")

    label = next_label(arguments.artifact_dir)
    weekday = "一二三四五六日"[report_day.weekday()]
    document = {
        "date": f"{arguments.date} 星期{weekday}",
        "label": label,
        "generatedAt": normalize_generated_at(arguments.generated_at),
        "fallback": True,
        "oneLiner": (
            "📌 今日一句话：模型稿未通过生产门时先保住可核验的一手信号，"
            "本期为不调用付费 API 的自动恢复版。"
        ),
        "sections": sections,
    }
    atomic_write_text(output, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    headlines = [item["headline"] for section in sections for item in section["items"]]
    append_reported(
        arguments.reported, arguments.date, label, headlines, archive_items
    )
    print(f"fallback report: wrote {output.name} with {len(headlines)} source-linked items")
    return output


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = parse_args(argv)
        build_report(arguments)
    except (OSError, ValueError) as exc:
        print(f"fallback report failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
