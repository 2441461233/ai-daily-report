#!/usr/bin/env python3
"""Build public/data/reports.json from repository-owned content.

Rich report artifacts live in content/artifacts as plain artifact JSON. The
compressed content/reported.md archive supplies issues without rich artifacts,
and content/links.json backfills URLs for those compressed entries.
"""
import json
import re
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Optional

APP_ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = APP_ROOT / "content"
ARTIFACT_DIR = CONTENT_DIR / "artifacts"
LINKS_FILE = CONTENT_DIR / "links.json"
REPORTED_FILE = CONTENT_DIR / "reported.md"
OUT_FILE = APP_ROOT / "public" / "data" / "reports.json"

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

RE_HEAD = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})（(.+?)）\s*$")
RE_ITEM = re.compile(r"^-\s+(\d{4}-\d{2}-\d{2})\s*\|\s*(.+)$")
RE_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
RE_TRAIL_PAREN = re.compile(r"[（(]([^（）()]+)[）)]\s*$")
RE_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9.+-]{2,}")
RE_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9.-]*\.(dev|com|net|org|cn|io|ai|fm|so)$", re.I)
RE_DATEISH = re.compile(r"^\d+[/\d.-]*$")
RE_NATIVE_ARTIFACT = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<sequence>[1-9]\d*)\.json$"
)

MEDIA = {
    "财联社": "财联社", "量子位": "量子位", "新浪财经": "新浪财经", "新浪": "新浪",
    "新华社": "新华社", "新华网": "新华网", "央视": "央视", "央视网": "央视网", "央媒": "央媒",
    "界面": "界面新闻", "界面新闻": "界面新闻", "36氪": "36氪", "虎嗅": "虎嗅",
    "财新": "财新", "澎湃": "澎湃新闻", "澎湃新闻": "澎湃新闻", "彭博": "彭博",
    "路透": "路透", "华尔街见闻": "华尔街见闻", "东方财富": "东方财富",
    "科创板日报": "科创板日报", "晚点": "晚点LatePost", "极客公园": "极客公园",
    "钛媒体": "钛媒体", "机器之心": "机器之心", "新智元": "新智元", "智东西": "智东西",
    "雷锋网": "雷锋网", "第一财经": "第一财经", "证券时报": "证券时报",
    "中国经济网": "中国经济网", "蓝鲸": "蓝鲸新闻", "蓝鲸新闻": "蓝鲸新闻", "星图": "星图",
    "it之家": "IT之家", "techcrunch": "TechCrunch", "the verge": "The Verge",
    "wired": "Wired", "404 media": "404 Media", "the information": "The Information",
    "bloomberg": "彭博", "reuters": "路透", "ofweek": "OFweek", "infoq": "InfoQ",
    "billboard": "Billboard", "hn": "HN", "hacker news": "HN", "aisi": "AISI",
    "afm": "AFM", "mbw": "MBW", "axios": "Axios", "venturebeat": "VentureBeat",
    "the register": "The Register", "github": "GitHub", "spotify": "Spotify",
}

RULES = [
    ("开源与工具", ["GitHub", "Trending", "开源", "stars", "HN ", "Apache", "ComfyUI", "workflow"]),
    ("音乐与内容", ["Suno", "Udio", "音乐", "歌曲", "Billboard", "唱片", "Spotify", "Merlin",
                    "Stable Audio", "可灵", "即梦", "PixVerse", "短剧", "视频", "AIGC",
                    "虚拟演员", "AI 短片", "AI 歌", "ElevenLabs", "创作"]),
    ("融资与商业", ["融资", "估值", "IPO", "募资", "ARR", "涨停", "营收", "中报", "高盛",
                    "投资", "领投", "D 轮", "B 轮", "C 轮", "Pre-A", "Pre-IPO", "变现", "商单", "报价"]),
    ("模型与公司", ["OpenAI", "Anthropic", "Claude", "GPT", "DeepSeek", "Qwen", "阿里", "字节",
                    "腾讯", "百度", "MiniMax", "Google", "DeepMind", "Meta", "模型", "Kimi",
                    "月之暗面", "AMD", "芯片", "Agent", "agent", "豆包", "混元", "爱诗",
                    "Opus", "Muse", "Seed", "Om AI", "Stability", "Cloudflare", "Neon", "YC"]),
]
FALLBACK = "其他动态"
WAYTOAGI_SECTION = "🧭 WayToAGI 知识库精选"
GITHUB_SECTION_TITLES = frozenset({"GitHub Trending", "💻 GitHub Trending"})
OPC_SECTION_TITLES = frozenset({"AI 一人公司（OPC）", "🚀 AI 一人公司（OPC）"})


