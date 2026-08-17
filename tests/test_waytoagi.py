from __future__ import annotations

import importlib.util
import http.client
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Optional
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "waytoagi.py"
SPEC = importlib.util.spec_from_file_location("waytoagi", SCRIPT)
assert SPEC and SPEC.loader
waytoagi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(waytoagi)

VALIDATOR_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_waytoagi_run.py"
VALIDATOR_SPEC = importlib.util.spec_from_file_location("validate_waytoagi_run", VALIDATOR_SCRIPT)
assert VALIDATOR_SPEC and VALIDATOR_SPEC.loader
validate_waytoagi_run = importlib.util.module_from_spec(VALIDATOR_SPEC)
sys.modules[VALIDATOR_SPEC.name] = validate_waytoagi_run
VALIDATOR_SPEC.loader.exec_module(validate_waytoagi_run)


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
        self.assertEqual(payload["mode"], "automatic")
        self.assertEqual(payload["refreshDays"], 14)
        self.assertEqual(payload["sourceStatus"], {"status": "ok"})
        self.assertEqual(payload["issues"][0]["sourceItemCount"], 1)
        self.assertEqual(payload["issues"][0]["date"], "2026-08-11")
        self.assertEqual(payload["refreshErrors"], [])

    def test_archived_refresh_failure_is_recorded_and_new_issue_continues(self) -> None:
        index = "".join(
            [
                '<a href="/blog/news-20260812">new</a>',
                '<a href="/blog/news-20260811">archived</a>',
            ]
        )
        original_artifacts = waytoagi.ARTIFACTS
        try:
            with tempfile.TemporaryDirectory() as directory:
                waytoagi.ARTIFACTS = Path(directory)
                archived_items = waytoagi.parse_issue(issue_html(item(1)), "20260811")
                archived = waytoagi.attachment_for(
                    waytoagi.issue_record("20260811", archived_items),
                    "2026-08-11T23:59:00+08:00",
                )
                (waytoagi.ARTIFACTS / "waytoagi-20260811.json").write_text(
                    json.dumps(archived, ensure_ascii=False), "utf-8"
                )

                def fetch(url: str) -> str:
                    if url == waytoagi.INDEX_URL:
                        return index
                    if url.endswith("20260811"):
                        raise waytoagi.CollectionError(f"GET {url} returned HTTP 500")
                    return issue_html(item(2))

                stderr = StringIO()
                with redirect_stderr(stderr):
                    payload = waytoagi.collect(
                        fetch=fetch,
                        now=datetime.fromisoformat("2026-08-13T10:00:00+08:00"),
                    )
        finally:
            waytoagi.ARTIFACTS = original_artifacts

        self.assertEqual([issue["stamp"] for issue in payload["issues"]], ["20260812"])
        self.assertEqual(
            payload["refreshErrors"],
            [
                {
                    "severity": "warning",
                    "code": "archived_issue_refresh_failed",
                    "stage": "refresh",
                    "stamp": "20260811",
                    "date": "2026-08-11",
                    "sourceUrl": waytoagi.ISSUE_URL.format("20260811"),
                    "message": (
                        f"GET {waytoagi.ISSUE_URL.format('20260811')} returned HTTP 500"
                    ),
                }
            ],
        )
        self.assertIn("warning: skipped archived issue refresh 20260811", stderr.getvalue())

    def test_unarchived_issue_fetch_failure_remains_fatal(self) -> None:
        index = '<a href="/blog/news-20260812">new</a>'

        def fetch(url: str) -> str:
            if url == waytoagi.INDEX_URL:
                return index
            raise waytoagi.CollectionError(f"GET {url} timed out")

        original_artifacts = waytoagi.ARTIFACTS
        try:
            with tempfile.TemporaryDirectory() as directory:
                waytoagi.ARTIFACTS = Path(directory)
                with self.assertRaisesRegex(waytoagi.CollectionError, "timed out"):
                    waytoagi.collect(
                        fetch=fetch,
                        now=datetime.fromisoformat("2026-08-13T10:00:00+08:00"),
                    )
        finally:
            waytoagi.ARTIFACTS = original_artifacts

    def test_explicit_archived_refresh_failure_remains_fatal(self) -> None:
        index = '<a href="/blog/news-20260811">archived</a>'
        original_artifacts = waytoagi.ARTIFACTS
        try:
            with tempfile.TemporaryDirectory() as directory:
                waytoagi.ARTIFACTS = Path(directory)
                archived_items = waytoagi.parse_issue(issue_html(item(1)), "20260811")
                archived = waytoagi.attachment_for(
                    waytoagi.issue_record("20260811", archived_items),
                    "2026-08-11T23:59:00+08:00",
                )
                (waytoagi.ARTIFACTS / "waytoagi-20260811.json").write_text(
                    json.dumps(archived, ensure_ascii=False), "utf-8"
                )

                def fetch(url: str) -> str:
                    if url == waytoagi.INDEX_URL:
                        return index
                    raise waytoagi.CollectionError("explicit refresh failed")

                with self.assertRaisesRegex(waytoagi.CollectionError, "explicit refresh failed"):
                    waytoagi.collect(
                        fetch=fetch,
                        requested=["20260811"],
                        now=datetime.fromisoformat("2026-08-13T10:00:00+08:00"),
                    )
        finally:
            waytoagi.ARTIFACTS = original_artifacts

    def test_index_fetch_failure_remains_fatal(self) -> None:
        def fetch(_url: str) -> str:
            raise waytoagi.CollectionError("index unavailable")

        with self.assertRaisesRegex(waytoagi.CollectionError, "index unavailable"):
            waytoagi.collect(fetch=fetch)

    def test_automatic_source_unavailable_is_explicit_and_empty(self) -> None:
        def unavailable_index(_url: str) -> str:
            raise waytoagi.CollectionError("index HTTP 524")

        payload = waytoagi.collect(
            fetch=unavailable_index,
            now=datetime.fromisoformat("2026-08-13T10:00:00+08:00"),
            refresh_days=0,
            allow_source_unavailable=True,
        )
        self.assertEqual(payload["mode"], "automatic")
        self.assertEqual(payload["refreshDays"], 0)
        self.assertEqual(
            payload["sourceStatus"],
            {"status": "unavailable", "message": "index HTTP 524"},
        )
        self.assertEqual(payload["issues"], [])
        self.assertEqual(payload["refreshErrors"], [])

    def test_cli_source_unavailable_payload_exits_zero(self) -> None:
        payload = {
            "schemaVersion": 1,
            "sourceIndex": waytoagi.INDEX_URL,
            "generatedAt": "2026-08-13T10:00:00+08:00",
            "mode": "automatic",
            "refreshDays": 0,
            "sourceStatus": {"status": "unavailable", "message": "index HTTP 524"},
            "issues": [],
            "refreshErrors": [],
        }
        stdout = StringIO()
        with (
            mock.patch.object(waytoagi, "collect", return_value=payload) as collect,
            redirect_stdout(stdout),
        ):
            result = waytoagi.main(
                ["--refresh-days", "0", "--allow-source-unavailable"]
            )
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stdout.getvalue()), payload)
        self.assertTrue(collect.call_args.kwargs["allow_source_unavailable"])

    def test_new_issue_unavailable_can_degrade_only_in_automatic_mode(self) -> None:
        index = '<a href="/blog/news-20260812">new</a>'

        def fetch(url: str) -> str:
            if url == waytoagi.INDEX_URL:
                return index
            raise waytoagi.CollectionError("new issue timed out")

        original_artifacts = waytoagi.ARTIFACTS
        try:
            with tempfile.TemporaryDirectory() as directory:
                waytoagi.ARTIFACTS = Path(directory)
                payload = waytoagi.collect(
                    fetch=fetch,
                    now=datetime.fromisoformat("2026-08-13T10:00:00+08:00"),
                    allow_source_unavailable=True,
                )
                with self.assertRaisesRegex(
                    waytoagi.CollectionError,
                    "valid only in automatic collection mode",
                ):
                    waytoagi.collect(
                        fetch=fetch,
                        requested=["20260812"],
                        now=datetime.fromisoformat("2026-08-13T10:00:00+08:00"),
                        allow_source_unavailable=True,
                    )
        finally:
            waytoagi.ARTIFACTS = original_artifacts
        self.assertEqual(payload["sourceStatus"]["status"], "unavailable")
        self.assertEqual(payload["issues"], [])

    def test_fetch_html_retries_and_wraps_incomplete_read(self) -> None:
        failure = http.client.IncompleteRead(b"partial", 100)
        with (
            mock.patch.object(waytoagi.urllib.request, "urlopen", side_effect=failure) as urlopen,
            mock.patch.object(waytoagi, "sleep"),
        ):
            with self.assertRaisesRegex(
                waytoagi.CollectionError, "IncompleteRead"
            ):
                waytoagi.fetch_html("https://example.test/issue")
        self.assertEqual(urlopen.call_count, waytoagi.FETCH_ATTEMPTS)

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


