from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fallback = load_script("build_fallback_report")
validate_content = load_script("validate_content")


class FallbackReportTests(unittest.TestCase):
    @staticmethod
    def arguments(
        root: Path,
        *,
        priority: Path | None = None,
        waytoagi: Path | None = None,
        archive_only: bool = False,
    ) -> argparse.Namespace:
        builders = root / "builders.json"
        if not builders.exists():
            builders.write_text('{"x": []}', "utf-8")
        return argparse.Namespace(
            date="2026-08-24",
            builders=builders,
            priority=priority,
            waytoagi=waytoagi,
            generated_at="2026-08-24T03:00:00Z",
            artifact_dir=root / "artifacts",
            reported=root / "reported.md",
            max_items=14,
            min_items=3,
            archive_only=archive_only,
        )

    def test_archive_only_does_not_create_a_missing_main_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            builders = root / "builders.json"
            builders.write_text('{"x": []}', "utf-8")
            reported = root / "reported.md"
            reported.write_text("", "utf-8")
            arguments = argparse.Namespace(
                date="2026-08-24",
                builders=builders,
                priority=None,
                waytoagi=None,
                generated_at="2026-08-24T03:00:00Z",
                artifact_dir=artifacts,
                reported=reported,
                max_items=14,
                min_items=3,
                archive_only=True,
            )

            self.assertIsNone(fallback.build_report(arguments))
            self.assertFalse((artifacts / "2026-08-24-1.json").exists())
            self.assertEqual(reported.read_text("utf-8"), "")

    def test_builds_a_valid_source_linked_recovery_edition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            (artifacts / "2026-08-18-1.json").write_text(
                json.dumps({"label": "第十七期", "sections": []}), "utf-8"
            )
            reported = root / "reported.md"
            reported.write_text("", "utf-8")
            builders = root / "builders.json"
            tweets = []
            texts = (
                "We released a frontier model with stronger safety evaluation and a new public benchmark today.",
                "A practical video workflow now turns a product brief into editable scenes and final exports.",
                "This research explains why long context agents lose important instructions during tool use.",
                "Our startup reached product market fit by automating customer onboarding and support workflows.",
                "The new agent system can plan a task, inspect results, and recover from failed tool calls.",
                "We measured inference latency across several production models and published the full results.",
                "A small team launched an AI SaaS and shared its revenue, pricing, and distribution lessons.",
                "The latest voice and music tools make it easier to build interactive media prototypes.",
                "Security testing found a prompt injection path and documents the mitigation for agent builders.",
                "Teams are using simulation to train agents against realistic customer feedback at scale.",
            )
            for index, text in enumerate(texts):
                tweets.append(
                    {
                        "id": str(1000 + index),
                        "text": text,
                        "createdAt": f"2026-08-23T0{index % 9}:00:00.000Z",
                        "url": f"https://x.com/builder/status/{1000 + index}",
                        "likes": 100 - index,
                        "retweets": 10,
                        "replies": 5,
                    }
                )
            builders.write_text(
                json.dumps(
                    {
                        "x": [
                            {
                                "name": "Builder",
                                "handle": "builder",
                                "bio": "Building AI products",
                                "tweets": tweets,
                            }
                        ]
                    }
                ),
                "utf-8",
            )
            priority = root / "priority.json"
            priority.write_text('{"candidates": []}', "utf-8")

            arguments = argparse.Namespace(
                date="2026-08-24",
                builders=builders,
                priority=priority,
                waytoagi=None,
                generated_at="2026-08-24T03:00:00Z",
                artifact_dir=artifacts,
                reported=reported,
                max_items=14,
                min_items=8,
                archive_only=False,
            )
            output = fallback.build_report(arguments)

            self.assertIsNotNone(output)
            assert output is not None
            document = json.loads(output.read_text("utf-8"))
            self.assertTrue(document["fallback"])
            self.assertEqual(document["label"], "第十八期·自动恢复版")
            self.assertGreaterEqual(
                sum(len(section["items"]) for section in document["sections"]), 8
            )
            self.assertIn("第十八期·自动恢复版", reported.read_text("utf-8"))

            errors = validate_content.Errors()
            stats = validate_content.Stats()
            archive = [
                validate_content.ReportedIssue(
                    date="2026-08-24",
                    label="第十八期·自动恢复版",
                    items=["recovery"],
                    line=1,
                )
            ]
            record = validate_content.validate_artifact_file(
                output, archive, errors, stats
            )
            self.assertIsNotNone(record)
            self.assertEqual(errors.messages, [])

    def test_waytoagi_archive_requires_exact_trusted_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory)
            issue = {
                "stamp": "20260821",
                "date": "2026-08-21",
                "items": [{"title": "第一条"}, {"title": "第二条"}],
            }
            (artifacts / "waytoagi-20260821.json").write_text(
                json.dumps(
                    {
                        "sections": [
                            {
                                "items": [
                                    {"headline": "第一条"},
                                    {"headline": "第二条"},
                                ]
                            }
                        ]
                    }
                ),
                "utf-8",
            )

            pending = fallback.pending_waytoagi_archive_items(
                {"issues": [issue]}, artifacts, ""
            )

            self.assertEqual(
                pending,
                ["WayToAGI 2026-08-21：第一条", "WayToAGI 2026-08-21：第二条"],
            )
            self.assertEqual(
                fallback.pending_waytoagi_archive_items(
                    {"issues": [issue]}, artifacts, "\n".join(pending)
                ),
                [],
            )

    def test_required_priority_candidate_is_deduped_by_id_not_shared_url(self) -> None:
        candidate = {
            "id": "lab:model-2",
            "title": "Lab 发布 Model 2",
            "url": "https://example.com/release-notes",
            "required": True,
            "officialSource": "Lab",
            "matchTerms": ["Model", "2"],
        }

        selected = fallback.priority_candidates(
            {"candidates": [candidate]}, {"lab:model-1"}
        )

        self.assertEqual([item["priorityIds"] for item in selected], [["lab:model-2"]])
        self.assertEqual(
            fallback.priority_candidates(
                {"candidates": [candidate]}, {"lab:model-2"}
            ),
            [],
        )

    def test_existing_main_with_uncovered_priority_writes_recovery_addendum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            (artifacts / "2026-08-24-1.json").write_text(
                json.dumps(
                    {
                        "date": "2026-08-24 星期一",
                        "label": "第一期",
                        "generatedAt": "2026-08-24T10:00:00+08:00",
                        "oneLiner": "📌 今日一句话：主刊。",
                        "sections": [
                            {
                                "title": "🔥 AI 重要事件",
                                "items": [
                                    {
                                        "headline": "主刊事件",
                                        "summary": "主刊摘要。",
                                        "expanded": False,
                                        "sources": [
                                            {
                                                "name": "主刊来源（单一来源）",
                                                "url": "https://example.com/main",
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                "utf-8",
            )
            (root / "reported.md").write_text(
                "## 2026-08-24（第一期）\n\n- 2026-08-24 | 主刊事件\n",
                "utf-8",
            )
            priority = root / "priority.json"
            priority.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "id": "lab:model-2",
                                "title": "Lab 发布 Model 2",
                                "summary": "Model 2 已正式开放。",
                                "url": "https://github.com/example/model-2",
                                "officialSource": "Lab",
                                "matchTerms": ["Model", "2"],
                                "required": True,
                            }
                        ]
                    }
                ),
                "utf-8",
            )

            fallback.build_report(self.arguments(root, priority=priority))

            addendum_path = artifacts / "2026-08-24-2.json"
            addendum = json.loads(addendum_path.read_text("utf-8"))
            self.assertEqual(addendum["kind"], "addendum")
            self.assertEqual(addendum["label"], "第二期·自动恢复补刊")
            self.assertEqual(
                addendum["sections"][0]["items"][0]["priorityIds"],
                ["lab:model-2"],
            )
            self.assertIn(
                "## 2026-08-24（第二期·自动恢复补刊）",
                (root / "reported.md").read_text("utf-8"),
            )
            errors = validate_content.Errors()
            stats = validate_content.Stats()
            record = validate_content.validate_artifact_file(
                addendum_path,
                [
                    validate_content.ReportedIssue(
                        date="2026-08-24",
                        label="第二期·自动恢复补刊",
                        items=["Lab 发布 Model 2"],
                        line=1,
                    )
                ],
                errors,
                stats,
            )
            self.assertIsNotNone(record)
            self.assertEqual(errors.messages, [])

    def test_archive_only_repairs_orphan_main_heading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            (artifacts / "2026-08-24-1.json").write_text(
                json.dumps(
                    {
                        "label": "第一期·自动恢复版",
                        "sections": [
                            {"title": "🌍 海外观察", "items": [{"headline": "恢复事件"}]}
                        ],
                    }
                ),
                "utf-8",
            )
            (root / "reported.md").write_text("", "utf-8")

            fallback.build_report(self.arguments(root, archive_only=True))

            self.assertIn(
                "## 2026-08-24（第一期·自动恢复版）\n\n- 2026-08-24 | 恢复事件",
                (root / "reported.md").read_text("utf-8"),
            )

    def test_archive_only_appends_after_existing_same_day_addendum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            for sequence, label, headline in (
                (1, "第一期", "主刊事件"),
                (2, "第二期·补刊", "补刊事件"),
            ):
                (artifacts / f"2026-08-24-{sequence}.json").write_text(
                    json.dumps(
                        {
                            "label": label,
                            "sections": [
                                {"title": "🔥 AI 重要事件", "items": [{"headline": headline}]}
                            ],
                        }
                    ),
                    "utf-8",
                )
            (artifacts / "waytoagi-20260821.json").write_text(
                json.dumps(
                    {"sections": [{"items": [{"headline": "知识库新条目"}]}]}
                ),
                "utf-8",
            )
            (root / "reported.md").write_text(
                "## 2026-08-24（第一期）\n\n- 2026-08-24 | 主刊事件\n\n"
                "## 2026-08-24（第二期·补刊）\n\n- 2026-08-24 | 补刊事件\n",
                "utf-8",
            )
            waytoagi = root / "waytoagi.json"
            waytoagi.write_text(
                json.dumps(
                    {
                        "issues": [
                            {
                                "stamp": "20260821",
                                "date": "2026-08-21",
                                "items": [{"title": "知识库新条目"}],
                            }
                        ]
                    }
                ),
                "utf-8",
            )

            fallback.build_report(
                self.arguments(root, waytoagi=waytoagi, archive_only=True)
            )

            self.assertTrue(
                (root / "reported.md")
                .read_text("utf-8")
                .endswith("- 2026-08-24 | WayToAGI 2026-08-21：知识库新条目\n")
            )

    def test_issue_number_round_trip(self) -> None:
        for number in (1, 9, 10, 11, 20, 23, 99, 101, 219):
            with self.subTest(number=number):
                self.assertEqual(
                    fallback.chinese_to_int(fallback.int_to_chinese(number)), number
                )


if __name__ == "__main__":
    unittest.main()
