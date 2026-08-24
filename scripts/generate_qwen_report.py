#!/usr/bin/env python3
"""Generate the researched daily edition with Qwen3.7 Plus.

The model never writes repository files directly.  This program performs a
three-stage call instead:

1. Qwen Responses API discovers recent evidence with built-in web search;
   this program then fetches each selected public source directly.
2. Qwen Chat Completions turns the frozen evidence cards into strict JSON.
3. A fresh Qwen call audits every drafted claim against the cited frozen
   evidence and rejects the whole draft if any claim is unsupported.

Only evidence-backed model fields are compiled into the repository artifact.
Source URLs are copied from trusted collectors or returned tool metadata, not
from the editor's prose.  Any API, grounding, schema, or quality failure exits
non-zero so the outer workflow can publish the deterministic recovery edition.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import ipaddress
import json
import math
import os
import re
import socket
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_fallback_report as fallback  # noqa: E402


ROOT = SCRIPT_DIR.parent
DEFAULT_ARTIFACT_DIR = ROOT / "content" / "artifacts"
DEFAULT_REPORTED_FILE = ROOT / "content" / "reported.md"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-plus"
PROMPT_VERSION = "qwen-daily-v2"
QWEN_PLUS_INPUT_CNY_PER_MILLION = 2.0
QWEN_PLUS_CACHED_INPUT_CNY_PER_MILLION = 0.4
QWEN_PLUS_OUTPUT_CNY_PER_MILLION = 8.0
QWEN_WEB_SEARCH_CNY_PER_CALL = 0.004
PRICING_VERSION = "2026-08-24-cn-beijing"

SECTION_POLICY = (
    ("🔥 AI 重要事件", 3, 5),
    ("🎬 AI 创作 · 视频/音乐/媒体娱乐", 3, 4),
    ("🌍 海外观察", 3, 4),
    ("📄 论文与技术前沿", 2, 3),
    ("🚀 AI 一人公司（OPC）", 2, 3),
    ("💻 GitHub Trending", 4, 6),
)
SECTION_TITLES = tuple(title for title, _minimum, _maximum in SECTION_POLICY)
SECTION_RESEARCH_ANGLES = {
    SECTION_TITLES[0]: (
        "聚焦官方模型/产品发布、公司重大动态",
        "聚焦融资、并购、政策监管与算力产业变化",
    ),
    SECTION_TITLES[1]: (
        "聚焦 AI 视频、图像、音乐与创作工具正式更新",
        "聚焦影视/媒体实际案例、版权与创作变现",
    ),
    SECTION_TITLES[2]: (
        "聚焦海外 builder 原创观点与可验证的产品实践",
        "聚焦产业一线数据、商业模式与高信息量访谈",
    ),
    SECTION_TITLES[3]: (
        "聚焦近 72 小时 arXiv/实验室原论文与技术报告",
        "聚焦伴随代码仓库的新方法、评测与安全研究",
    ),
    SECTION_TITLES[4]: (
        "聚焦独立开发者/小团队的真实收入与工作流",
        "聚焦 Agent 基建、自动化运营与小团队产品化方法",
    ),
    SECTION_TITLES[5]: (
        "只核对今日 GitHub Trending 总榜中的 AI 仓库",
        "只核对今日 GitHub Trending Python 榜中的 AI 仓库",
    ),
}
MAPPING_SECTIONS = frozenset({SECTION_TITLES[2], SECTION_TITLES[4]})
SINGLE_SOURCE_SUFFIX = "（单一来源）"
URL_RE = re.compile(r"https?://[^\s\]\[<>{}\"']+")
ASCII_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z][A-Za-z0-9._-]*\d)"
    r"[A-Za-z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*",
    re.IGNORECASE,
)
ASCII_QUANTITY_RE = re.compile(
    r"(?P<currency>US\$|CN¥|RMB|USD|CNY|[$¥￥])?\s*"
    r"(?P<number>\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(?P<scale>万|亿|兆)?\s*"
    r"(?P<unit>"
    r"%|％|percent(?:age)?|tokens?|params?|parameters?|"
    r"(?:ki|mi|gi|ti|pi)?b|kb|mb|gb|tb|pb|"
    r"khz|mhz|ghz|thz|hz|ms|fps|px|dpi|"
    r"kbps|mbps|gbps|tbps|kw|mw|gw|w|"
    r"参数|令牌|字节|毫秒|秒|分钟|小时|天|年|"
    r"人|家|项|个|条|次|倍|层|张|篇|款|种|轮|位|台|元|美元|人民币"
    r")?",
    re.IGNORECASE,
)
CHINESE_QUANTITY_RE = re.compile(
    r"(?P<number>[零〇一二两三四五六七八九十百千万亿兆]+)\s*"
    r"(?P<unit>%|％|参数|令牌|字节|毫秒|秒|分钟|小时|天|年|"
    r"人|家|项|个|条|次|倍|层|张|篇|款|种|轮|位|台|元|美元|人民币)?"
)
CHINESE_PERCENT_RE = re.compile(
    r"百分之(?P<number>[0-9]+(?:\.[0-9]+)?|[零〇一二两三四五六七八九十百千万亿兆]+)"
)
GITHUB_REPO_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
REPORTED_ITEM_RE = re.compile(
    r"^-\s+(?P<date>\d{4}-\d{2}-\d{2})\s*\|\s*(?P<headline>.+?)\s*$"
)
SENSITIVE_HISTORY_RE = re.compile(
    r"weapon|kill|attack|exploit|zero.?day|biosecurity|adult|sexual|suicide|"
    r"武器|攻击|窃取|漏洞|生物安全|色情|裸|自杀",
    re.IGNORECASE,
)
CRITICAL_CLAIM_EQUIVALENTS = {
    "全球": ("全球", "global", "worldwide"),
    "无条件": ("无条件", "unconditional", "without restriction"),
    "全面": ("全面", "fully", "general availability", "generally available"),
    "正式上线": ("正式上线", "general availability", "generally available", "launched"),
    "已上线": ("已上线", "is available", "now available", "launched"),
    "首次": ("首次", "first"),
    "唯一": ("唯一", "only", "unique"),
    "最强": ("最强", "strongest", "best", "state-of-the-art"),
    "领先": ("领先", "leading", "outperform"),
    "免费": ("免费", "free"),
    "开源": ("开源", "open source", "open-source"),
    "收购": ("收购", "acquire", "acquisition"),
    "融资": ("融资", "funding", "raised", "financing"),
    "停止": ("停止", "stopped", "ceased", "discontinued"),
    "删除": ("删除", "deleted", "removed", "erased"),
    "关闭": ("关闭", "closed", "shut down", "shutdown"),
    "清除": ("清除", "cleared", "purged", "erased"),
}
CLAIM_ASCII_STOPWORDS = frozenset(
    {
        "agent",
        "agents",
        "api",
        "artificial",
        "intelligence",
        "github",
        "json",
        "llm",
        "model",
        "models",
        "mvp",
        "qwen",
        "trending",
    }
)


class QwenReportError(RuntimeError):
    """A safe, user-actionable generation failure."""


class QwenRequestOutcomeUnknown(QwenReportError):
    """The client lost the response, so the reserved cost remains consumed."""


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep the bearer token on the already validated API origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class GitHubTrendingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_repository_article = False
        self.article_depth = 0
        self.in_heading = False
        self.repositories: set[str] = set()

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "article" and "Box-row" in classes:
            self.in_repository_article = True
            self.article_depth = 1
            return
        if not self.in_repository_article:
            return
        self.article_depth += 1
        if tag == "h2":
            self.in_heading = True
        if tag == "a" and self.in_heading:
            href = attributes.get("href") or ""
            candidate = normalize_url(f"https://github.com{href}")
            if is_github_repository_url(candidate):
                self.repositories.add(candidate)

    def handle_endtag(self, tag: str) -> None:
        if not self.in_repository_article:
            return
        if tag == "h2":
            self.in_heading = False
        self.article_depth -= 1
        if self.article_depth <= 0:
            self.in_repository_article = False
            self.in_heading = False


class PageTextParser(HTMLParser):
    """Small deterministic HTML-to-text extractor for public source pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        if tag == "meta":
            attributes = {key.lower(): value for key, value in attrs}
            name = (attributes.get("name") or attributes.get("property") or "").lower()
            if name in {
                "description",
                "og:title",
                "og:description",
                "article:published_time",
                "date",
                "datepublished",
            }:
                content = attributes.get("content")
                if content:
                    self.parts.append(content)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self.ignored_depth = max(0, self.ignored_depth - 1)

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth and data.strip():
            self.parts.append(data)


def fetch_github_trending_repositories(timeout: int = 30) -> set[str]:
    repositories: set[str] = set()
    for url in (
        "https://github.com/trending?since=daily",
        "https://github.com/trending/python?since=daily",
    ):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "evan-ai-daily-report/2.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                html = response.read().decode("utf-8", errors="replace")
        except (OSError, TimeoutError, urllib.error.HTTPError) as exc:
            raise QwenReportError(f"cannot fetch GitHub Trending: {exc}") from exc
        parser = GitHubTrendingParser()
        parser.feed(html)
        repositories.update(parser.repositories)
    if len(repositories) < 4:
        raise QwenReportError(
            f"GitHub Trending parser found only {len(repositories)} repositories"
        )
    return repositories


def validate_public_source_url(value: str) -> str:
    normalized = normalize_url(value)
    if not normalized:
        raise QwenReportError("source URL is malformed")
    parsed = urlsplit(normalized)
    if parsed.username is not None or parsed.password is not None:
        raise QwenReportError("source URL must not contain credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise QwenReportError("source URL has an invalid port") from exc
    if port not in {None, 80, 443}:
        raise QwenReportError("source URL uses a non-web port")
    host = (parsed.hostname or "").rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".local", ".internal")
    ):
        raise QwenReportError("source URL is not public")
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            resolved = socket.getaddrinfo(
                host,
                port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise QwenReportError("source hostname could not be resolved") from exc
        addresses = []
        for record in resolved:
            try:
                addresses.append(ipaddress.ip_address(record[4][0].split("%", 1)[0]))
            except ValueError:
                continue
    if not addresses or any(not address.is_global for address in addresses):
        raise QwenReportError("source URL resolved to a non-public address")
    return normalized


def fetch_public_page(url: str, timeout: int = 30) -> dict[str, str]:
    trusted_url = validate_public_source_url(url)
    request = urllib.request.Request(
        trusted_url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9",
            "User-Agent": "evan-ai-daily-report/2.0",
        },
    )
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type().lower()
            if content_type not in {
                "text/html",
                "application/xhtml+xml",
                "text/plain",
                "application/json",
                "application/ld+json",
                "application/xml",
                "text/xml",
            }:
                raise QwenReportError("source page has an unsupported content type")
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise QwenReportError("source page exceeds the 2 MB limit")
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise QwenReportError(f"source page returned HTTP {exc.code}") from exc
    except (OSError, TimeoutError) as exc:
        raise QwenReportError("source page request failed") from exc
    try:
        decoded = raw.decode(charset, errors="replace")
    except LookupError:
        decoded = raw.decode("utf-8", errors="replace")
    if content_type in {"text/html", "application/xhtml+xml"}:
        parser = PageTextParser()
        parser.feed(decoded)
        text = clean_text(" ".join(parser.parts), 2500)
    else:
        text = clean_text(decoded, 2500)
    if len(text) < 120:
        raise QwenReportError("source page yielded too little readable text")
    return {
        "url": trusted_url,
        "outputSummary": text,
        "outputSha256": hashlib.sha256(raw).hexdigest(),
    }