def classify(text: str) -> str:
    for name, keywords in RULES:
        if any(k in text for k in keywords):
            return name
    return FALLBACK


def latin_tokens(text: str) -> set:
    return {t.lower() for t in RE_LATIN_TOKEN.findall(text)}


def normalize_section_order(sections: list) -> list:
    """Present OPC before GitHub Trending without rewriting source artifacts.

    Rich artifacts use emoji-prefixed titles, while legacy artifacts use the
    same titles without emoji. Swapping the two positions (rather than moving
    either section) leaves every unrelated and attachment section untouched.
    """
    ordered = list(sections)
    github_index = next(
        (
            index
            for index, section in enumerate(ordered)
            if isinstance(section, dict)
            and section.get("title") in GITHUB_SECTION_TITLES
        ),
        None,
    )
    opc_index = next(
        (
            index
            for index, section in enumerate(ordered)
            if isinstance(section, dict) and section.get("title") in OPC_SECTION_TITLES
        ),
        None,
    )
    if github_index is not None and opc_index is not None and github_index < opc_index:
        ordered[github_index], ordered[opc_index] = (
            ordered[opc_index],
            ordered[github_index],
        )
    return ordered


# ---------------------------------------------------------------- basic parse
def parse_item(raw: str, links_db: dict) -> dict:
    sources = []
    flag = False
    text = raw

    # backfilled links first (keyed by the raw archived line)
    for s in links_db.get(raw, []):
        sources.append({"name": s["name"], "url": s["url"]})

    def link(m: re.Match) -> str:
        sources.append({"name": m.group(1), "url": m.group(2)})
        return m.group(1)

    text = RE_LINK.sub(link, text)

    if "单一来源" in text:
        flag = True
        text = (
            text.replace("，单一来源", "").replace("单一来源，", "")
            .replace("（单一来源）", "").replace("(单一来源)", "")
            .replace("单一来源", "")
        )

    m = RE_TRAIL_PAREN.search(text)
    if m and "消息" not in m.group(1) and "报道" not in m.group(1):
        tokens = [t.strip() for t in re.split(r"[/、，,；;·|]+", m.group(1)) if t.strip()]
        chips = []
        ok = bool(tokens)
        for t in tokens:
            key = t.lower()
            if key in MEDIA:
                chips.append(MEDIA[key])
            elif RE_DOMAIN.match(t):
                chips.append(t)
            elif RE_DATEISH.match(t):
                continue
            else:
                ok = False
                break
        if ok and chips:
            text = text[: m.start()].rstrip(" ，,。;；")
            have = {s["name"] for s in sources}
            for c in dict.fromkeys(chips):
                if c not in have:
                    sources.append({"name": c})

    return {"text": text.strip(), "flag": flag, "sources": sources}


def parse_reported(md: str, links_db: dict) -> list:
    reports = []
    cur = None
    sequences = {}
    for raw in md.splitlines():
        line = raw.strip()
        m = RE_HEAD.match(line)
        if m:
            date = m.group(1)
            sequences[date] = sequences.get(date, 0) + 1
            cur = {
                "date": date,
                "label": m.group(2),
                "raw_items": [],
                "rich": False,
                "_sequence": sequences[date],
            }
            reports.append(cur)
            continue
        m = RE_ITEM.match(line)
        if m and cur is not None:
            cur["raw_items"].append(m.group(2).strip())

    for r in reports:
        modules = {}
        for raw in r["raw_items"]:
            item = parse_item(raw, links_db)
            modules.setdefault(classify(item["text"]), []).append(item)
        r["sections"] = [{"title": k, "items": v} for k, v in modules.items()]
    return reports


