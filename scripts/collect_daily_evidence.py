#!/usr/bin/env python3
"""Collect dated public evidence independently of the language-model provider."""

from __future__ import annotations
import concurrent.futures
import hashlib
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit
import generate_qwen_report as shared

FEEDS = (
    ("OpenAI", "https://openai.com/news/rss.xml"),
    ("Google AI", "https://blog.google/technology/ai/rss/"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml"),
    ("NVIDIA", "https://blogs.nvidia.com/feed/"),
    ("Microsoft Research", "https://www.microsoft.com/en-us/research/feed/"),
    ("arXiv AI", "https://rss.arxiv.org/rss/cs.AI"),
    ("arXiv CV", "https://rss.arxiv.org/rss/cs.CV"),
    ("Replicate", "https://replicate.com/blog/rss"),
)


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.all_text = []
        self.article_text = []
        self.dates = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in {
                "article:published_time",
                "date",
                "datepublished",
                "pubdate",
                "publishdate",
            }:
                self.dates.append(attrs.get("content", ""))
        if tag == "time" and attrs.get("datetime"):
            self.dates.append(attrs["datetime"])
        if tag not in {
            "meta",
            "link",
            "img",
            "br",
            "hr",
            "input",
            "source",
            "area",
            "wbr",
            "embed",
            "param",
        }:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            self.stack = self.stack[: len(self.stack) - 1 - self.stack[::-1].index(tag)]

    def handle_data(self, data):
        if any(
            tag in self.stack
            for tag in (
                "script",
                "style",
                "nav",
                "header",
                "footer",
                "noscript",
                "svg",
                "button",
            )
        ):
            return
        if data.strip():
            self.all_text.append(data.strip())
            if "article" in self.stack or "main" in self.stack:
                self.article_text.append(data.strip())


def fetch(url, timeout=20):
    """Validate every redirect; never forward a credential to a source."""
    for _ in range(5):
        if urlsplit(url).username or urlsplit(url).password:
            raise ValueError("source URL must not contain credentials")
        shared.validate_public_source_url(url)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "evan-ai-daily-report/3.0",
                "Accept": "text/html,application/xml,application/atom+xml,application/rss+xml,application/json,text/plain",
            },
        )
        try:
            with urllib.request.build_opener(shared.NoRedirectHandler()).open(
                req, timeout=timeout
            ) as response:
                raw = response.read(3_000_001)
                if len(raw) > 3_000_000:
                    raise ValueError("source exceeds 3 MB")
                return url, raw, response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308} and exc.headers.get("Location"):
                url = urljoin(url, exc.headers["Location"])
                continue
            raise ValueError(f"HTTP {exc.code}") from None
    raise ValueError("too many redirects")


def parse_date(value):
    result = shared.parse_datetime(value)
    if result:
        return result.astimezone(timezone.utc)
    try:
        result = parsedate_to_datetime(value)
        return result.replace(tzinfo=result.tzinfo or timezone.utc).astimezone(
            timezone.utc
        )
    except (TypeError, ValueError, OverflowError):
        return None


def feed_entries(name, url, now):
    final, raw, _ = fetch(url)
    root = ET.fromstring(raw)
    result = []
    for item in root.findall(".//item"):
        date = parse_date(item.findtext("pubDate") or "")
        if date is None or not now - timedelta(hours=72) <= date <= now:
            continue
        link = item.findtext("link") or ""
        if not link:
            continue
        result.append(
            {
                "title": item.findtext("title") or "",
                "url": link,
                "publishedAt": date.isoformat(),
                "dateBasis": "publisher-feed",
                "feedUrl": final,
                "publisher": name,
                "feedDescription": item.findtext("description") or "",
                "feedRecordSha256": hashlib.sha256(ET.tostring(item)).hexdigest(),
            }
        )
    if name.startswith("arXiv"):
        for record in result:
            record["section"] = shared.SECTION_TITLES[3]
    return result[:12]


def discover_hn(query, now):
    params = {
        "query": query,
        "tags": "story",
        "hitsPerPage": 25,
        "numericFilters": f"created_at_i>{int((now - timedelta(hours=72)).timestamp())},points>2",
    }
    _, raw, _ = fetch("https://hn.algolia.com/api/v1/search?" + urlencode(params))
    records = []
    for hit in json.loads(raw).get("hits", []):
        url = hit.get("url")
        if not url or urlsplit(url).hostname in {
            "news.ycombinator.com",
            "youtube.com",
            "www.youtube.com",
        }:
            continue
        records.append(
            {
                "title": hit.get("title", ""),
                "url": url,
                "publishedAt": hit.get("created_at", ""),
                "dateBasis": "community-discussion",
                "discussionUrl": f"https://news.ycombinator.com/item?id={hit['objectID']}",
                "publisher": urlsplit(url).hostname,
            }
        )
    return records