def is_allowed_dashscope_host(host: str) -> bool:
    """Return whether *host* is an Alibaba Model Studio API hostname."""

    normalized = host.rstrip(".").lower()
    if normalized in {
        "dashscope.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
        "dashscope-us.aliyuncs.com",
    }:
        return True
    return normalized.endswith(".dashscope.aliyuncs.com") or normalized.endswith(
        ".maas.aliyuncs.com"
    )


def validate_base_url(value: Any) -> str:
    """Validate and normalize the only origins allowed to receive the API key."""

    if not isinstance(value, str) or not value.strip():
        raise QwenReportError("DashScope base URL is empty")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise QwenReportError("DashScope base URL is malformed") from exc
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme.lower() != "https":
        raise QwenReportError("DashScope base URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise QwenReportError("DashScope base URL must not contain credentials")
    if not is_allowed_dashscope_host(host):
        raise QwenReportError(
            "DashScope base URL host must be DashScope or *.maas.aliyuncs.com"
        )
    if port not in {None, 443}:
        raise QwenReportError("DashScope base URL may only use HTTPS port 443")
    if parsed.query or parsed.fragment:
        raise QwenReportError("DashScope base URL must not contain a query or fragment")
    path = re.sub(r"/{2,}", "/", parsed.path or "").rstrip("/")
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit(("https", netloc, path, "", ""))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Shanghai date, YYYY-MM-DD")
    parser.add_argument("--generated-at", required=True, help="Frozen collector time")
    parser.add_argument("--builders", type=Path, required=True)
    parser.add_argument("--priority", type=Path, required=True)
    parser.add_argument("--artificial-analysis", type=Path, required=True)
    parser.add_argument("--waytoagi", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--reported", type=Path, default=DEFAULT_REPORTED_FILE)
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument(
        "--base-url",
        default=os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL),
    )
    parser.add_argument(
        "--model", default=os.getenv("DAILY_REPORT_MODEL", DEFAULT_MODEL)
    )
    parser.add_argument("--research-timeout", type=int, default=900)
    parser.add_argument("--editor-timeout", type=int, default=600)
    parser.add_argument(
        "--cost-cap-cny",
        type=float,
        default=float(os.getenv("DAILY_REPORT_COST_CAP_CNY", "1.0")),
        help="Reject the model draft when its nominal token cost exceeds this cap",
    )
    return parser.parse_args(argv)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text("utf-8"))
    except Exception as exc:
        raise QwenReportError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise QwenReportError(f"{path} must contain a JSON object")
    return value


def attachment_exclusions(
    artificial_analysis: dict[str, Any], waytoagi: dict[str, Any]
) -> list[dict[str, Any]]:
    """Freeze deterministic attachment events that the main report must skip."""

    records: list[dict[str, Any]] = []

    def add(title: Any, urls: Iterable[Any]) -> None:
        clean_title = clean_text(title, 320)
        clean_urls = []
        for raw_url in urls:
            normalized = normalize_url(str(raw_url or ""))
            if normalized and normalized not in clean_urls:
                clean_urls.append(normalized)
        if clean_title or clean_urls:
            records.append({"title": clean_title, "urls": clean_urls})

    for change in artificial_analysis.get("changes") or []:
        if isinstance(change, dict):
            add(
                change.get("headline")
                or change.get("title")
                or change.get("summary")
                or change.get("type"),
                [change.get("url"), *(change.get("evidenceUrls") or [])],
            )
    artifact = artificial_analysis.get("artifact")
    document = artifact.get("document") if isinstance(artifact, dict) else None
    for section in document.get("sections") or [] if isinstance(document, dict) else []:
        if not isinstance(section, dict):
            continue
        for item in section.get("items") or []:
            if not isinstance(item, dict):
                continue
            add(
                item.get("headline") or item.get("title"),
                [
                    source_item.get("url")
                    for source_item in item.get("sources") or []
                    if isinstance(source_item, dict)
                ],
            )

    for issue in waytoagi.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        issue_url = issue.get("sourceUrl")
        for item in issue.get("items") or []:
            if isinstance(item, dict):
                add(item.get("title"), [issue_url, item.get("url")])
    return records[:500]


def normalized_event_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", clean_text(value, 1000).lower())


def matches_attachment_event(
    title: str, facts: str, excluded_titles: Iterable[str]
) -> bool:
    candidate = normalized_event_text(f"{title} {facts}")
    if not candidate:
        return False
    for excluded in excluded_titles:
        normalized = normalized_event_text(excluded)
        if len(normalized) >= 8 and normalized in candidate:
            return True
    return False


def write_diagnostics(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fallback.atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )


def api_post(
    base_url: str,
    endpoint: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    trusted_base_url = validate_base_url(base_url)
    url = f"{trusted_base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "evan-ai-daily-report/2.0",
        },
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(NoRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            error_document = json.loads(
                exc.read(4000).decode("utf-8", errors="replace")
            )
            if isinstance(error_document, dict):
                error_object = error_document.get("error")
                if isinstance(error_object, dict):
                    code = clean_text(error_object.get("code"), 80)
                    message = clean_text(error_object.get("message"), 300)
                else:
                    code = clean_text(error_document.get("code"), 80)
                    message = clean_text(error_document.get("message"), 300)
                safe_detail = clean_text(f"{code}: {message}".strip(": "), 380)
                safe_detail = safe_detail.replace(api_key, "[redacted]")
                safe_detail = re.sub(
                    r"sk-[A-Za-z0-9._-]{12,}", "[redacted]", safe_detail
                )
                if safe_detail:
                    detail = f" ({safe_detail})"
        except (json.JSONDecodeError, OSError):
            pass
        raise QwenReportError(
            f"DashScope {endpoint} returned HTTP {exc.code}{detail}"
        ) from exc
    except (OSError, TimeoutError) as exc:
        raise QwenRequestOutcomeUnknown(
            f"DashScope {endpoint} request outcome is unknown"
        ) from exc
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise QwenReportError(
            f"DashScope {endpoint} returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise QwenReportError(f"DashScope {endpoint} returned a non-object response")
    return value


def response_matches_model(returned: Any, requested: str) -> bool:
    if not isinstance(returned, str) or not returned:
        return False
    return returned == requested or returned.startswith(f"{requested}-")


def usage_cost_cny(usage: Any) -> float:
    if not isinstance(usage, dict):
        raise QwenReportError("model response is missing usage")
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    if not isinstance(input_tokens, int) or input_tokens < 0:
        raise QwenReportError("model usage is missing input tokens")
    if not isinstance(output_tokens, int) or output_tokens < 0:
        raise QwenReportError("model usage is missing output tokens")
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cached_tokens = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
    if not isinstance(cached_tokens, int) or not 0 <= cached_tokens <= input_tokens:
        cached_tokens = 0
    uncached_tokens = input_tokens - cached_tokens
    return (
        uncached_tokens * QWEN_PLUS_INPUT_CNY_PER_MILLION
        + cached_tokens * QWEN_PLUS_CACHED_INPUT_CNY_PER_MILLION
        + output_tokens * QWEN_PLUS_OUTPUT_CNY_PER_MILLION
    ) / 1_000_000


def web_search_count(response: dict[str, Any]) -> int:
    usage = response.get("usage")
    x_tools = usage.get("x_tools") if isinstance(usage, dict) else None
    web_search = x_tools.get("web_search") if isinstance(x_tools, dict) else None
    count = web_search.get("count") if isinstance(web_search, dict) else None
    if isinstance(count, int) and count >= 0:
        return count
    return sum(
        isinstance(item, dict) and item.get("type") == "web_search_call"
        for item in response.get("output") or []
    )


def response_metadata(
    response: dict[str, Any], requested_model: str, *, include_web_search: bool = False
) -> dict[str, Any]:
    returned_model = response.get("model")
    if not response_matches_model(returned_model, requested_model):
        raise QwenReportError(
            f"response model {returned_model!r} does not match {requested_model!r}"
        )
    request_id = response.get("id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise QwenReportError("model response is missing a request id")
    usage = response.get("usage")
    token_cost = usage_cost_cny(usage)
    search_count = web_search_count(response) if include_web_search else 0
    search_cost = search_count * QWEN_WEB_SEARCH_CNY_PER_CALL
    estimated_cost = token_cost + search_cost
    return {
        "requestId": request_id,
        "model": returned_model,
        "usage": usage,
        "pricingVersion": PRICING_VERSION,
        "tokenCostCny": round(token_cost, 8),
        "webSearchCount": search_count,
        "webSearchCostCny": round(search_cost, 8),
        "estimatedCostCny": round(estimated_cost, 8),
    }


def normalize_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.rstrip(".").lower()
    port = parsed.port
    if port and not (
        parsed.scheme.lower() == "http" and port == 80
    ) and not (parsed.scheme.lower() == "https" and port == 443):
        host = f"{host}:{port}"
    clean_query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"ref", "source", "campaign"}
        ],
        doseq=True,
    )
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), host, path, clean_query, ""))


def is_artificial_analysis_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").rstrip(".").lower()
    return host == "artificialanalysis.ai" or host.endswith(
        ".artificialanalysis.ai"
    )


def is_reserved_attachment_url(value: str) -> bool:
    """Keep deterministic AA and WayToAGI sources out of model evidence."""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").rstrip(".").lower()
    return (
        is_artificial_analysis_url(value)
        or host == "waytoagi.com"
        or host.endswith(".waytoagi.com")
        or host == "waytoagi.feishu.cn"
    )


def is_github_repository_url(value: str) -> bool:
    """Accept only a canonical github.com/owner/repository page URL."""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if (parsed.hostname or "").rstrip(".").lower() != "github.com":
        return False
    if parsed.query or parsed.fragment:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or any(not GITHUB_REPO_PART_RE.fullmatch(part) for part in parts):
        return False
    return not parts[1].lower().endswith(".git")


def clean_text(value: Any, limit: int = 1600) -> str:
    if not isinstance(value, str):
        return ""
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip(" ,.;:，。；：") + "…"


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        for pattern in ("%b %d, %Y", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value.strip(), pattern)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def text_supports_publication_date(text: str, published_time: datetime) -> bool:
    day = published_time.astimezone(timezone.utc)
    month_short = day.strftime("%b")
    month_long = day.strftime("%B")
    patterns = (
        day.strftime("%Y-%m-%d"),
        day.strftime("%Y/%m/%d"),
        f"{day.year}年{day.month}月{day.day}日",
        f"{month_short} {day.day}, {day.year}",
        f"{month_long} {day.day}, {day.year}",
        f"{day.day} {month_short} {day.year}",
        f"{day.day} {month_long} {day.year}",
    )
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def source(name: Any, url: Any) -> dict[str, str] | None:
    clean_url = normalize_url(str(url or ""))
    if not clean_url or is_reserved_attachment_url(clean_url):
        return None
    clean_name = clean_text(name, 160) or (urlsplit(clean_url).hostname or "来源")
    clean_name = clean_name.removesuffix(SINGLE_SOURCE_SUFFIX)
    return {"name": clean_name, "url": clean_url}


