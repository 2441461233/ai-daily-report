from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_data = load_script("build_data")
validate_content = load_script("validate_content")
check_daily_changes = load_script("check_daily_changes")


def news_item(headline: str) -> dict:
    return {
        "headline": headline,
        "summary": f"{headline}的可核验摘要。",
        "expanded": False,
        "priorityIds": ["test-lab:model-1"],
        "sources": [{"name": "官方", "url": "https://x.ai/news/example"}],
    }


def addendum(date: str, sequence: int, label: str = "补刊 1") -> dict:
    return {
        "date": date,
        "kind": "addendum",
        "label": label,
        "generatedAt": f"{date}T12:00:00+08:00",
        "oneLiner": "📌 补刊：补录已核验的旗舰模型发布。",
        "sections": [
            {
                "title": "🔥 AI 重要事件",
                "items": [news_item(f"补刊新闻 {sequence}")],
            }
        ],
    }


class BuildDataAddendumTests(unittest.TestCase):
    def test_build_preserves_main_and_addendum_in_filename_sequence(self) -> None:
        date = "2099-01-02"
        main = {
            "date": date,
            "label": "主刊",
            # Deliberately later: filename/archive sequence must win over time.
            "generatedAt": f"{date}T18:00:00+08:00",
            "oneLiner": "📌 今日一句话：主刊。",
            "sections": [
                {"title": "🔥 AI 重要事件", "items": [news_item("主刊新闻")]}
            ],
        }
        supplement = addendum(date, 2)
        supplement["generatedAt"] = f"{date}T09:00:00+08:00"
        reported = "\n".join(
            [
                f"## {date}（主刊）",
                "",
                f"- {date} | 主刊新闻",
                "",
                f"## {date}（补刊 1）",
                "",
                f"- {date} | 补刊新闻 2",
            ]
        )

        old_values = {
            name: getattr(build_data, name)
            for name in ("ARTIFACT_DIR", "LINKS_FILE", "REPORTED_FILE", "OUT_FILE")
        }
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                artifacts = root / "content" / "artifacts"
                artifacts.mkdir(parents=True)
                (artifacts / f"{date}-1.json").write_text(
                    json.dumps(main, ensure_ascii=False), "utf-8"
                )
                (artifacts / f"{date}-2.json").write_text(
                    json.dumps(supplement, ensure_ascii=False), "utf-8"
                )
                reported_file = root / "content" / "reported.md"
                reported_file.write_text(reported, "utf-8")
                links_file = root / "content" / "links.json"
                links_file.write_text("{}", "utf-8")
                output = root / "public" / "data" / "reports.json"

                build_data.ARTIFACT_DIR = artifacts
                build_data.LINKS_FILE = links_file
                build_data.REPORTED_FILE = reported_file
                build_data.OUT_FILE = output
                self.assertEqual(build_data.main(), 0)
                payload = json.loads(output.read_text("utf-8"))
        finally:
            for name, value in old_values.items():
                setattr(build_data, name, value)

        reports = payload["reports"]
        self.assertEqual([report["label"] for report in reports], ["主刊", "补刊 1"])
        self.assertEqual([report["seq"] for report in reports], [1, 2])
        self.assertNotIn("kind", reports[0])
        self.assertEqual(reports[1]["kind"], "addendum")
        headlines = [
            item["text"]
            for report in reports
            for section in report["sections"]
            for item in section["items"]
        ]
        self.assertEqual(headlines, ["主刊新闻", "补刊新闻 2"])

    def test_general_attachments_merge_into_main_not_addendum(self) -> None:
        date = "2099-01-02"
        main = {"date": date, "label": "主刊", "sections": []}
        supplement = {
            "date": date,
            "kind": "addendum",
            "label": "补刊 1",
            "sections": [{"title": "🔥 AI 重要事件", "items": [{"text": "补刊"}]}],
        }
        attachment = {
            "date": date,
            "attachTo": date,
            "sections": [{"title": "🧭 WayToAGI 知识库精选", "items": [{"text": "精选"}]}],
        }
        issues = build_data.apply_attachments([main, supplement], [attachment])
        self.assertEqual(
            [section["title"] for section in issues[0]["sections"]],
            ["🧭 WayToAGI 知识库精选"],
        )
        self.assertEqual(
            [section["title"] for section in issues[1]["sections"]],
            ["🔥 AI 重要事件"],
        )