def read_record(record, now):
    final, raw, kind = fetch(record["url"])
    decoded = raw.decode("utf-8", errors="replace")
    dates = []
    if kind in {"text/html", "application/xhtml+xml"}:
        parser = ArticleParser()
        parser.feed(decoded)
        body = " ".join(
            parser.article_text
            if len(" ".join(parser.article_text)) > 300
            else parser.all_text
        )
        dates = parser.dates
        dates += re.findall(r'"datePublished"\s*:\s*"([^"]+)"', decoded)
    elif kind in {
        "text/plain",
        "application/json",
        "application/xml",
        "text/xml",
        "application/atom+xml",
    }:
        body = decoded
    else:
        raise ValueError("source has no readable text")
    body = shared.clean_text(body, 8500)
    if len(body) < 160:
        raise ValueError("too little source text")
    result = {
        **record,
        "url": final,
        "text": body,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    if record["dateBasis"] == "community-discussion":
        found = next((parse_date(d) for d in dates if parse_date(d)), None)
        if found:
            if not now - timedelta(hours=72) <= found <= now:
                raise ValueError("original article is outside the 72-hour window")
            result.update(publishedAt=found.isoformat(), dateBasis="publisher-page")
    return result


def collect(now, builders, artifact_dir):
    errors = []
    discovered = []
    sources = []
    jobs = [("feed", name, url) for name, url in FEEDS] + [
        ("hn", q, "")
        for q in ("AI", "LLM", "AI video", "AI music", "agent startup", "AI image")
    ]

    def discovery(job):
        kind, name, url = job
        try:
            return (
                feed_entries(name, url, now)
                if kind == "feed"
                else discover_hn(name, now)
            )
        except Exception as exc:
            return {"source": url or name, "error": str(exc)[:180]}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(discovery, jobs):
            if isinstance(result, dict):
                errors.append(result)
            else:
                discovered.extend(result)
    previous = shared.fallback.existing_source_urls(artifact_dir)
    unique = {}
    for record in discovered:
        url = shared.normalize_url(record["url"])
        if url and url not in previous and not shared.is_reserved_attachment_url(url):
            unique.setdefault(url, record)

    def read(record):
        try:
            return read_record(record, now)
        except Exception as exc:
            description = shared.clean_text(record.get("feedDescription", ""), 2000)
            if record.get("dateBasis") == "publisher-feed" and len(description) >= 80:
                return {
                    **record,
                    "dateBasis": "publisher-feed-summary",
                    "text": record["title"]
                    + " "
                    + description
                    + " Published: "
                    + record["publishedAt"]
                    + " Evidence is limited to the publisher RSS summary; the full article was unavailable.",
                    "sha256": record["feedRecordSha256"],
                }
            return {"source": record["url"], "error": str(exc)[:180]}

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        for result in pool.map(read, list(unique.values())[:90]):
            if "text" in result:
                sources.append(result)
            else:
                errors.append(result)
    # arXiv abstracts are publisher-provided primary evidence, with submission dates.
    try:
        arxiv_url = "https://export.arxiv.org/api/query?" + urlencode(
            {
                "search_query": "cat:cs.AI OR cat:cs.LG OR cat:cs.CV",
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": 12,
            }
        )
        _, raw, _ = fetch(arxiv_url)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in ET.fromstring(raw).findall("a:entry", ns):
            published = parse_date(entry.findtext("a:published", "", ns))
            if not published or not now - timedelta(hours=72) <= published <= now:
                continue
            url = entry.findtext("a:id", "", ns).replace("http://", "https://")
            if shared.normalize_url(url) in previous:
                continue
            title = shared.clean_text(entry.findtext("a:title", "", ns), 400)
            abstract = shared.clean_text(entry.findtext("a:summary", "", ns), 6000)
            sources.append(
                {
                    "title": title,
                    "url": url,
                    "publishedAt": published.isoformat(),
                    "dateBasis": "publisher-abstract",
                    "publisher": "arXiv",
                    "text": title
                    + " Published: "
                    + published.isoformat()
                    + " Abstract: "
                    + abstract,
                    "sha256": hashlib.sha256(ET.tostring(entry)).hexdigest(),
                    "section": shared.SECTION_TITLES[3],
                }
            )
    except Exception as exc:
        errors.append({"source": "arXiv", "error": str(exc)[:180]})
    # Daily Trending is a dated observation, not a claim that a repository is new.
    trending = shared.fetch_github_trending_repositories()

    def repo(url):
        try:
            item = read_record(
                {
                    "title": url.removeprefix("https://github.com/"),
                    "url": url,
                    "publishedAt": now.isoformat(),
                    "dateBasis": "trending-observation",
                    "publisher": "GitHub Trending",
                },
                now,
            )
            if not re.search(
                r"\b(AI|LLM|agents?|language models?|diffusion|inference|GPT)\b",
                item["text"],
                re.I,
            ):
                return None
            item["text"] = (
                f"Observed in GitHub daily Trending on {now.date().isoformat()}. This does not establish a new release date. "
                + item["text"]
            )
            item["section"] = shared.SECTION_TITLES[5]
            return item
        except Exception as exc:
            return {"source": url, "error": str(exc)[:180]}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(repo, sorted(trending)):
            if result and "text" in result:
                sources.append(result)
            elif result:
                errors.append(result)
    # These curated feeds preserve the authors' posts, including dates and URLs.
    for card in shared.builder_cards(builders, now.replace(tzinfo=None)):
        published = parse_date(card["publishedAt"])
        url = card["sources"][0]["url"]
        if (
            not published
            or not now - timedelta(hours=72) <= published <= now
            or shared.normalize_url(url) in previous
        ):
            continue
        if len(re.sub(r"https?://\S+", "", card["facts"])) < 100:
            continue
        sources.append(
            {
                "title": card["title"],
                "url": url,
                "publishedAt": published.isoformat(),
                "dateBasis": "author-post-feed",
                "publisher": card["sources"][0]["name"],
                "text": card["facts"],
                "sha256": hashlib.sha256(card["facts"].encode()).hexdigest(),
            }
        )
    dedup = {}
    for s in sources:
        dedup.setdefault(shared.normalize_url(s["url"]), s)
    result = []
    for i, s in enumerate(dedup.values(), 1):
        result.append({"id": f"S{i:03d}", **s})
    return {
        "generatedAt": now.isoformat(),
        "sources": result,
        "errors": errors,
        "discoveredCount": len(unique),
        "trendingRepositories": sorted(trending),
    }