def priority_cards(
    document: dict[str, Any], covered_priority_ids: set[str]
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for candidate in document.get("candidates") or []:
        if not isinstance(candidate, dict) or not candidate.get("required"):
            continue
        candidate_id = clean_text(candidate.get("id"), 240)
        if not candidate_id or candidate_id in covered_priority_ids:
            continue
        urls = []
        for raw_url in [candidate.get("url"), *(candidate.get("evidenceUrls") or [])]:
            item = source(candidate.get("officialSource") or "官方来源", raw_url)
            if item and item["url"] not in {entry["url"] for entry in urls}:
                urls.append(item)
        if not urls:
            raise QwenReportError(f"required priority candidate {candidate_id} has no URL")
        title = clean_text(candidate.get("title"), 300)
        facts = " ".join(
            part
            for part in (
                clean_text(candidate.get("summary"), 800),
                clean_text(candidate.get("details"), 1600),
                "必须逐字覆盖匹配词："
                + "、".join(
                    clean_text(term, 80) for term in candidate.get("matchTerms") or []
                ),
            )
            if part
        )
        cards.append(
            {
                "id": f"P{len(cards) + 1:03d}",
                "section": SECTION_TITLES[0],
                "title": title or candidate_id,
                "facts": facts,
                "publishedAt": clean_text(candidate.get("publishedAt"), 80),
                "sources": urls,
                "priorityIds": [candidate_id],
                "matchTerms": [
                    clean_text(term, 80)
                    for term in candidate.get("matchTerms") or []
                    if clean_text(term, 80)
                ],
            }
        )
    return cards


def builder_cards(
    document: dict[str, Any], report_day: datetime
) -> list[dict[str, Any]]:
    cutoff = report_day.replace(tzinfo=timezone.utc) - timedelta(hours=72)
    cards: list[dict[str, Any]] = []

    def add_card(
        title: Any,
        facts: Any,
        published_at: Any,
        source_name: Any,
        source_url: Any,
        section: str,
    ) -> None:
        parsed = parse_datetime(published_at)
        if parsed is not None and parsed < cutoff:
            return
        item_source = source(source_name, source_url)
        text = clean_text(facts, 1800)
        if item_source is None or len(text) < 24:
            return
        cards.append(
            {
                "id": f"B{len(cards) + 1:03d}",
                "section": section,
                "title": clean_text(title, 300) or clean_text(facts, 140),
                "facts": text,
                "publishedAt": clean_text(published_at, 80),
                "sources": [item_source],
                "priorityIds": [],
                "matchTerms": [],
            }
        )

    for account in document.get("x") or []:
        if not isinstance(account, dict):
            continue
        name = clean_text(account.get("name"), 120)
        handle = clean_text(account.get("handle"), 80)
        bio = clean_text(account.get("bio"), 240)
        source_name = f"{name or handle} 的 X 帖文"
        for tweet in account.get("tweets") or []:
            if not isinstance(tweet, dict):
                continue
            text = clean_text(tweet.get("text"), 1800)
            section = SECTION_TITLES[2]
            lowered = f"{text} {bio}".lower()
            if any(term in lowered for term in ("video", "music", "film", "image")):
                section = SECTION_TITLES[1]
            elif any(
                term in lowered
                for term in ("revenue", "saas", "founder", "startup", "ship", "product")
            ):
                section = SECTION_TITLES[4]
            add_card(
                f"{name or '@' + handle}：{clean_text(text, 140)}",
                f"作者背景：{bio}。原帖：{text}",
                tweet.get("createdAt"),
                source_name,
                tweet.get("url"),
                section,
            )

    for podcast in document.get("podcasts") or []:
        if not isinstance(podcast, dict):
            continue
        add_card(
            podcast.get("title"),
            podcast.get("transcript") or podcast.get("description"),
            podcast.get("publishedAt"),
            podcast.get("name") or "播客",
            podcast.get("url"),
            SECTION_TITLES[2],
        )

    for blog in document.get("blogs") or []:
        if not isinstance(blog, dict):
            continue
        add_card(
            blog.get("title"),
            blog.get("content") or blog.get("description"),
            blog.get("publishedAt"),
            blog.get("name") or "官方博客",
            blog.get("url"),
            SECTION_TITLES[2],
        )
    return cards[:80]


def reported_history(path: Path, report_day: datetime) -> list[dict[str, str]]:
    if not path.exists():
        return []
    cutoff = report_day.date() - timedelta(days=5)
    entries: list[dict[str, str]] = []
    for line in path.read_text("utf-8").splitlines():
        match = REPORTED_ITEM_RE.fullmatch(line.strip())
        if match is None:
            continue
        try:
            item_day = datetime.strptime(match.group("date"), "%Y-%m-%d").date()
        except ValueError:
            continue
        if item_day < cutoff:
            continue
        if SENSITIVE_HISTORY_RE.search(match.group("headline")):
            continue
        entries.append(
            {"date": match.group("date"), "headline": match.group("headline")}
        )
    return entries[-500:]


def research_prompt(
    date_value: str,
    generated_at: str,
    seeds: list[dict[str, Any]],
    history: list[dict[str, str]],
    section_title: str,
    target_minimum: int,
    target_maximum: int,
    excluded_attachment_items: list[dict[str, Any]] | None = None,
    focus: str = "",
) -> str:
    compact_seeds = [
        {
            "id": card["id"],
            "section": card["section"],
            "title": card["title"],
            "facts": card["facts"],
            "publishedAt": card["publishedAt"],
            "sources": card["sources"],
            "priorityIds": card["priorityIds"],
            "matchTerms": card["matchTerms"],
        }
        for card in seeds
    ]
    return f"""你是 Evan 的 AI 日报研究员。冻结日期为 {date_value}（Asia/Shanghai），采集时刻为 {generated_at}。

本次调用只研究板块“{section_title}”。本轮检索角度：{focus or '按板块定义全面检索'}。必须使用 web_search 检索近 24 小时的新信息；若不足，可扩展到 72 小时，但不得用旧闻凑数。为后续编辑准备 {target_minimum}–{target_maximum} 张互不重复的独立证据卡。evidence 里的每一个 sources URL 必须是本轮 web_search 真实返回的精确页面；程序会在模型调用后直接读取该页面，无法读取或原文不支持的卡会被丢弃。板块定义如下：
- 🔥 AI 重要事件：模型正式发布、重大公司动态、监管或融资，优先官方原文；
- 🎬 AI 创作：视频、音乐、影视、媒体娱乐、版权与创作工具；
- 🌍 海外观察：高信息量 builder 观点、产业和真实产品案例；
- 📄 论文与技术前沿：原论文、项目仓库或实验室技术报告；
- 🚀 AI 一人公司（OPC）：真实变现、工作流、Agent 基建与小团队方法；
- 💻 GitHub Trending：当天 trending 中的具体 AI 仓库，链接必须是仓库页。

硬约束：
1. Artificial Analysis 与 WayToAGI 由外层确定性采集，不要放进主日报证据。deterministicAttachmentExclusions 中列出的事件和 URL 也不得通过二手来源再次收录。
2. 每张卡只写一个事件；事实必须能由该卡列出的具体原文 URL 直接支撑。
3. 优先一手来源；关键数字和强结论尽量增加第二个可靠来源。来源 URL 必须是本轮搜索或抽取实际访问到的 item-specific 页面，禁止首页、栏目页、搜索页和猜测 URL。
4. 不得重复 history 中已报道事件；只有出现新价格、新裁决、新数据或正式发布等实质进展才可再次列入，并在 facts 写清新增点。
5. seedEvidence 中 priorityIds 非空的卡是强制候选，必须保留其 id、全部 matchTerms、官方 URL 与事实；其余 seed 仅是可靠线索，可交叉核验后采用。
6. 不做证据外推，不把“可能降低”写成“已经降低”，不把一次招聘写成既定战略。
7. 这是一份适合公开发布的商业科技简报；跳过成人、暴力、自伤、生物危害和可操作的网络攻击内容，安全类信息只保留非操作性的防御结论。
8. evidence 中每张卡的 section 必须逐字写成“{section_title}”，不要返回其他板块。

只输出一个 JSON 对象，不要 Markdown 或解释：
{{"evidence":[{{"section":"六个固定板块之一","title":"事件标题","facts":"1–3句可核验事实与必要限定","publishedAt":"ISO日期或日期","sources":[{{"name":"来源名","url":"具体原文URL"}}]}}]}}

seedEvidence:
{json.dumps(compact_seeds, ensure_ascii=False, separators=(',', ':'))}

history:
{json.dumps(history, ensure_ascii=False, separators=(',', ':'))}

deterministicAttachmentExclusions:
{json.dumps(excluded_attachment_items or [], ensure_ascii=False, separators=(',', ':'))}
"""


def response_output_text(response: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text)
    return "\n".join(chunks).strip()


def web_search_source_map(response: dict[str, Any]) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for item in response.get("output") or []:
        if (
            not isinstance(item, dict)
            or item.get("type") != "web_search_call"
            or item.get("status") != "completed"
        ):
            continue
        action = item.get("action")
        for candidate in action.get("sources") or [] if isinstance(action, dict) else []:
            if not isinstance(candidate, dict):
                continue
            normalized = normalize_url(str(candidate.get("url") or ""))
            if not normalized or is_reserved_attachment_url(normalized):
                continue
            found.setdefault(
                normalized,
                {
                    "name": clean_text(
                        candidate.get("title") or candidate.get("name"), 160
                    )
                    or (urlsplit(normalized).hostname or "网页来源"),
                    "url": normalized,
                },
            )
    return found


def direct_fetch_source_map(
    discovered_sources: dict[str, dict[str, str]],
    requested_urls: Iterable[str],
    timeout: int,
) -> dict[str, dict[str, str]]:
    selected: list[str] = []
    for raw_url in requested_urls:
        normalized = normalize_url(raw_url)
        if (
            normalized
            and normalized in discovered_sources
            and normalized not in selected
            and len(selected) < 18
        ):
            selected.append(normalized)
    hydrated: dict[str, dict[str, str]] = {}

    def fetch(url: str) -> tuple[str, dict[str, str] | None]:
        try:
            return url, fetch_public_page(url, timeout)
        except QwenReportError:
            return url, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        for url, page in executor.map(fetch, selected):
            if page is None:
                continue
            hydrated[url] = {
                "name": discovered_sources[url]["name"],
                "url": page["url"],
                "extractorOutputSummary": page["outputSummary"],
                "extractorOutputSha256": page["outputSha256"],
                "retrievalMethod": "direct-http",
            }
    return hydrated


def tool_source_map(response: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Return only URLs with a completed web-extractor output.

    Search results are useful discovery hints, but they are not proof that the
    model actually opened a page.  Each accepted URL therefore carries the
    provider's extractor summary and the hash of the full, unmodified output.
    """

    found: dict[str, dict[str, str]] = {}
    search_names = {
        url: item["name"] for url, item in web_search_source_map(response).items()
    }

    for item in response.get("output") or []:
        if (
            not isinstance(item, dict)
            or item.get("type") != "web_extractor_call"
            or item.get("status") != "completed"
        ):
            continue
        urls = item.get("urls")
        output = item.get("output")
        if (
            not isinstance(urls, list)
            or len(urls) != 1
            or not isinstance(output, str)
            or not output.strip()
        ):
            continue
        output_summary = clean_text(output, 6000)
        output_sha256 = hashlib.sha256(output.encode("utf-8")).hexdigest()
        for raw_url in urls:
            if not isinstance(raw_url, str):
                continue
            normalized = normalize_url(raw_url)
            if not normalized or is_reserved_attachment_url(normalized):
                continue
            found.setdefault(
                normalized,
                {
                    "name": search_names.get(normalized)
                    or (urlsplit(normalized).hostname or "网页来源"),
                    "url": normalized,
                    "extractorOutputSummary": output_summary,
                    "extractorOutputSha256": output_sha256,
                },
            )
    return found


def completed_extractor_call_count(response: dict[str, Any]) -> int:
    return sum(
        isinstance(item, dict)
        and item.get("type") == "web_extractor_call"
        and item.get("status") == "completed"
        and isinstance(item.get("urls"), list)
        and len(item["urls"]) == 1
        and isinstance(item.get("output"), str)
        and bool(item["output"].strip())
        for item in response.get("output") or []
    )


def extractor_audit_records(
    available_sources: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Group retained excerpts by their full-output hash for diagnostics."""

    grouped: dict[str, dict[str, Any]] = {}
    for item in available_sources.values():
        digest = item.get("extractorOutputSha256")
        summary = item.get("extractorOutputSummary")
        if not digest or not summary:
            continue
        record = grouped.setdefault(
            digest,
            {
                "outputSha256": digest,
                "outputSummary": clean_text(summary, 800),
                "urls": [],
            },
        )
        if item["url"] not in record["urls"]:
            record["urls"].append(item["url"])
    return list(grouped.values())


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise QwenReportError("model response is not one strict JSON object") from exc
    if not isinstance(value, dict):
        raise QwenReportError("model JSON must be an object")
    return value


def parse_research_document(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    candidates: list[Any] = []
    try:
        candidates.append(json.loads(stripped))
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(stripped):
        if character not in "[{":
            continue
        try:
            candidate, _end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        candidates.append(candidate)

    def evidence_list(value: Any) -> list[dict[str, Any]] | None:
        if isinstance(value, list) and value and all(
            isinstance(item, dict)
            and {"section", "title", "facts", "sources"}.issubset(item)
            for item in value
        ):
            return value
        if isinstance(value, dict):
            if {"section", "title", "facts", "sources"}.issubset(value):
                return [value]
            for key in ("evidence", "evidenceCards", "evidence_cards", "cards", "items", "results", "data"):
                if key not in value:
                    continue
                found = evidence_list(value[key])
                if found is not None:
                    return found
        return None

    for candidate in candidates:
        found = evidence_list(candidate)
        if found is not None:
            return {"evidence": found}
    raise QwenReportError("research response did not contain an evidence-card array")


def merge_research_cards(
    seed_cards: list[dict[str, Any]],
    research_document: dict[str, Any],
    available_sources: dict[str, dict[str, str]],
    reference_time: datetime | None = None,
    excluded_titles: Iterable[str] = (),
    trending_repositories: set[str] | None = None,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for original in seed_cards:
        if any(
            is_reserved_attachment_url(str(item.get("url") or ""))
            for item in original.get("sources") or []
            if isinstance(item, dict)
        ):
            if original.get("priorityIds"):
                raise QwenReportError(
                    f"priority seed {original.get('id')!r} uses a reserved attachment URL"
                )
            continue
        cards.append(json.loads(json.dumps(original, ensure_ascii=False)))
    source_to_card: dict[str, dict[str, Any]] = {}
    for card in cards:
        for item_source in card["sources"]:
            normalized = normalize_url(item_source["url"])
            if normalized:
                source_to_card[normalized] = card

    if reference_time is not None:
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=timezone.utc)
        reference_time = reference_time.astimezone(timezone.utc)
        oldest_allowed = reference_time - timedelta(hours=72)
    else:
        oldest_allowed = None

    evidence = research_document.get("evidence")
    if not isinstance(evidence, list):
        raise QwenReportError("research JSON must contain an evidence array")
    next_index = 1
    for raw_card in evidence:
        if not isinstance(raw_card, dict):
            continue
        section = raw_card.get("section")
        if section not in SECTION_TITLES:
            continue
        facts = clean_text(raw_card.get("facts"), 2200)
        title = clean_text(raw_card.get("title"), 320)
        if len(facts) < 36 or len(title) < 6:
            continue
        if matches_attachment_event(title, facts, excluded_titles):
            continue
        published_at = clean_text(raw_card.get("publishedAt"), 80)
        published_time = parse_datetime(published_at)
        if published_time is None:
            continue
        if reference_time is not None and not (
            oldest_allowed <= published_time <= reference_time
        ):
            continue

        raw_source_urls = [
            normalize_url(str(raw_source.get("url") or ""))
            for raw_source in raw_card.get("sources") or []
            if isinstance(raw_source, dict)
        ]
        if any(
            normalized and is_reserved_attachment_url(normalized)
            for normalized in raw_source_urls
        ):
            continue

        card_sources: list[dict[str, str]] = []
        extractor_outputs: list[dict[str, str]] = []
        for raw_source in raw_card.get("sources") or []:
            if not isinstance(raw_source, dict):
                continue
            normalized = normalize_url(str(raw_source.get("url") or ""))
            trusted = available_sources.get(normalized)
            if (
                trusted
                and not is_reserved_attachment_url(trusted["url"])
                and trusted.get("extractorOutputSummary")
                and re.fullmatch(
                    r"[0-9a-f]{64}", str(trusted.get("extractorOutputSha256") or "")
                )
                and (
                    section != SECTION_TITLES[5]
                    or is_github_repository_url(trusted["url"])
                )
                and trusted["url"] not in {
                    entry["url"] for entry in card_sources
                }
                and (
                    trusted.get("retrievalMethod") != "direct-http"
                    or section == SECTION_TITLES[5]
                    or text_supports_publication_date(
                        str(trusted.get("extractorOutputSummary") or ""),
                        published_time,
                    )
                )
            ):
                card_sources.append(
                    {"name": trusted["name"], "url": trusted["url"]}
                )
                extractor_outputs.append(
                    {
                        "url": trusted["url"],
                        "outputSummary": trusted["extractorOutputSummary"],
                        "outputSha256": trusted["extractorOutputSha256"],
                    }
                )
        if not card_sources:
            continue
        if section == SECTION_TITLES[5]:
            if not re.search(r"\btrending\b", facts, re.IGNORECASE):
                continue
            if len({item["url"] for item in card_sources}) != 1:
                continue
            if trending_repositories is not None and card_sources[0]["url"] not in trending_repositories:
                continue
        overlap = next(
            (
                source_to_card.get(normalize_url(item_source["url"]))
                for item_source in card_sources
                if normalize_url(item_source["url"]) in source_to_card
            ),
            None,
        )
        if overlap is not None:
            # Seed cards are frozen collector input.  A model-authored card that
            # reuses a seed URL must never append facts or sources to that seed.
            continue
        card = {
            "id": f"W{next_index:03d}",
            "section": section,
            "title": title,
            "facts": facts,
            "publishedAt": published_at,
            "sources": card_sources[:3],
            "extractorOutputs": extractor_outputs[:3],
            "priorityIds": [],
            "matchTerms": [],
        }
        next_index += 1
        cards.append(card)
        for item_source in card_sources:
            source_to_card[normalize_url(item_source["url"])] = card
    return cards


def item_schema(mode: str = "main") -> dict[str, Any]:
    expanded_schema: dict[str, Any] = {"type": "boolean"}
    return {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "summary": {"type": "string"},
            "expanded": expanded_schema,
            "evidenceIds": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 1 if mode == "addendum" else 3,
            },
        },
        "required": ["headline", "summary", "expanded", "evidenceIds"],
        "additionalProperties": False,
    }


def editor_schema(mode: str) -> dict[str, Any]:
    section_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "enum": list(SECTION_TITLES)},
            "items": {
                "type": "array",
                "items": item_schema(mode),
                "minItems": 1,
                "maxItems": 6,
            },
        },
        "required": ["title", "items"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "oneLiner": {"type": "string"},
            "sections": {
                "type": "array",
                "items": section_schema,
                "minItems": 1 if mode == "addendum" else 6,
                "maxItems": 1 if mode == "addendum" else 6,
            },
        },
        "required": ["oneLiner", "sections"],
        "additionalProperties": False,
    }


