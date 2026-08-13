from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Optional


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "waytoagi.py"
SPEC = importlib.util.spec_from_file_location("waytoagi", SCRIPT)
assert SPEC and SPEC.loader
waytoagi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(waytoagi)


def item(number: int, title: Optional[str] = None, summary: Optional[str] = None) -> str:
    title = title or f"标题 {number}"
    summary = summary or f"摘要 {number}，包含可用于日报的完整信息。"
    return (
        "<li>《"
        f'<a href="https://waytoagi.feishu.cn/wiki/Token{number}?from=from_copylink">{title}</a>'
        f"》{summary}</li>"
    )


def issue_html(items: str, *, extra: str = "") -> str:
    return f"""
    <html><head>
      <meta name="description" content="news-20990101 https://waytoagi.feishu.cn/wiki/MetaFake">
      <script>self.__next_f.push([1, 'news-20990102 /wiki/RscFake'])</script>
    </head><body>
      {extra}
      <div class="markdown-body blog-content"><ul>{items}</ul></div>
      <section class="recommendations">
        <a href="https://waytoagi.feishu.cn/wiki/RecommendedFake">相关推荐</a>
      </section>
    </body></html>
    """


class IndexTests(unittest.TestCase):
    def test_discovers_only_real_anchor_dates(self) -> None:
        html = """
        <meta content="/blog/news-20990101">
        <script>self.__next_f.push([1,"/blog/news-20990102"])</script>
        <a href="/blog/news-20260811">十一日</a>
        <a href="/zh/blog/news-20260810?from=home">十日 query 不符合 canonical route</a>
        <a href="/zh/blog/news-20260810">十日</a>
        <a href="/blog/news-20260811">重复</a>
        <a href="/blog/news-20260230">无效日期</a>
        """
        self.assertEqual(waytoagi.discover_stamps(html), ["20260811", "20260810"])

    def test_index_structure_error_is_explicit(self) -> None:
        with self.assertRaisesRegex(waytoagi.CollectionError, "contains no linked"):
            waytoagi.discover_stamps('<script>"/blog/news-20260811"</script>')


class IssueTests(unittest.TestCase):
    def test_parses_all_six_body_items_only(self) -> None:
        body = "".join(item(number) for number in range(1, 7))
        parsed = waytoagi.parse_issue(issue_html(body), "20260811")
        self.assertEqual(len(parsed), 6)
        self.assertEqual(parsed[0], {
            "title": "标题 1",
            "summary": "摘要 1，包含可用于日报的完整信息。",
            "url": "https://waytoagi.feishu.cn/wiki/Token1",
        })
        self.assertEqual(parsed[-1]["title"], "标题 6")
        self.assertNotIn("RecommendedFake", json.dumps(parsed))
        self.assertNotIn("MetaFake", json.dumps(parsed))
        self.assertNotIn("RscFake", json.dumps(parsed))

    def test_distinct_source_items_may_share_an_upstream_link(self) -> None:
        duplicate = item(1) + item(1, title="重复标题", summary="另一段摘要")
        parsed = waytoagi.parse_issue(issue_html(duplicate), "20260811")
        self.assertEqual(len(parsed), 2)
        self.assertEqual([entry["title"] for entry in parsed], ["标题 1", "重复标题"])

    def test_missing_target_block_is_a_structure_error(self) -> None:
        with self.assertRaisesRegex(waytoagi.CollectionError, "expected exactly one"):
            waytoagi.parse_issue(f"<html><body><ul>{item(1)}</ul></body></html>", "20260811")

    def test_incomplete_body_item_is_a_structure_error(self) -> None:
        bad = item(1) + '<li><a href="https://other.example/article">无飞书原文</a>摘要</li>'
        with self.assertRaisesRegex(waytoagi.CollectionError, "parsed 1 complete items from 2"):
            waytoagi.parse_issue(issue_html(bad), "20260811")

    def test_rolling_log_link_is_not_accepted_as_item_specific(self) -> None:
        rolling = (
            '<li><a href="https://waytoagi.feishu.cn/wiki/'
            f'{waytoagi.ROLLING_LOG_TOKEN}">聚合日志</a>摘要</li>'
        )
        with self.assertRaisesRegex(waytoagi.CollectionError, "no complete Feishu-linked"):
            waytoagi.parse_issue(issue_html(rolling), "20260811")