class ValidateAddendumTests(unittest.TestCase):
    def validate_file(self, artifact: dict, sequence: int = 2):
        errors = validate_content.Errors()
        stats = validate_content.Stats()
        date = str(artifact["date"]).split()[0]
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / f"{date}-{sequence}.json"
            file.write_text(json.dumps(artifact, ensure_ascii=False), "utf-8")
            record = validate_content.validate_artifact_file(file, [], errors, stats)
        return record, errors.messages, stats

    def test_accepts_small_major_events_addendum(self) -> None:
        artifact = addendum("2099-01-02", 2)
        record, messages, stats = self.validate_file(artifact)
        self.assertEqual(messages, [])
        assert record is not None
        self.assertEqual(record.kind, "addendum")
        self.assertEqual(record.sequence, 2)
        self.assertEqual(stats.addenda, 1)
        self.assertEqual(stats.main_artifacts, 0)

    def test_rejects_non_major_or_multi_section_addendum(self) -> None:
        artifact = addendum("2099-01-02", 2)
        artifact["sections"].append(
            {"title": "💻 GitHub Trending", "items": [news_item("工具新闻")]}
        )
        _record, messages, _stats = self.validate_file(artifact)
        self.assertTrue(
            any("exactly one major-events section" in message for message in messages),
            messages,
        )

    def test_rejects_addendum_as_first_issue(self) -> None:
        artifact = addendum("2099-01-02", 1)
        _record, messages, _stats = self.validate_file(artifact, sequence=1)
        self.assertTrue(any("first same-day issue" in message for message in messages), messages)

    def test_main_cannot_smuggle_addendum_policy_with_kind_main(self) -> None:
        artifact = addendum("2099-01-02", 2)
        artifact["kind"] = "main"
        _record, messages, _stats = self.validate_file(artifact)
        self.assertTrue(any("must be omitted" in message for message in messages), messages)
        self.assertTrue(any("current main report" in message for message in messages), messages)

    def test_sequence_must_match_archive_position_and_have_main(self) -> None:
        date = "2099-01-02"
        main_file = Path(f"{date}-1.json")
        addendum_file = Path(f"{date}-3.json")
        records = [
            validate_content.ArtifactIssue(date, "主刊", main_file, 1, "main"),
            validate_content.ArtifactIssue(date, "补刊 1", addendum_file, 3, "addendum"),
        ]
        reported = [
            validate_content.ReportedIssue(date, "主刊", ["主刊新闻"], 1),
            validate_content.ReportedIssue(date, "补刊 1", ["补刊新闻"], 2),
        ]
        errors = validate_content.Errors()
        validate_content.validate_issue_sequences(records, reported, errors)
        self.assertTrue(any("archive position 2" in message for message in errors.messages))


class DailyChangeGuardAddendumTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        (self.root / "content" / "artifacts").mkdir(parents=True)
        (self.root / "public" / "data").mkdir(parents=True)
        (self.root / "content" / "reported.md").write_text("存档\n", "utf-8")
        (self.root / "public" / "data" / "reports.json").write_text("{}\n", "utf-8")
        main = {"date": self.today, "label": "主刊", "sections": [{"items": [1]}]}
        (self.root / "content" / "artifacts" / f"{self.today}-1.json").write_text(
            json.dumps(main, ensure_ascii=False), "utf-8"
        )
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.old_root = check_daily_changes.ROOT
        check_daily_changes.ROOT = self.root

    def tearDown(self) -> None:
        check_daily_changes.ROOT = self.old_root
        self.temp.cleanup()

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.root, check=True)

    def write_candidate(self, sequence: int, *, kind: str = "addendum") -> None:
        artifact = addendum(self.today, sequence)
        artifact["kind"] = kind
        path = self.root / "content" / "artifacts" / f"{self.today}-{sequence}.json"
        path.write_text(json.dumps(artifact, ensure_ascii=False), "utf-8")
        with (self.root / "content" / "reported.md").open("a", encoding="utf-8") as handle:
            handle.write(f"## {self.today}（补刊）\n- {self.today} | 补刊新闻\n")
        (self.root / "public" / "data" / "reports.json").write_text(
            '{"rebuilt": true}\n', "utf-8"
        )

    def run_guard(self) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = check_daily_changes.main([])
        return result, output.getvalue()

    def test_allows_next_same_day_addendum(self) -> None:
        self.write_candidate(2)
        result, output = self.run_guard()
        self.assertEqual(result, 0, output)

    def test_clean_tree_with_committed_main_is_idempotent(self) -> None:
        result, output = self.run_guard()
        self.assertEqual(result, 0, output)
        self.assertIn("clean tree with committed report", output)

    def test_rejects_skipped_addendum_number(self) -> None:
        self.write_candidate(3)
        result, output = self.run_guard()
        self.assertEqual(result, 1)
        self.assertIn("without duplicates or gaps", output)

    def test_rejects_second_main_instead_of_addendum(self) -> None:
        self.write_candidate(2, kind="main")
        result, output = self.run_guard()
        self.assertEqual(result, 1)
        self.assertIn("must declare kind 'addendum'", output)

    def test_rejects_edit_to_committed_main(self) -> None:
        path = self.root / "content" / "artifacts" / f"{self.today}-1.json"
        path.write_text('{"date": "changed"}\n', "utf-8")
        result, output = self.run_guard()
        self.assertEqual(result, 1)
        self.assertIn("existing report artifacts are immutable", output)

    def test_rejects_nested_artifact_json(self) -> None:
        nested = self.root / "content" / "artifacts" / "nested"
        nested.mkdir()
        (nested / f"{self.today}-2.json").write_text(
            json.dumps(addendum(self.today, 2), ensure_ascii=False),
            "utf-8",
        )

        result, output = self.run_guard()

        self.assertEqual(result, 1)
        self.assertIn("directly under content/artifacts", output)

    def test_reported_archive_is_append_only(self) -> None:
        self.write_candidate(2)
        reported = self.root / "content" / "reported.md"
        reported.write_text(
            f"历史被篡改\n## {self.today}（补刊）\n- {self.today} | 补刊新闻\n",
            "utf-8",
        )

        result, output = self.run_guard()

        self.assertEqual(result, 1)
        self.assertIn("append-only", output)

    def test_explicit_report_date_is_stable_and_strict(self) -> None:
        self.assertEqual(check_daily_changes.resolve_report_date(self.today), self.today)
        with self.assertRaises(ValueError):
            check_daily_changes.resolve_report_date("2099-02-30")


class DailyChangeGuardFirstRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        (self.root / "content" / "artifacts").mkdir(parents=True)
        (self.root / "public" / "data").mkdir(parents=True)
        (self.root / "content" / "reported.md").write_text("存档\n", "utf-8")
        (self.root / "public" / "data" / "reports.json").write_text("{}\n", "utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.root, check=True)
        self.old_root = check_daily_changes.ROOT
        check_daily_changes.ROOT = self.root

    def tearDown(self) -> None:
        check_daily_changes.ROOT = self.old_root
        self.temp.cleanup()

    def write_report(self, sequence: int, kind: str | None) -> None:
        document = addendum(self.today, sequence)
        if kind is None:
            document.pop("kind", None)
        else:
            document["kind"] = kind
        path = self.root / "content" / "artifacts" / f"{self.today}-{sequence}.json"
        path.write_text(json.dumps(document, ensure_ascii=False), "utf-8")

    def run_guard(self) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = check_daily_changes.main(["--date", self.today])
        return result, output.getvalue()

    def test_first_run_allows_main_plus_contiguous_addenda(self) -> None:
        self.write_report(1, None)
        self.write_report(2, "addendum")
        self.write_report(3, "addendum")
        with (self.root / "content" / "reported.md").open("a", encoding="utf-8") as handle:
            handle.write("changed\n")
        (self.root / "public" / "data" / "reports.json").write_text(
            '{"rebuilt": true}\n', "utf-8"
        )

        result, output = self.run_guard()

        self.assertEqual(result, 0, output)

    def test_clean_tree_without_committed_main_fails(self) -> None:
        result, output = self.run_guard()

        self.assertEqual(result, 1)
        self.assertIn(f"{self.today}-1.json", output)

    def test_clean_tree_with_addendum_as_first_committed_issue_fails(self) -> None:
        self.write_report(1, "addendum")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "bad sequence"], cwd=self.root, check=True)

        result, output = self.run_guard()

        self.assertEqual(result, 1)
        self.assertIn("first report must be a main report", output)

    def test_first_run_rejects_gap_and_second_main(self) -> None:
        self.write_report(1, None)
        self.write_report(3, None)
        with (self.root / "content" / "reported.md").open("a", encoding="utf-8") as handle:
            handle.write("changed\n")
        (self.root / "public" / "data" / "reports.json").write_text(
            '{"rebuilt": true}\n', "utf-8"
        )

        result, output = self.run_guard()

        self.assertEqual(result, 1)
        self.assertIn("contiguous", output)


if __name__ == "__main__":
    unittest.main()