def editor_prompt(
    date_value: str, cards: list[dict[str, Any]], mode: str
) -> tuple[str, str]:
    system = """你是证据纪律极强的中文 AI 日报主编。只能依据用户给出的 evidence cards 写作；不得添加卡片没有的事实、数字、URL、人物身份、因果或收益。使用审慎限定语。标题和“对你的映射：”之前的事实叙述必须保留证据原文中的关键短语；中文证据必须逐分句做抽取式压缩，不得引入原文没有的相邻中文词组。英文证据的中文概括必须在每个事实分句中保留模型、公司或项目的英文实体名。输出必须严格符合 JSON Schema。"""
    card_payload = [
        {
            "id": card["id"],
            "section": card["section"],
            "title": card["title"] if not card["id"].startswith("W") else "",
            "facts": trusted_evidence_text(card),
            "publishedAt": card["publishedAt"],
            "sources": card["sources"],
            "extractorOutputs": card.get("extractorOutputs", []),
            "priorityIds": card["priorityIds"],
            "matchTerms": card["matchTerms"],
        }
        for card in cards
    ]
    if mode == "addendum":
        task = """今天主刊已经存在。只生成一个“🔥 AI 重要事件”补刊，覆盖全部给定强制候选；每条引用不同 evidenceId，expanded 全为 false，oneLiner 以“📌 补刊：”开头。"""
    else:
        task = """生成今天的完整主刊，要求：
- 恰好六个板块，顺序和 title 必须为：🔥 AI 重要事件、🎬 AI 创作 · 视频/音乐/媒体娱乐、🌍 海外观察、📄 论文与技术前沿、🚀 AI 一人公司（OPC）、💻 GitHub Trending。
- 各板块条数依次为 3–5、3–4、3–4、2–3、2–3、4–6，总计 20–28 条。
- 每个 evidenceId 全文只能使用一次；优先新鲜、一手、与 Evan 的 AI 创作/Agent/OPC 工作直接相关的证据。
- priorityIds 非空的卡必须全部使用，标题或摘要逐字包含卡片的全部 matchTerms。
- 只有 1–2 条 expanded=true；其余为 false。普通摘要 1–3 句，expanded 是一段完整分析。
- “🌍 海外观察”和“🚀 AI 一人公司（OPC）”每条，以及所有 expanded 条目，summary 最后都用“对你的映射：……”给出本周可执行动作。
- 单一来源、传闻、社区测试和模型自述必须明确限定，不得升级证据强度。
- oneLiner 以“📌 今日一句话：”开头，只浓缩 2–3 个最重要信号和一个行动取舍。"""
    user = f"""日期：{date_value}
{task}

每条只输出 headline、summary、expanded、evidenceIds；来源将由程序按 evidenceIds 自动复制，禁止在文字中编造链接。web 卡的 extractorOutputs 是程序在搜索后直接读取精确原文并冻结的摘要与原始哈希；核对卡片 facts 时以 outputSummary 为直接页面证据，不得外推。

evidence cards:
{json.dumps(card_payload, ensure_ascii=False, separators=(',', ':'))}
"""
    return system, user