# ---------------------------------------------------------------- rich parse
def normalize_artifact(a: dict, artifact_name: str) -> Optional[dict]:
    """Turn a repository artifact into an issue dict."""
    date = str(a.get("date", "")).split()[0]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return None
    sections = []
    for s in a.get("sections", []):
        items = []
        for it in s.get("items", []):
            sources = []
            flag = False
            for x in it.get("sources", []):
                name = str(x.get("name", "")).strip()
                if not name:
                    continue
                if "单一来源" in name:
                    flag = True
                    name = name.replace("（单一来源）", "").replace("(单一来源)", "").strip()
                entry = {"name": name}
                if x.get("url"):
                    entry["url"] = x["url"]
                sources.append(entry)
            item = {
                "text": it.get("headline", ""),
                "summary": it.get("summary", ""),
                "expanded": bool(it.get("expanded")),
                "flag": flag,
                "sources": sources,
            }
            priority_ids = it.get("priorityIds")
            if isinstance(priority_ids, list) and priority_ids:
                item["priorityIds"] = priority_ids
            items.append(item)
        sec = {"title": s.get("title", ""), "items": items}
        if s.get("note"):
            sec["note"] = s["note"]
        sections.append(sec)
    issue = {
        "date": date,
        "generatedAt": a.get("generatedAt", ""),
        "oneLiner": a.get("oneLiner", ""),
        "sections": sections,
        "startedAt": a.get("generatedAt") or f"{date}T00:00:00",
        "rich": True,
    }
    if a.get("kind") == "addendum":
        issue["kind"] = "addendum"
    artifact_match = RE_NATIVE_ARTIFACT.fullmatch(artifact_name)
    if artifact_match and artifact_match.group("date") == date:
        issue["_sequence"] = int(artifact_match.group("sequence"))
    if a.get("label"):
        issue["label"] = str(a["label"]).strip()
    if a.get("attachTo"):
        # Supplement: these sections belong to another day's issue, not this one.
        # Used for sources whose content carries its own original date (WayToAGI),
        # so a piece published on 8/5 is filed under 8/5 even when we report it later.
        target = str(a["attachTo"]).strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", target):
            print(f"  ! ignoring bad attachTo {target!r} in {artifact_name}", file=sys.stderr)
        else:
            issue["attachTo"] = target
            issue["date"] = target
    return issue


def issue_sort_key(issue: dict) -> tuple:
    """Sort same-day issues by their durable issue sequence when available."""
    sequence = issue.get("_sequence")
    if isinstance(sequence, int):
        return (issue["date"], 0, sequence, issue.get("startedAt") or "")
    return (issue["date"], 1, 0, issue.get("startedAt") or "")


def attachment_target(day_issues: list) -> dict:
    """Keep general attachments on a day's main issue, not its addendum."""
    return next(
        (issue for issue in reversed(day_issues) if issue.get("kind") != "addendum"),
        day_issues[-1],
    )


def parse_artifacts() -> list:
    """Read plain artifact JSON committed under content/artifacts."""
    out = []
    if not ARTIFACT_DIR.is_dir():
        return out
    for src in sorted(ARTIFACT_DIR.glob("*.json")):
        try:
            artifact = json.loads(src.read_text("utf-8"))
        except Exception:
            print(f"  ! skipped unparseable artifact: {src.name}", file=sys.stderr)
            continue
        if not isinstance(artifact, dict) or not artifact.get("sections"):
            print(f"  ! skipped artifact without sections: {src.name}", file=sys.stderr)
            continue
        issue = normalize_artifact(artifact, src.name)
        if issue is None:
            print(f"  ! skipped artifact with bad date: {src.name}", file=sys.stderr)
            continue
        out.append(issue)
    return out


def match_label(artifact: dict, reported: list) -> tuple:
    """Return (label, matched_report_or_None) for an artifact issue.

    A native artifact carries its own label (e.g. "第九期"); the archive heading
    for the same issue uses the same label, so match on (date, label) exactly and
    skip the token-overlap heuristic, which is unreliable for mostly-Chinese items.
    """
    if artifact.get("label"):
        for r in reported:
            if r["date"] == artifact["date"] and r["label"] == artifact["label"]:
                return artifact["label"], r
        return artifact["label"], None

    a_tokens = set()
    for s in artifact["sections"]:
        for it in s["items"]:
            a_tokens |= latin_tokens(it["text"] + " " + it.get("summary", ""))
    best, best_score = None, 0
    for r in reported:
        if r["date"] != artifact["date"]:
            continue
        r_tokens = set()
        for raw in r["raw_items"]:
            r_tokens |= latin_tokens(raw)
        score = len(a_tokens & r_tokens)
        if score > best_score:
            best, best_score = r, score
    if best is not None and best_score >= 3:
        return best["label"], best
    hhmm = artifact["generatedAt"][11:16] if len(artifact["generatedAt"]) >= 16 else ""
    return (f"更新 {hhmm}" if hhmm else "看板日报"), None