class ValidationCompatibilityTests(unittest.TestCase):
    def test_validator_accepts_structured_archived_refresh_warning(self) -> None:
        payload = {
            "schemaVersion": 1,
            "sourceIndex": waytoagi.INDEX_URL,
            "generatedAt": "2026-08-13T10:00:00+08:00",
            "mode": "automatic",
            "refreshDays": 14,
            "sourceStatus": {"status": "ok"},
            "issues": [],
            "refreshErrors": [
                waytoagi.refresh_error(
                    "20260811", waytoagi.CollectionError("temporary timeout")
                )
            ],
        }
        old_root = validate_waytoagi_run.ROOT
        old_artifacts = validate_waytoagi_run.ARTIFACTS
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                artifacts = root / "content" / "artifacts"
                artifacts.mkdir(parents=True)
                head_archive = waytoagi.attachment_for(
                    waytoagi.issue_record(
                        "20260811", waytoagi.parse_issue(issue_html(item(1)), "20260811")
                    ),
                    "2026-08-11T23:59:00+08:00",
                )
                input_path = root / "waytoagi.json"
                input_path.write_text(json.dumps(payload), "utf-8")
                manifest = root / "waytoagi.changed"
                manifest.write_text("", "utf-8")
                validate_waytoagi_run.ROOT = root
                validate_waytoagi_run.ARTIFACTS = artifacts
                def git_output(command: list[str], **_kwargs: object) -> str:
                    if command[1] == "show":
                        return json.dumps(head_archive, ensure_ascii=False)
                    return ""

                with mock.patch.object(
                    validate_waytoagi_run.subprocess, "check_output", side_effect=git_output
                ):
                    failures = validate_waytoagi_run.validate_run(input_path, manifest)
                    payload["mode"] = "requested"
                    input_path.write_text(json.dumps(payload), "utf-8")
                    explicit_failures = validate_waytoagi_run.validate_run(
                        input_path, manifest
                    )
        finally:
            validate_waytoagi_run.ROOT = old_root
            validate_waytoagi_run.ARTIFACTS = old_artifacts
        self.assertEqual(failures, [])
        self.assertTrue(
            any(
                "only when $.mode is 'automatic'" in failure.message
                for failure in explicit_failures
            ),
            explicit_failures,
        )

    def test_validator_rejects_malformed_archive_from_head(self) -> None:
        failures: list[validate_waytoagi_run.Failure] = []
        with mock.patch.object(
            validate_waytoagi_run.subprocess, "check_output", return_value="{}"
        ):
            validate_waytoagi_run.validate_head_archive(
                "20260811", failures, "$.refreshErrors[0]"
            )
        self.assertTrue(
            any("date must be" in failure.message for failure in failures), failures
        )

    def test_validator_rejects_warning_for_unarchived_issue(self) -> None:
        payload = {
            "schemaVersion": 1,
            "sourceIndex": waytoagi.INDEX_URL,
            "generatedAt": "2026-08-13T10:00:00+08:00",
            "mode": "automatic",
            "refreshDays": 14,
            "sourceStatus": {"status": "ok"},
            "issues": [],
            "refreshErrors": [
                waytoagi.refresh_error(
                    "20260812", waytoagi.CollectionError("temporary timeout")
                )
            ],
        }
        old_root = validate_waytoagi_run.ROOT
        old_artifacts = validate_waytoagi_run.ARTIFACTS
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                artifacts = root / "content" / "artifacts"
                artifacts.mkdir(parents=True)
                input_path = root / "waytoagi.json"
                input_path.write_text(json.dumps(payload), "utf-8")
                manifest = root / "waytoagi.changed"
                manifest.write_text("", "utf-8")
                validate_waytoagi_run.ROOT = root
                validate_waytoagi_run.ARTIFACTS = artifacts
                def git_output(command: list[str], **_kwargs: object) -> str:
                    if command[1] == "show":
                        raise subprocess.CalledProcessError(128, command)
                    return ""

                with mock.patch.object(
                    validate_waytoagi_run.subprocess, "check_output", side_effect=git_output
                ):
                    failures = validate_waytoagi_run.validate_run(input_path, manifest)
        finally:
            validate_waytoagi_run.ROOT = old_root
            validate_waytoagi_run.ARTIFACTS = old_artifacts
        self.assertTrue(
            any("archive tracked in HEAD" in failure.message for failure in failures),
            failures,
        )

    def test_validator_accepts_automatic_source_unavailable_with_empty_manifest(self) -> None:
        payload = {
            "schemaVersion": 1,
            "sourceIndex": waytoagi.INDEX_URL,
            "generatedAt": "2026-08-13T10:00:00+08:00",
            "mode": "automatic",
            "refreshDays": 0,
            "sourceStatus": {"status": "unavailable", "message": "index HTTP 524"},
            "issues": [],
            "refreshErrors": [],
        }
        old_root = validate_waytoagi_run.ROOT
        old_artifacts = validate_waytoagi_run.ARTIFACTS
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                artifacts = root / "content" / "artifacts"
                artifacts.mkdir(parents=True)
                input_path = root / "waytoagi.json"
                input_path.write_text(json.dumps(payload), "utf-8")
                manifest = root / "waytoagi.changed"
                manifest.write_text("", "utf-8")
                validate_waytoagi_run.ROOT = root
                validate_waytoagi_run.ARTIFACTS = artifacts
                with mock.patch.object(
                    validate_waytoagi_run.subprocess,
                    "check_output",
                    return_value="",
                ):
                    failures = validate_waytoagi_run.validate_run(input_path, manifest)
                    payload["mode"] = "requested"
                    payload["issues"] = [{}]
                    input_path.write_text(json.dumps(payload), "utf-8")
                    manifest.write_text("content/artifacts/waytoagi-20260812.json\n", "utf-8")
                    invalid = validate_waytoagi_run.validate_run(input_path, manifest)
        finally:
            validate_waytoagi_run.ROOT = old_root
            validate_waytoagi_run.ARTIFACTS = old_artifacts

        self.assertEqual(failures, [])
        messages = [failure.message for failure in invalid]
        self.assertTrue(any("only in automatic mode" in message for message in messages), messages)
        self.assertTrue(any("must be empty when sourceStatus" in message for message in messages), messages)


if __name__ == "__main__":
    unittest.main()