def editor_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise QwenReportError("editor response contains no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        chunks = [
            item.get("text")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        if chunks:
            return "\n".join(chunks)
    raise QwenReportError("editor response contains no text")


def canonical_decimal(value: str, scale: str = "") -> str | None:
    try:
        number = Decimal(value.replace(",", ""))
        if scale:
            number *= Decimal({"万": 10_000, "亿": 100_000_000, "兆": 1_000_000_000_000}[scale])
    except (InvalidOperation, KeyError):
        return None
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered.startswith(("-", "0.")):
        return rendered
    return rendered.lstrip("0") or "0"


def chinese_integer(value: str) -> int | None:
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    small_units = {"十": 10, "百": 100, "千": 1000}
    large_units = {"万": 10_000, "亿": 100_000_000, "兆": 1_000_000_000_000}
    if not value or value[0] in large_units:
        return None
    if not any(char in small_units or char in large_units for char in value):
        try:
            return int("".join(str(digits[char]) for char in value))
        except (KeyError, ValueError):
            return None
    total = section = current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in small_units:
            section += (current or 1) * small_units[char]
            current = 0
        elif char in large_units:
            section += current
            total += (section or 1) * large_units[char]
            section = current = 0
        else:
            return None
    return total + section + current


def canonical_unit(value: str) -> str:
    lowered = value.replace("％", "%").lower()
    if lowered in {"percent", "percentage"}:
        return "%"
    if lowered in {"token", "tokens"}:
        return "token"
    if lowered in {"param", "params", "parameter", "parameters"}:
        return "parameter"
    return lowered


def normalized_numbers(value: str) -> set[str]:
    """Extract comparable numeric claims, including model IDs and CJK units."""

    normalized: set[str] = set()
    percent_spans: list[tuple[int, int]] = []
    for match in CHINESE_PERCENT_RE.finditer(value):
        raw = match.group("number")
        number = canonical_decimal(raw) if raw[0].isdigit() else chinese_integer(raw)
        if number is not None:
            normalized.add(f"{number}%")
            percent_spans.append(match.span())

    for match in ASCII_IDENTIFIER_RE.finditer(value):
        normalized.add(match.group(0).lower())
    for match in ASCII_QUANTITY_RE.finditer(value):
        if any(match.start() < end and match.end() > start for start, end in percent_spans):
            continue
        number = canonical_decimal(match.group("number"), match.group("scale") or "")
        if number is None:
            continue
        unit = canonical_unit(match.group("unit") or "")
        currency = canonical_unit(match.group("currency") or "")
        if currency in {"$", "us$", "usd"}:
            currency = "usd:"
        elif currency in {"¥", "￥", "cn¥", "rmb", "cny"}:
            currency = "cny:"
        normalized.add(f"{currency}{number}{unit}")

    for match in CHINESE_QUANTITY_RE.finditer(value):
        if any(match.start() < end and match.end() > start for start, end in percent_spans):
            continue
        raw = match.group("number")
        unit = match.group("unit") or ""
        if not unit and not any(char in "十百千万亿兆" for char in raw):
            continue
        # In prose, these forms normally act as indefinite articles rather
        # than material numeric claims (for example, "一项更新").
        if raw == "一" and unit in {"项", "个", "条", "次", "种", "款"}:
            continue
        if raw == "千万" and value[match.end() : match.end() + 1] == "不":
            continue
        number = chinese_integer(raw)
        if number is not None:
            normalized.add(f"{number}{canonical_unit(unit)}")
    return normalized


def exclude_previously_sourced_cards(
    cards: list[dict[str, Any]], previously_used_urls: set[str]
) -> list[dict[str, Any]]:
    normalized_used = {normalize_url(url) for url in previously_used_urls}
    result: list[dict[str, Any]] = []
    for card in cards:
        if card["priorityIds"]:
            result.append(card)
            continue
        card_urls = {normalize_url(item["url"]) for item in card["sources"]}
        if card_urls and card_urls.issubset(normalized_used):
            continue
        result.append(card)
    return result


def compiled_sources(cards: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for card in cards:
        for item_source in card["sources"]:
            normalized = normalize_url(item_source["url"])
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(
                {
                    "name": clean_text(item_source.get("name"), 160).removesuffix(
                        SINGLE_SOURCE_SUFFIX
                    ),
                    "url": normalized,
                }
            )
    if len(result) == 1:
        result[0]["name"] += SINGLE_SOURCE_SUFFIX
    return result[:3]


def trusted_evidence_text(card: dict[str, Any]) -> str:
    """Return the local trust boundary, excluding W-card model-authored prose."""

    extractor_text = " ".join(
        clean_text(item.get("outputSummary"), 6000)
        for item in card.get("extractorOutputs") or []
        if isinstance(item, dict)
    )
    if str(card.get("id") or "").startswith("W"):
        return extractor_text
    return f"{card['title']} {card['facts']} {extractor_text}"


def evidence_card_text(card: dict[str, Any]) -> str:
    """Grounding text available to the editor, deliberately excluding dates."""

    return trusted_evidence_text(card)


def unsupported_claim_markers(claim: str, evidence: str) -> list[str]:
    """Catch high-risk strengthening and invented identifiers before model audit."""

    factual_claim = claim.split("对你的映射：", 1)[0]
    lowered_evidence = evidence.lower()
    unsupported: list[str] = []
    for marker, equivalents in CRITICAL_CLAIM_EQUIVALENTS.items():
        if marker in factual_claim and not any(
            equivalent.lower() in lowered_evidence for equivalent in equivalents
        ):
            unsupported.append(marker)
    for token in re.findall(r"[A-Za-z][A-Za-z0-9._-]{1,}", factual_claim):
        lowered = token.lower()
        if lowered in CLAIM_ASCII_STOPWORDS or lowered.isdigit():
            continue
        if lowered not in lowered_evidence and token not in unsupported:
            unsupported.append(token)
    for quoted in re.findall(r"[《“]([^》”]{2,40})[》”]", factual_claim):
        if quoted not in evidence and quoted not in unsupported:
            unsupported.append(quoted)
    return unsupported


def chinese_bigrams(value: str) -> set[str]:
    """Return CJK phrase anchors while keeping punctuation as a boundary."""

    result: set[str] = set()
    for sequence in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", value):
        result.update(
            sequence[index : index + 2] for index in range(len(sequence) - 1)
        )
    return result


def ascii_semantic_anchors(value: str) -> set[str]:
    """Return cross-language entity anchors, excluding generic report vocabulary."""

    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9._-]{2,}", value)
        if token.lower() not in CLAIM_ASCII_STOPWORDS
    }


def longest_unmatched_chinese_bigram_run(
    value: str, evidence_grams: set[str]
) -> int:
    longest = 0
    for sequence in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", value):
        running = 0
        for index in range(len(sequence) - 1):
            if sequence[index : index + 2] in evidence_grams:
                running = 0
            else:
                running += 1
                longest = max(longest, running)
    return longest


def claim_grounding_error(claim: str, evidence: str) -> str | None:
    """Fail closed when a factual claim is not even topically tied to its evidence.

    This is deliberately conservative. It is not a semantic oracle; the separate
    factual-audit call still decides entailment. Its job is to make an unrelated
    but verbatim quote insufficient to pass the production gate.
    """

    factual_claim = claim.split("对你的映射：", 1)[0]
    trusted = clean_text(evidence, 12000)
    evidence_grams = chinese_bigrams(trusted)
    evidence_anchors = ascii_semantic_anchors(trusted)
    evidence_numbers = normalized_numbers(trusted)
    segments = [
        clean_text(segment, 500)
        for segment in re.split(
            r"[\n。！？；!?;，,]+|"
            r"同时|并且|以及|而且|然而|随后|从而|因此|意味着|导致",
            factual_claim,
        )
        if clean_text(segment, 500)
    ]
    if not segments:
        return "claim has no factual segment"
    for segment in segments:
        claim_grams = chinese_bigrams(segment)
        if len(claim_grams) >= 4 and len(evidence_grams) >= 8:
            shared = claim_grams.intersection(evidence_grams)
            unmatched_run = longest_unmatched_chinese_bigram_run(
                segment, evidence_grams
            )
            if shared == claim_grams:
                continue
            return (
                f"segment {segment[:80]!r} is not extractively grounded "
                f"({len(shared)}/{len(claim_grams)}, unmatched run {unmatched_run})"
            )
        shared_identifiers = ascii_semantic_anchors(segment).intersection(
            evidence_anchors
        )
        shared_numbers = normalized_numbers(segment).intersection(evidence_numbers)
        if shared_identifiers or shared_numbers:
            continue
        if len(claim_grams.intersection(evidence_grams)) >= 3:
            continue
        return (
            f"segment {segment[:80]!r} has no shared phrase, entity, "
            "or numeric anchor"
        )
    return None


def validate_editor_shape(document: dict[str, Any], mode: str) -> None:
    one_liner = document.get("oneLiner")
    expected_prefix = "📌 补刊：" if mode == "addendum" else "📌 今日一句话："
    if not isinstance(one_liner, str) or not one_liner.startswith(expected_prefix):
        raise QwenReportError(f"oneLiner must start with {expected_prefix!r}")
    sections = document.get("sections")
    if not isinstance(sections, list):
        raise QwenReportError("editor sections must be an array")
    if mode == "addendum":
        if len(sections) != 1 or sections[0].get("title") != SECTION_TITLES[0]:
            raise QwenReportError("addendum must contain one major-events section")
        item_count = len(sections[0].get("items") or [])
        if not 1 <= item_count <= 5:
            raise QwenReportError("addendum must contain 1–5 items")
        for item in sections[0].get("items") or []:
            if not isinstance(item, dict) or item.get("expanded") is not False:
                raise QwenReportError("addendum items must set expanded=false")
            evidence_ids = item.get("evidenceIds")
            if not isinstance(evidence_ids, list) or len(evidence_ids) != 1:
                raise QwenReportError(
                    "addendum items must cite exactly one priority evidenceId"
                )
    else:
        titles = [section.get("title") for section in sections if isinstance(section, dict)]
        if titles != list(SECTION_TITLES):
            raise QwenReportError("main report must contain six canonical sections in order")
        total = 0
        expanded = 0
        for section, (title, minimum, maximum) in zip(sections, SECTION_POLICY):
            items = section.get("items") if isinstance(section, dict) else None
            if not isinstance(items, list) or not minimum <= len(items) <= maximum:
                raise QwenReportError(
                    f"section {title!r} must contain {minimum}–{maximum} items"
                )
            total += len(items)
            expanded += sum(
                item.get("expanded") is True for item in items if isinstance(item, dict)
            )
        if not 20 <= total <= 28:
            raise QwenReportError("main report must contain 20–28 items")
        if not 1 <= expanded <= 2:
            raise QwenReportError("main report must contain 1–2 expanded items")