class CollectionTests(unittest.TestCase):
    def test_collect_uses_index_dates_and_emits_unified_schema(self) -> None:
        index = '<a href="/blog/news-20260811">latest</a>'
        page = issue_html(item(1))

        def fetch(url: str) -> str:
            return index if url == waytoagi.INDEX_URL else page

        original_artifacts = waytoagi.ARTIFACTS
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                waytoagi.ARTIFACTS = root / "artifacts"
                payload = waytoagi.collect(
                    fetch=fetch,
                    now=datetime.fromisoformat("2026-08-13T10:00:00+08:00"),
                )
        finally:
            waytoagi.ARTIFACTS = original_artifacts

        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["sourceIndex"], waytoagi.INDEX_URL)
        self.assertEqual(payload["generatedAt"], "2026-08-13T10:00:00+08:00")
        self.assertEqual(payload["issues"][0]["sourceItemCount"], 1)
        self.assertEqual(payload["issues"][0]["date"], "2026-08-11")

    def test_default_starts_at_project_archive_boundary_and_checks_recent_archive(self) -> None:
        available = ["20260814", "20260811", "20260728", "20260727"]
        original_artifacts = waytoagi.ARTIFACTS
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                waytoagi.ARTIFACTS = root / "artifacts"
                waytoagi.ARTIFACTS.mkdir()
                (waytoagi.ARTIFACTS / "waytoagi-20260811.json").write_text("{}", "utf-8")
                self.assertEqual(
                    waytoagi.select_stamps(
                        available,
                        requested=None,
                        include_consumed=False,
                        now=datetime.fromisoformat("2026-08-13T10:00:00+08:00"),
                    ),
                    ["20260728", "20260811"],
                )
                self.assertEqual(
                    waytoagi.select_stamps(
                        available,
                        requested=["20260811"],
                        include_consumed=True,
                        now=datetime.fromisoformat("2026-08-13T10:00:00+08:00"),
                    ),
                    ["20260811"],
                )
        finally:
            waytoagi.ARTIFACTS = original_artifacts

    def test_refresh_outputs_only_when_archived_item_sequence_changes(self) -> None:
        index = '<a href="/blog/news-20260811">latest</a>'
        original_artifacts = waytoagi.ARTIFACTS
        try:
            with tempfile.TemporaryDirectory() as directory:
                waytoagi.ARTIFACTS = Path(directory)
                old_items = waytoagi.parse_issue(issue_html(item(1)), "20260811")
                old_issue = waytoagi.issue_record("20260811", old_items)
                artifact = waytoagi.attachment_for(old_issue, "2026-08-11T23:59:00+08:00")
                (waytoagi.ARTIFACTS / "waytoagi-20260811.json").write_text(
                    json.dumps(artifact, ensure_ascii=False), "utf-8"
                )

                def unchanged_fetch(url: str) -> str:
                    return index if url == waytoagi.INDEX_URL else issue_html(item(1))

                unchanged = waytoagi.collect(
                    fetch=unchanged_fetch,
                    now=datetime.fromisoformat("2026-08-13T10:00:00+08:00"),
                )
                self.assertEqual(unchanged["issues"], [])

                def changed_fetch(url: str) -> str:
                    return index if url == waytoagi.INDEX_URL else issue_html(item(1) + item(2))

                changed = waytoagi.collect(
                    fetch=changed_fetch,
                    now=datetime.fromisoformat("2026-08-13T10:00:00+08:00"),
                )
                self.assertEqual(changed["issues"][0]["sourceItemCount"], 2)
        finally:
            waytoagi.ARTIFACTS = original_artifacts

    def test_refresh_detects_source_order_changes(self) -> None:
        index = '<a href="/blog/news-20260811">latest</a>'
        original_artifacts = waytoagi.ARTIFACTS
        try:
            with tempfile.TemporaryDirectory() as directory:
                waytoagi.ARTIFACTS = Path(directory)
                old_items = waytoagi.parse_issue(issue_html(item(1) + item(2)), "20260811")
                artifact = waytoagi.attachment_for(
                    waytoagi.issue_record("20260811", old_items),
                    "2026-08-11T23:59:00+08:00",
                )
                (waytoagi.ARTIFACTS / "waytoagi-20260811.json").write_text(
                    json.dumps(artifact, ensure_ascii=False), "utf-8"
                )

                def reordered_fetch(url: str) -> str:
                    return index if url == waytoagi.INDEX_URL else issue_html(item(2) + item(1))

                refreshed = waytoagi.collect(
                    fetch=reordered_fetch,
                    now=datetime.fromisoformat("2026-08-13T10:00:00+08:00"),
                )
                self.assertEqual(
                    [entry["url"] for entry in refreshed["issues"][0]["items"]],
                    [
                        "https://waytoagi.feishu.cn/wiki/Token2",
                        "https://waytoagi.feishu.cn/wiki/Token1",
                    ],
                )
        finally:
            waytoagi.ARTIFACTS = original_artifacts

    def test_requested_date_must_exist_in_index(self) -> None:
        with self.assertRaisesRegex(waytoagi.CollectionError, "not linked"):
            waytoagi.select_stamps(
                ["20260811"],
                requested=["20260812"],
                include_consumed=True,
                now=datetime.fromisoformat("2026-08-13T10:00:00+08:00"),
            )

    def test_attachment_matches_existing_schema(self) -> None:
        issue = waytoagi.issue_record("20260811", waytoagi.parse_issue(issue_html(item(1)), "20260811"))
        artifact = waytoagi.attachment_for(issue, "2026-08-11T23:59:00+08:00")
        self.assertEqual(artifact["date"], "2026-08-11")
        self.assertEqual(artifact["attachTo"], "2026-08-11")
        output_item = artifact["sections"][0]["items"][0]
        self.assertFalse(output_item["expanded"])
        self.assertEqual(output_item["sources"][0]["url"], waytoagi.ISSUE_URL.format("20260811"))
        self.assertEqual(output_item["sources"][1]["url"], "https://waytoagi.feishu.cn/wiki/Token1")

    def test_write_artifacts_writes_complete_attachment(self) -> None:
        issue = waytoagi.issue_record("20260811", waytoagi.parse_issue(issue_html(item(1)), "20260811"))
        original_artifacts = waytoagi.ARTIFACTS
        try:
            with tempfile.TemporaryDirectory() as directory:
                waytoagi.ARTIFACTS = Path(directory)
                waytoagi.write_artifacts([issue])
                document = json.loads(
                    (waytoagi.ARTIFACTS / "waytoagi-20260811.json").read_text("utf-8")
                )
        finally:
            waytoagi.ARTIFACTS = original_artifacts
        self.assertEqual(document["generatedAt"], "2026-08-11T23:59:00+08:00")
        self.assertEqual(document["sections"][0]["items"][0]["headline"], "标题 1")


if __name__ == "__main__":
    unittest.main()