def apply_attachments(issues: list, attachments: list) -> list:
    """Fold each attachment's sections into the issue for its attachTo date.

    Sections with a title the target issue already has are merged into it, so a
    day never grows two headers with the same name. When the target date has no
    issue at all (WayToAGI publishes on days we ran no digest), the attachment is
    promoted to a standalone issue of its own so the content still gets a date.
    """
    if not attachments:
        return issues

    by_date = {}
    for r in issues:
        by_date.setdefault(r["date"], []).append(r)

    promoted = []
    for a in attachments:
        target_day = by_date.get(a["date"])
        if not target_day:
            a.pop("attachTo", None)
            a.setdefault("label", "补录")
            promoted.append(a)
            by_date.setdefault(a["date"], []).append(a)
            continue
        target = attachment_target(target_day)
        existing = {s["title"]: s for s in target["sections"]}
        for sec in a["sections"]:
            hit = existing.get(sec["title"])
            if hit is None:
                target["sections"].append(sec)
                existing[sec["title"]] = sec
            else:
                hit["items"].extend(sec["items"])
                if sec.get("note") and not hit.get("note"):
                    hit["note"] = sec["note"]

    if promoted:
        issues = issues + promoted
        issues.sort(key=issue_sort_key)
    return issues


def surface_latest_waytoagi(issues: list, attachments: list) -> list:
    """Also show the newest WayToAGI issue on the site's latest report.

    Attachments remain filed under their original publication date, but the
    upstream mirror often trails our daily report by a day or two. Without this
    small latest-view copy, a successful late sync is effectively invisible on
    the homepage and looks like a failed crawl.
    """
    candidates = [
        (attachment, section)
        for attachment in attachments
        for section in attachment.get("sections", [])
        if section.get("title") == WAYTOAGI_SECTION
    ]
    if not issues or not candidates:
        return issues

    attachment, section = max(
        candidates,
        key=lambda pair: (pair[0].get("date", ""), pair[0].get("generatedAt", "")),
    )
    latest_date = issues[-1]["date"]
    latest = attachment_target(
        [issue for issue in issues if issue["date"] == latest_date]
    )
    if any(item.get("title") == WAYTOAGI_SECTION for item in latest.get("sections", [])):
        return issues

    surfaced = deepcopy(section)
    source_date = attachment["date"]
    month = int(source_date[5:7])
    day = int(source_date[8:10])
    surfaced["note"] = (
        f"WayToAGI 上游最新可用一期为 {month}/{day}，已完整同步 "
        f"{len(surfaced.get('items', []))} 条；内容同时按原文日期归档。"
    )
    latest["sections"].append(surfaced)
    return issues


def main() -> int:
    links_db = json.loads(LINKS_FILE.read_text("utf-8")) if LINKS_FILE.is_file() else {}

    reported = []
    if REPORTED_FILE.is_file():
        reported = parse_reported(REPORTED_FILE.read_text("utf-8"), links_db)

    artifacts = parse_artifacts()
    artifacts.sort(key=issue_sort_key)

    attachments = [a for a in artifacts if a.get("attachTo")]
    artifacts = [a for a in artifacts if not a.get("attachTo")]

    replaced_ids = set()
    issues = []
    for a in artifacts:
        label, matched = match_label(a, reported)
        if matched is not None:
            replaced_ids.add(id(matched))
            # Legacy artifact names did not encode a sequence. Preserve the
            # matching archive heading's position so they cannot reorder a day.
            a.setdefault("_sequence", matched.get("_sequence"))
        a["label"] = label
        issues.append(a)
    for r in reported:
        if id(r) not in replaced_ids:
            r["oneLiner"] = ""
            issues.append(r)

    issues.sort(key=issue_sort_key)
    issues = apply_attachments(issues, attachments)
    issues = surface_latest_waytoagi(issues, attachments)
    for issue in issues:
        issue["sections"] = normalize_section_order(issue.get("sections", []))

    # per-day seq, slugs, global issue numbers, weekday
    seen = {}
    for i, r in enumerate(issues, start=1):
        seen[r["date"]] = seen.get(r["date"], 0) + 1
        seq = seen[r["date"]]
        dt = datetime.strptime(r["date"], "%Y-%m-%d")
        r["issue"] = i
        r["seq"] = seq
        r["slug"] = f"{r['date']}--{seq}"
        r["weekday"] = WEEKDAYS[dt.weekday()]
        r.pop("raw_items", None)
        r.pop("startedAt", None)
        r.pop("_sequence", None)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generatedAt": max(
            (r.get("generatedAt", "") for r in issues),
            default="",
        ),
        "count": len(issues),
        "reports": issues,
    }
    OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    rich = sum(1 for r in issues if r.get("rich"))
    print(f"built {len(issues)} issue(s) ({rich} rich) -> {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