def compile_sections(
    editor_document: dict[str, Any], cards: list[dict[str, Any]], mode: str
) -> list[dict[str, Any]]:
    validate_editor_shape(editor_document, mode)
    card_by_id = {card["id"]: card for card in cards}
    used_evidence: set[str] = set()
    used_priority: set[str] = set()
    compiled: list[dict[str, Any]] = []
    for section in editor_document["sections"]:
        title = section["title"]
        compiled_items: list[dict[str, Any]] = []
        for raw_item in section["items"]:
            if not isinstance(raw_item, dict):
                raise QwenReportError("editor item must be an object")
            evidence_ids = raw_item.get("evidenceIds")
            if not isinstance(evidence_ids, list) or not evidence_ids:
                raise QwenReportError("every item must cite at least one evidenceId")
            if any(not isinstance(item, str) or item not in card_by_id for item in evidence_ids):
                raise QwenReportError("editor cited an unknown evidenceId")
            if used_evidence.intersection(evidence_ids):
                raise QwenReportError("an evidenceId was reused across report items")
            used_evidence.update(evidence_ids)
            cited = [card_by_id[item] for item in evidence_ids]
            if any(card["section"] != title for card in cited):
                raise QwenReportError("an item cited evidence from a different section")
            priority_cards_cited = [card for card in cited if card["priorityIds"]]
            if priority_cards_cited and (
                len(cited) != 1
                or len(priority_cards_cited) != 1
                or len(priority_cards_cited[0]["priorityIds"]) != 1
            ):
                raise QwenReportError(
                    "a priority item must cite exactly one priority evidence card"
                )
            headline = clean_text(raw_item.get("headline"), 220)
            summary = clean_text(raw_item.get("summary"), 1200)
            expanded = raw_item.get("expanded")
            if len(headline) < 8 or len(summary) < 28 or not isinstance(expanded, bool):
                raise QwenReportError("editor item has empty or malformed fields")
            evidence_text = " ".join(evidence_card_text(card) for card in cited)
            unsupported_numbers = normalized_numbers(f"{headline} {summary}") - normalized_numbers(
                evidence_text
            )
            if unsupported_numbers:
                raise QwenReportError(
                    f"item {headline!r} introduced unsupported numbers: "
                    + ", ".join(sorted(unsupported_numbers))
                )
            unsupported_markers = unsupported_claim_markers(
                f"{headline} {summary}", evidence_text
            )
            if unsupported_markers:
                raise QwenReportError(
                    f"item {headline!r} introduced unsupported claim markers: "
                    + ", ".join(unsupported_markers)
                )
            grounding_error = claim_grounding_error(
                f"{headline}\n{summary}", evidence_text
            )
            if grounding_error:
                raise QwenReportError(
                    f"item {headline!r} is not topically grounded: {grounding_error}"
                )
            if (title in MAPPING_SECTIONS or expanded) and "对你的映射：" not in summary:
                raise QwenReportError(
                    f"item {headline!r} is missing its required action mapping"
                )
            priority_ids: list[str] = []
            for card in cited:
                for priority_id in card["priorityIds"]:
                    if priority_id not in priority_ids:
                        priority_ids.append(priority_id)
                        used_priority.add(priority_id)
                for match_term in card["matchTerms"]:
                    if match_term and match_term.lower() not in f"{headline} {summary}".lower():
                        raise QwenReportError(
                            f"priority item {headline!r} omitted match term {match_term!r}"
                        )
            item = {
                "headline": headline,
                "summary": summary,
                "expanded": expanded,
                "sources": compiled_sources(cited),
            }
            if priority_ids:
                item["priorityIds"] = priority_ids
            compiled_items.append(item)
        compiled.append({"title": title, "items": compiled_items})

    required_priority = {
        priority_id for card in cards for priority_id in card["priorityIds"]
    }
    if required_priority - used_priority:
        raise QwenReportError(
            "editor omitted required priority candidates: "
            + ", ".join(sorted(required_priority - used_priority))
        )
    return compiled


def artifact_mode(artifact_dir: Path, date_value: str) -> str:
    return "addendum" if (artifact_dir / f"{date_value}-1.json").exists() else "main"


def build_artifact(
    arguments: argparse.Namespace,
    editor_document: dict[str, Any],
    cards: list[dict[str, Any]],
    mode: str,
) -> Path:
    try:
        report_day = datetime.strptime(arguments.date, "%Y-%m-%d")
    except ValueError as exc:
        raise QwenReportError("--date must be YYYY-MM-DD") from exc
    arguments.artifact_dir.mkdir(parents=True, exist_ok=True)
    same_day = fallback.same_day_artifacts(arguments.artifact_dir, arguments.date)
    if mode == "main":
        if same_day:
            raise QwenReportError("refusing to overwrite an existing same-day report")
        sequence = 1
        issue_number = fallback.next_issue_number(arguments.artifact_dir)
        label = f"第{fallback.int_to_chinese(issue_number)}期"
    else:
        if not same_day or same_day[0][0] != 1:
            raise QwenReportError("cannot create an addendum without a main report")
        sequence = same_day[-1][0] + 1
        issue_number = fallback.next_issue_number(arguments.artifact_dir)
        label = f"第{fallback.int_to_chinese(issue_number)}期·补刊"
    sections = compile_sections(editor_document, cards, mode)
    unsupported_one_liner_numbers = normalized_numbers(
        editor_document["oneLiner"]
    ) - normalized_numbers(" ".join(evidence_card_text(card) for card in cards))
    if unsupported_one_liner_numbers:
        raise QwenReportError(
            "oneLiner introduced unsupported numbers: "
            + ", ".join(sorted(unsupported_one_liner_numbers))
        )
    weekday = "一二三四五六日"[report_day.weekday()]
    artifact: dict[str, Any] = {
        "date": f"{arguments.date} 星期{weekday}",
        "label": label,
        "generatedAt": fallback.normalize_generated_at(arguments.generated_at),
        "oneLiner": clean_text(editor_document["oneLiner"], 500),
        "sections": sections,
    }
    if mode == "addendum":
        artifact["kind"] = "addendum"
    if artifact.get("fallback") is not None or "自动恢复版" in label:
        raise QwenReportError("Qwen output may not enter the fallback policy")
    output = arguments.artifact_dir / f"{arguments.date}-{sequence}.json"
    fallback.atomic_write_text(
        output, json.dumps(artifact, ensure_ascii=False, indent=2) + "\n"
    )
    headlines = [
        item["headline"] for section in sections for item in section["items"]
    ]
    fallback.append_reported(
        arguments.reported, arguments.date, label, headlines, []
    )
    return output


def research(
    arguments: argparse.Namespace,
    api_key: str,
    seed_cards: list[dict[str, Any]],
    report_day: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    history = reported_history(arguments.reported, report_day)
    previously_used_urls = fallback.existing_source_urls(arguments.artifact_dir)
    reference_time = parse_datetime(arguments.generated_at)
    if reference_time is None:
        raise QwenReportError("--generated-at must be an ISO date/time")
    attachment_items = getattr(arguments, "attachment_exclusions", [])
    trending_repositories = getattr(arguments, "trending_repositories", None)
    excluded_titles = [
        item.get("title", "") for item in attachment_items if isinstance(item, dict)
    ]
    cost_cap_cny = float(getattr(arguments, "cost_cap_cny", 1.0))

    progress_lock = threading.Lock()
    progress_calls: list[dict[str, Any]] = []
    active_reservations = 0.0

    def reserve_call(prompt: str) -> float:
        nonlocal active_reservations
        prompt_upper = (
            len(prompt.encode("utf-8")) * QWEN_PLUS_INPUT_CNY_PER_MILLION
            + 6000 * QWEN_PLUS_OUTPUT_CNY_PER_MILLION
        ) / 1_000_000 + QWEN_WEB_SEARCH_CNY_PER_CALL
        reservation = max(0.08, prompt_upper)
        with progress_lock:
            actual = sum(
                float(item.get("estimatedCostCny") or 0) for item in progress_calls
            )
            if actual + active_reservations + reservation > cost_cap_cny:
                raise QwenReportError(
                    "research cost reservation exceeded cap"
                )
            active_reservations += reservation
        return reservation

    def cancel_reservation(reservation: float) -> None:
        nonlocal active_reservations
        if not reservation:
            return
        with progress_lock:
            active_reservations = max(0.0, active_reservations - reservation)

    def persist_progress(call: dict[str, Any], reservation: float = 0.0) -> float:
        nonlocal active_reservations
        with progress_lock:
            active_reservations = max(0.0, active_reservations - reservation)
            progress_calls.append(call)
            running_cost = round(
                sum(float(item.get("estimatedCostCny") or 0) for item in progress_calls),
                8,
            )
            write_diagnostics(
                getattr(arguments, "diagnostics", None),
                {
                    "status": "running",
                    "model": arguments.model,
                    "date": arguments.date,
                    "costCapCny": cost_cap_cny,
                    "research": {
                        "calls": list(progress_calls),
                        "estimatedCostCny": running_cost,
                    },
                },
            )
            return running_cost

    def run_section(
        policy: tuple[str, int, int]
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        title, minimum, maximum = policy
        section_seeds = [card for card in seed_cards if card["section"] == title]
        cards = list(section_seeds)
        calls: list[dict[str, Any]] = []
        desired = min(maximum, minimum + 1)
        errors: list[str] = []
        for attempt, focus in enumerate(SECTION_RESEARCH_ANGLES[title], start=1):
            call: dict[str, Any] | None = None
            reservation = 0.0
            remaining = max(2, desired - len(cards))
            prompt = research_prompt(
                arguments.date,
                arguments.generated_at,
                cards,
                history,
                title,
                remaining,
                min(maximum + 3, remaining + 3),
                attachment_items,
                focus,
            )
            try:
                reservation = reserve_call(prompt)
                response = api_post(
                    arguments.base_url,
                    "responses",
                    api_key,
                    {
                        "model": arguments.model,
                        "input": prompt,
                        "tools": [{"type": "web_search"}],
                        "reasoning": {"effort": "medium"},
                        "max_output_tokens": 6000,
                    },
                    arguments.research_timeout,
                )
                metadata = response_metadata(
                    response, arguments.model, include_web_search=True
                )
                call = {
                    "section": title,
                    "attempt": attempt,
                    **metadata,
                    "promptSha256": hashlib.sha256(
                        prompt.encode("utf-8")
                    ).hexdigest(),
                }
                if response.get("status") != "completed":
                    raise QwenReportError(
                        f"response status is {response.get('status')!r}"
                    )
                output_text = response_output_text(response)
                if not output_text:
                    raise QwenReportError("response has no text")
                research_document = parse_research_document(output_text)
                requested_urls = [
                    str(item_source.get("url") or "")
                    for evidence in research_document.get("evidence") or []
                    if isinstance(evidence, dict)
                    for item_source in evidence.get("sources") or []
                    if isinstance(item_source, dict)
                ]
                discovered_sources = web_search_source_map(response)
                if any(
                    isinstance(item, dict)
                    and item.get("type") == "web_extractor_call"
                    for item in response.get("output") or []
                ):
                    raise QwenReportError(
                        "response used an unrequested web_extractor tool"
                    )
                sources = direct_fetch_source_map(
                    discovered_sources,
                    requested_urls,
                    min(30, arguments.research_timeout),
                )
                extractor_records = extractor_audit_records(sources)
                call.update(
                    {
                        "toolSourceCount": len(discovered_sources),
                        "completedExtractorCallCount": completed_extractor_call_count(
                            response
                        ),
                        "directFetchCount": sum(
                            item.get("retrievalMethod") == "direct-http"
                            for item in sources.values()
                        ),
                        "extractorOutputCount": len(extractor_records),
                        "extractorOutputs": extractor_records,
                    }
                )
                if not sources:
                    raise QwenReportError(
                        "response has no model-selected source that could be read directly"
                    )
                cards = merge_research_cards(
                    cards,
                    research_document,
                    sources,
                    reference_time,
                    excluded_titles,
                    trending_repositories,
                )
                cards = [card for card in cards if card["section"] == title]
                cards = exclude_previously_sourced_cards(
                    cards, previously_used_urls
                )
                call["evidenceCardCount"] = len(cards)
                running_cost = persist_progress(call, reservation)
                reservation = 0.0
                calls.append(call)
                if running_cost > cost_cap_cny:
                    raise QwenReportError(
                        f"research cost CNY {running_cost:.6f} exceeded cap "
                        f"{cost_cap_cny:.6f}"
                    )
            except QwenReportError as exc:
                if "exceeded cap" in str(exc):
                    cancel_reservation(reservation)
                    raise
                errors.append(f"attempt {attempt}: {exc}")
                if call is not None and call not in calls:
                    call["error"] = str(exc)
                    running_cost = persist_progress(call, reservation)
                    reservation = 0.0
                    calls.append(call)
                    if running_cost > cost_cap_cny:
                        raise QwenReportError(
                            f"research cost CNY {running_cost:.6f} exceeded cap "
                            f"{cost_cap_cny:.6f}"
                        )
                elif isinstance(exc, QwenRequestOutcomeUnknown) and reservation:
                    call = {
                        "section": title,
                        "attempt": attempt,
                        "requestId": None,
                        "model": arguments.model,
                        "pricingVersion": PRICING_VERSION,
                        "tokenCostCny": round(reservation, 8),
                        "webSearchCount": 0,
                        "webSearchCostCny": 0,
                        "estimatedCostCny": round(reservation, 8),
                        "costAccounting": "reserved-after-unknown-outcome",
                        "error": str(exc),
                    }
                    running_cost = persist_progress(call, reservation)
                    reservation = 0.0
                    calls.append(call)
                    if running_cost > cost_cap_cny:
                        raise QwenReportError(
                            f"research cost CNY {running_cost:.6f} exceeded cap "
                            f"{cost_cap_cny:.6f}"
                        )
                else:
                    cancel_reservation(reservation)
            if len(cards) >= desired:
                break
        if len(cards) < minimum:
            detail = "; ".join(errors[-2:])
            raise QwenReportError(
                f"research evidence for {title!r} has {len(cards)} card(s), "
                f"needs at least {minimum}" + (f"; {detail}" if detail else "")
            )
        return title, cards, calls

    section_results: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(run_section, policy): policy[0] for policy in SECTION_POLICY
        }
        for future in concurrent.futures.as_completed(futures):
            title, cards, call_records = future.result()
            section_results[title] = (cards, call_records)
            print(
                f"qwen report: researched {title} ({len(cards)} accepted card(s))",
                flush=True,
            )

    cards: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    next_web_index = 1
    for title, _minimum, _maximum in SECTION_POLICY:
        section_cards, call_records = section_results[title]
        calls.extend(call_records)
        for card in section_cards:
            card_urls = {normalize_url(item["url"]) for item in card["sources"]}
            if not card["priorityIds"] and card_urls and card_urls.issubset(seen_urls):
                continue
            if card["id"].startswith("W"):
                card["id"] = f"W{next_web_index:03d}"
                next_web_index += 1
            cards.append(card)
            seen_urls.update(card_urls)

    section_counts = {
        title: sum(card["section"] == title for card in cards) for title in SECTION_TITLES
    }
    missing_sections = [
        title
        for title, minimum, _maximum in SECTION_POLICY
        if section_counts[title] < minimum
    ]
    if missing_sections:
        raise QwenReportError(
            "research evidence is too sparse after cross-section dedupe: "
            + "、".join(missing_sections)
            + f"; counts={section_counts}"
        )
    estimated_cost = sum(float(call.get("estimatedCostCny") or 0) for call in calls)
    return cards, {
        "calls": calls,
        "tokenCostCny": round(
            sum(float(call.get("tokenCostCny") or 0) for call in calls), 8
        ),
        "webSearchCount": sum(int(call.get("webSearchCount") or 0) for call in calls),
        "webSearchCostCny": round(
            sum(float(call.get("webSearchCostCny") or 0) for call in calls), 8
        ),
        "estimatedCostCny": round(estimated_cost, 8),
        "toolSourceCount": sum(int(call.get("toolSourceCount") or 0) for call in calls),
        "completedExtractorCallCount": sum(
            int(call.get("completedExtractorCallCount") or 0) for call in calls
        ),
        "directFetchCount": sum(
            int(call.get("directFetchCount") or 0) for call in calls
        ),
        "extractorOutputCount": sum(
            int(call.get("extractorOutputCount") or 0) for call in calls
        ),
        "evidenceCardCount": len(cards),
        "sectionCounts": section_counts,
    }


def edit(
    arguments: argparse.Namespace,
    api_key: str,
    cards: list[dict[str, Any]],
    mode: str,
    spent_cost_cny: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    system, user = editor_prompt(arguments.date, cards, mode)
    prompt_cost_upper_bound = (
        len(f"{system}\n{user}".encode("utf-8"))
        * QWEN_PLUS_INPUT_CNY_PER_MILLION
        + 12000 * QWEN_PLUS_OUTPUT_CNY_PER_MILLION
    ) / 1_000_000
    if spent_cost_cny + prompt_cost_upper_bound > arguments.cost_cap_cny:
        raise QwenReportError(
            "editor worst-case reservation would exceed the daily cost cap"
        )
    response = api_post(
        arguments.base_url,
        "chat/completions",
        api_key,
        {
            "model": arguments.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "daily_report",
                    "strict": True,
                    "schema": editor_schema(mode),
                },
            },
            "enable_thinking": True,
            "thinking_budget": 4096,
            "max_completion_tokens": 12000,
        },
        arguments.editor_timeout,
    )
    choices = response.get("choices")
    finish_reason = (
        choices[0].get("finish_reason")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict)
        else None
    )
    if finish_reason != "stop":
        raise QwenReportError(
            f"editor response finish_reason is {finish_reason!r}, expected 'stop'"
        )
    content = editor_content(response)
    metadata = response_metadata(response, arguments.model)
    return parse_json_object(content), {
        **metadata,
        "finishReason": finish_reason,
        "promptCostUpperBoundCny": round(prompt_cost_upper_bound, 8),
        "promptSha256": hashlib.sha256(
            f"{system}\n{user}".encode("utf-8")
        ).hexdigest(),
    }


def audit_item_keys(editor_document: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for section_index, section in enumerate(
        editor_document.get("sections") or [], start=1
    ):
        if not isinstance(section, dict):
            continue
        for item_index, _item in enumerate(section.get("items") or [], start=1):
            keys.append(f"S{section_index}I{item_index}")
    return keys


def editor_document_sha256(editor_document: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            editor_document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def trusted_audit_text(card: dict[str, Any]) -> str:
    """Return only independently frozen evidence, never W-card model prose."""

    return trusted_evidence_text(card)


def factual_audit_schema(item_keys: list[str]) -> dict[str, Any]:
    citation = {
        "type": "object",
        "properties": {
            "evidenceId": {"type": "string"},
            "quote": {"type": "string"},
        },
        "required": ["evidenceId", "quote"],
        "additionalProperties": False,
    }
    one_liner = {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["supported", "unsupported", "uncertain"],
            },
            "reason": {"type": "string"},
            "supportingItemKeys": {
                "type": "array",
                "items": {"type": "string", "enum": item_keys},
                "minItems": 1,
                "maxItems": 4,
            },
        },
        "required": ["verdict", "reason", "supportingItemKeys"],
        "additionalProperties": False,
    }
    finding = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "enum": item_keys},
            "verdict": {
                "type": "string",
                "enum": ["supported", "unsupported", "uncertain"],
            },
            "reason": {"type": "string"},
            "evidenceQuotes": {
                "type": "array",
                "items": citation,
                "minItems": 1,
                "maxItems": 3,
            },
        },
        "required": ["key", "verdict", "reason", "evidenceQuotes"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "draftSha256": {"type": "string"},
            "findings": {
                "type": "array",
                "items": finding,
                "minItems": len(item_keys),
                "maxItems": len(item_keys),
            },
            "oneLiner": one_liner,
        },
        "required": ["draftSha256", "findings", "oneLiner"],
        "additionalProperties": False,
    }


def factual_audit_prompt(
    editor_document: dict[str, Any], cards: list[dict[str, Any]]
) -> tuple[str, str, list[str], str]:
    card_by_id = {card["id"]: card for card in cards}
    audit_items: list[dict[str, Any]] = []
    item_keys = audit_item_keys(editor_document)
    key_index = 0
    for section in editor_document.get("sections") or []:
        for item in section.get("items") or []:
            key = item_keys[key_index]
            key_index += 1
            cited = [
                card_by_id[evidence_id]
                for evidence_id in item.get("evidenceIds") or []
                if evidence_id in card_by_id
            ]
            audit_items.append(
                {
                    "key": key,
                    "section": section.get("title"),
                    "headline": item.get("headline"),
                    "summary": item.get("summary"),
                    "evidence": [
                        {
                            "id": card["id"],
                            "researcherClaimedPublishedAt": card["publishedAt"],
                            "trustedText": trusted_audit_text(card),
                            "sources": card["sources"],
                        }
                        for card in cited
                    ],
                }
            )
    system = """你是一名独立事实审稿人，不参与写稿。逐条比较候选稿与其引用的 trustedText。
只要标题或摘要中的人物身份、产品归属、数字、时间、比较、因果、可用状态、收入、排名或结论强度不能由 trustedText 直接支持，就必须 unsupported 或 uncertain。不得用常识、记忆、researcherClaimedPublishedAt 或外部知识补证；日期和“近 72 小时”也必须能在 trustedText 中核实。证据写“可能/计划/测试/自述”时，稿件不得升级为既成事实。
每个 supported 条目必须对其每个引用 evidenceId 返回一段从该 trustedText 逐字复制的 evidenceQuotes.quote；不能改写，且引文本身必须包含标题/摘要的关键主体和行为，不得用与结论无关的真句子充数。“对你的映射：”之后纯粹的建议可以通过，但建议中夹带的新事实仍须拒绝。oneLiner 只能概括 supported 条目，不得引入新事实。你只能审计，不能改稿。输出必须严格符合 JSON Schema。"""
    draft_sha256 = editor_document_sha256(editor_document)
    user = (
        f"draftSha256 必须原样返回：{draft_sha256}\n"
        "请对每个 key 恰好返回一次 verdict。任何存疑项都不得判 supported。\n\n"
        "candidateOneLiner:\n"
        + json.dumps(editor_document.get("oneLiner"), ensure_ascii=False)
        + "\n\nauditItems:\n"
        + json.dumps(audit_items, ensure_ascii=False, separators=(",", ":"))
    )
    return system, user, item_keys, draft_sha256


def factual_audit_cost_upper_bound_cny(system: str, user: str) -> float:
    # A tokenizer cannot emit more non-empty tokens than the UTF-8 byte count.
    # Reserve uncached input plus the full completion allowance before calling.
    input_token_upper_bound = len(f"{system}\n{user}".encode("utf-8"))
    return (
        input_token_upper_bound * QWEN_PLUS_INPUT_CNY_PER_MILLION
        + 8000 * QWEN_PLUS_OUTPUT_CNY_PER_MILLION
    ) / 1_000_000


def factual_audit(
    arguments: argparse.Namespace,
    api_key: str,
    editor_document: dict[str, Any],
    cards: list[dict[str, Any]],
    spent_cost_cny: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    system, user, item_keys, draft_sha256 = factual_audit_prompt(
        editor_document, cards
    )
    if not item_keys:
        raise QwenReportError("factual audit received no report items")
    upper_bound_cost = factual_audit_cost_upper_bound_cny(system, user)
    if spent_cost_cny + upper_bound_cost > arguments.cost_cap_cny:
        raise QwenReportError(
            "factual audit worst-case reservation would exceed the daily cost cap"
        )
    response = api_post(
        arguments.base_url,
        "chat/completions",
        api_key,
        {
            "model": arguments.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "daily_report_factual_audit",
                    "strict": True,
                    "schema": factual_audit_schema(item_keys),
                },
            },
            "enable_thinking": True,
            "thinking_budget": 4096,
            "max_completion_tokens": 8000,
        },
        arguments.editor_timeout,
    )
    choices = response.get("choices")
    finish_reason = (
        choices[0].get("finish_reason")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict)
        else None
    )
    if finish_reason != "stop":
        raise QwenReportError(
            f"factual audit finish_reason is {finish_reason!r}, expected 'stop'"
        )
    content = editor_content(response)
    metadata = response_metadata(response, arguments.model)
    return parse_json_object(content), {
        **metadata,
        "finishReason": finish_reason,
        "findingCount": len(item_keys),
        "draftSha256": draft_sha256,
        "promptCostUpperBoundCny": round(upper_bound_cost, 8),
        "promptSha256": hashlib.sha256(
            f"{system}\n{user}".encode("utf-8")
        ).hexdigest(),
    }


def validate_factual_audit(
    audit_document: dict[str, Any],
    editor_document: dict[str, Any],
    cards: list[dict[str, Any]],
) -> None:
    if set(audit_document) != {"draftSha256", "findings", "oneLiner"}:
        raise QwenReportError("factual audit has an invalid top-level shape")
    if audit_document.get("draftSha256") != editor_document_sha256(editor_document):
        raise QwenReportError("factual audit draft hash does not match the editor draft")
    expected_keys = audit_item_keys(editor_document)
    cited_by_key: dict[str, list[str]] = {}
    claim_by_key: dict[str, str] = {}
    key_index = 0
    for section in editor_document.get("sections") or []:
        for item in section.get("items") or []:
            key = expected_keys[key_index]
            cited_by_key[key] = list(item.get("evidenceIds") or [])
            claim_by_key[key] = (
                f"{item.get('headline') or ''}\n{item.get('summary') or ''}"
            )
            key_index += 1
    card_by_id = {card["id"]: card for card in cards}
    findings = audit_document.get("findings")
    if not isinstance(findings, list):
        raise QwenReportError("factual audit findings must be an array")
    observed_keys = [
        finding.get("key") for finding in findings if isinstance(finding, dict)
    ]
    if len(observed_keys) != len(findings) or sorted(observed_keys) != sorted(
        expected_keys
    ):
        raise QwenReportError("factual audit did not return every item exactly once")
    failures = []
    for finding in findings:
        if set(finding) != {"key", "verdict", "reason", "evidenceQuotes"}:
            raise QwenReportError("factual audit finding has an invalid shape")
        if finding["verdict"] not in {"supported", "unsupported", "uncertain"}:
            raise QwenReportError("factual audit returned an invalid verdict")
        if not isinstance(finding["reason"], str) or not finding["reason"].strip():
            raise QwenReportError("factual audit finding is missing a reason")
        quotes = finding.get("evidenceQuotes")
        if not isinstance(quotes, list) or not quotes:
            raise QwenReportError("factual audit finding has no evidence quote")
        quote_ids = []
        quote_texts: list[str] = []
        for citation in quotes:
            if not isinstance(citation, dict) or set(citation) != {"evidenceId", "quote"}:
                raise QwenReportError("factual audit evidence quote has an invalid shape")
            evidence_id = citation.get("evidenceId")
            quote = citation.get("quote")
            if evidence_id not in cited_by_key[finding["key"]]:
                raise QwenReportError("factual audit quote cites the wrong evidence card")
            if not isinstance(quote, str) or len(quote.strip()) < 8:
                raise QwenReportError("factual audit evidence quote is too short")
            trusted_text = trusted_audit_text(card_by_id[evidence_id])
            if quote not in trusted_text:
                raise QwenReportError("factual audit quote is not present in trusted evidence")
            quote_ids.append(evidence_id)
            quote_texts.append(quote)
        if sorted(quote_ids) != sorted(cited_by_key[finding["key"]]):
            raise QwenReportError("factual audit did not quote every cited evidence card")
        grounding_error = claim_grounding_error(
            claim_by_key[finding["key"]], " ".join(quote_texts)
        )
        if grounding_error:
            raise QwenReportError(
                "factual audit quote is not topically grounded for "
                f"{finding['key']}: {grounding_error}"
            )
        if finding["verdict"] != "supported":
            failures.append(f"{finding['key']}: {clean_text(finding['reason'], 240)}")
    one_liner = audit_document.get("oneLiner")
    if not isinstance(one_liner, dict) or set(one_liner) != {
        "verdict",
        "reason",
        "supportingItemKeys",
    }:
        raise QwenReportError("factual audit oneLiner verdict has an invalid shape")
    if one_liner.get("verdict") not in {"supported", "unsupported", "uncertain"}:
        raise QwenReportError("factual audit returned an invalid oneLiner verdict")
    if not isinstance(one_liner.get("reason"), str) or not one_liner["reason"].strip():
        raise QwenReportError("factual audit oneLiner verdict is missing a reason")
    supporting_keys = one_liner.get("supportingItemKeys")
    if (
        not isinstance(supporting_keys, list)
        or not supporting_keys
        or len(set(supporting_keys)) != len(supporting_keys)
        or any(key not in expected_keys for key in supporting_keys)
    ):
        raise QwenReportError("factual audit oneLiner has invalid supporting item keys")
    if one_liner["verdict"] != "supported":
        failures.append(f"oneLiner: {clean_text(one_liner['reason'], 240)}")
    if failures:
        raise QwenReportError("factual audit rejected the draft: " + "; ".join(failures[:8]))


def main_report_capacity(cards: list[dict[str, Any]]) -> int:
    return sum(
        min(
            maximum,
            sum(card.get("section") == title for card in cards),
        )
        for title, _minimum, maximum in SECTION_POLICY
    )


def run(arguments: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    arguments.base_url = validate_base_url(arguments.base_url)
    if (
        not isinstance(arguments.cost_cap_cny, (int, float))
        or not math.isfinite(arguments.cost_cap_cny)
        or arguments.cost_cap_cny <= 0
    ):
        raise QwenReportError("--cost-cap-cny must be a finite positive number")
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise QwenReportError("DASHSCOPE_API_KEY is not configured")
    try:
        report_day = datetime.strptime(arguments.date, "%Y-%m-%d")
    except ValueError as exc:
        raise QwenReportError("--date must be YYYY-MM-DD") from exc
    priority = load_json(arguments.priority)
    builders = load_json(arguments.builders)
    artificial_analysis_path = getattr(arguments, "artificial_analysis", None)
    waytoagi_path = getattr(arguments, "waytoagi", None)
    artificial_analysis = (
        load_json(artificial_analysis_path) if artificial_analysis_path else {}
    )
    waytoagi = load_json(waytoagi_path) if waytoagi_path else {}
    arguments.attachment_exclusions = attachment_exclusions(
        artificial_analysis, waytoagi
    )
    covered_priority_ids = fallback.existing_priority_ids(arguments.artifact_dir)
    priorities = priority_cards(priority, covered_priority_ids)
    mode = artifact_mode(arguments.artifact_dir, arguments.date)
    diagnostics: dict[str, Any] = {
        "status": "running",
        "model": arguments.model,
        "mode": mode,
        "date": arguments.date,
        "baseUrl": arguments.base_url,
        "promptVersion": PROMPT_VERSION,
        "costCapCny": arguments.cost_cap_cny,
    }
    if mode == "addendum":
        if not priorities:
            raise QwenReportError("no uncovered priority candidate needs an addendum")
        cards = priorities
        research_diagnostics: dict[str, Any] = {
            "skipped": True,
            "reason": "addenda use frozen official priority evidence only",
            "evidenceCardCount": len(cards),
        }
    else:
        if getattr(arguments, "trending_repositories", None) is None:
            arguments.trending_repositories = fetch_github_trending_repositories()
        builder_seed_cards = exclude_previously_sourced_cards(
            builder_cards(builders, report_day),
            fallback.existing_source_urls(arguments.artifact_dir),
        )
        seeds = priorities + builder_seed_cards
        print(
            f"qwen report: researching with {len(seeds)} frozen seed card(s)",
            flush=True,
        )
        cards, research_diagnostics = research(
            arguments, api_key, seeds, report_day
        )
        if research_diagnostics["estimatedCostCny"] > arguments.cost_cap_cny:
            raise QwenReportError("research calls exceeded the daily cost cap")
        capacity = main_report_capacity(cards)
        if capacity < 20:
            raise QwenReportError(
                f"accepted evidence can fill only {capacity} main-report slots; "
                "at least 20 are required"
            )
    diagnostics["research"] = research_diagnostics
    write_diagnostics(getattr(arguments, "diagnostics", None), diagnostics)
    print(
        f"qwen report: editing from {len(cards)} accepted evidence card(s)",
        flush=True,
    )
    editor_document, editor_diagnostics = edit(
        arguments,
        api_key,
        cards,
        mode,
        float(research_diagnostics.get("estimatedCostCny") or 0),
    )
    pre_audit_cost = float(
        research_diagnostics.get("estimatedCostCny") or 0
    ) + float(editor_diagnostics["estimatedCostCny"])
    diagnostics.update(
        {
            "research": research_diagnostics,
            "editor": editor_diagnostics,
            "estimatedCostCny": round(pre_audit_cost, 8),
        }
    )
    write_diagnostics(getattr(arguments, "diagnostics", None), diagnostics)
    if pre_audit_cost > arguments.cost_cap_cny:
        raise QwenReportError(
            f"estimated cost CNY {pre_audit_cost:.6f} exceeds cap "
            f"{arguments.cost_cap_cny:.6f}"
        )
    # Reject malformed, cross-section, reused, or numerically ungrounded drafts
    # before spending a third call on their factual audit.
    compile_sections(editor_document, cards, mode)
    print("qwen report: independently auditing every drafted claim", flush=True)
    audit_document, audit_diagnostics = factual_audit(
        arguments, api_key, editor_document, cards, pre_audit_cost
    )
    estimated_cost = pre_audit_cost + float(audit_diagnostics["estimatedCostCny"])
    diagnostics.update(
        {
            "verifier": audit_diagnostics,
            "estimatedCostCny": round(estimated_cost, 8),
        }
    )
    write_diagnostics(getattr(arguments, "diagnostics", None), diagnostics)
    if estimated_cost > arguments.cost_cap_cny:
        raise QwenReportError(
            f"estimated cost CNY {estimated_cost:.6f} exceeds cap "
            f"{arguments.cost_cap_cny:.6f}"
        )
    validate_factual_audit(audit_document, editor_document, cards)
    output = build_artifact(arguments, editor_document, cards, mode)
    output_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
    evidence_sha256 = hashlib.sha256(
        json.dumps(cards, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    diagnostics.update(
        {
            "status": "success",
            "artifact": output.name,
            "artifactSha256": output_sha256,
            "evidenceSha256": evidence_sha256,
            "tokenCostCny": round(
                float(research_diagnostics.get("tokenCostCny") or 0)
                + float(editor_diagnostics["tokenCostCny"])
                + float(audit_diagnostics["tokenCostCny"]),
                8,
            ),
            "webSearchCount": int(
                research_diagnostics.get("webSearchCount") or 0
            ),
            "webSearchCostCny": round(
                float(research_diagnostics.get("webSearchCostCny") or 0), 8
            ),
            "estimatedCostCny": round(estimated_cost, 8),
            "research": research_diagnostics,
            "editor": editor_diagnostics,
            "verifier": audit_diagnostics,
        }
    )
    return output, diagnostics


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        output, diagnostics = run(arguments)
    except (OSError, ValueError, QwenReportError) as exc:
        existing: dict[str, Any] = {}
        if arguments.diagnostics and arguments.diagnostics.exists():
            try:
                loaded = json.loads(arguments.diagnostics.read_text("utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
            except (OSError, json.JSONDecodeError):
                pass
        write_diagnostics(
            arguments.diagnostics,
            {
                **existing,
                "status": "failed",
                "model": arguments.model,
                "date": arguments.date,
                "errorType": type(exc).__name__,
                "error": str(exc),
            },
        )
        print(f"qwen report failed: {exc}", file=sys.stderr)
        return 1
    write_diagnostics(arguments.diagnostics, diagnostics)
    print(f"qwen report: wrote {output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
