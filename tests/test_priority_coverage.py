from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_priority_coverage.py"
SPEC = importlib.util.spec_from_file_location("validate_priority_coverage_for_tests", SCRIPT)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)

REPORT_DATE = "2026-08-13"
NOW = datetime(2026, 8, 13, 2, 45, tzinfo=timezone.utc)
CANDIDATE_ID = "spacexai:grok-4-6"
OFFICIAL_URL = "https://x.ai/news/grok-4-6"
DOCS_URL = "https://docs.x.ai/developers/grok-4-6"


def candidate(*, required: bool = True) -> dict:
    return {
        "id": CANDIDATE_ID,
        "title": "Grok 4.6",
        "url": OFFICIAL_URL,
        "publishedAt": "2026-08-12T00:00:00.000Z",
        "precision": "day",
        "category": "major_model_release",
        "required": required,
        "officialSource": "SpaceXAI",
        "evidenceUrls": [OFFICIAL_URL, DOCS_URL],
        "matchTerms": ["Grok", "4.6"],
        "summary": "SpaceXAI's frontier model is now available.",
        "details": "Official release-note details.",
    }


def priority_document(
    *,
    candidates: list[dict] | None = None,
    status: str = "ok",
    critical: bool = True,
    coverage_sufficient: bool = True,
    candidate_count: int | None = None,
) -> dict:
    values = [candidate()] if candidates is None else candidates
    count = len(values) if candidate_count is None else candidate_count
    return {
        "schemaVersion": 1,
        "generatedAt": "2026-08-13T02:45:00.000Z",
        "windowHours": 72,
        "sources": [
            {
                "id": "spacexai",
                "name": "SpaceXAI official releases",
                "officialSource": "SpaceXAI",
                "critical": critical,
                "coverageSufficient": coverage_sufficient,
                "status": status,
                "fetchedAt": "2026-08-13T02:45:00.000Z",
                "discoveredCount": count,
                "candidateCount": count,
                "endpoints": [],
            }
        ],
        "candidates": values,
        "errors": [],
    }


def report_item(
    *,
    priority_ids: list[str] | None = None,
    url: str = OFFICIAL_URL,
    headline: str = "SpaceXAI 发布 Grok 4.6 旗舰模型",
    summary: str = "Grok 4.6 已上线 API，面向编程与 Agent 任务。",
) -> dict:
    item = {
        "headline": headline,
        "summary": summary,
        "expanded": False,
        "sources": [{"name": "SpaceXAI", "url": url}],
    }
    if priority_ids is not None:
        item["priorityIds"] = priority_ids
    return item


def artifact(
    day: str,
    items: list[dict],
    *,
    section_title: str = validator.IMPORTANT_SECTION,
) -> dict:
    return {
        "date": day,
        "label": "测试期",
        "generatedAt": f"{day}T10:50:00+08:00",
        "oneLiner": "📌 今日一句话：测试。",
        "sections": [{"title": section_title, "items": items}],
    }


class PriorityCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "content" / "artifacts").mkdir(parents=True)
        self.input_path = self.root / "priority-news.json"
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        (self.root / "README.md").write_text("test\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-qm", "baseline")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=self.root, check=True)

    def write_input(self, document: dict) -> None:
        self.input_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    def write_new_artifact(
        self,
        document: dict,
        *,
        day: str = REPORT_DATE,
        sequence: int = 1,
    ) -> Path:
        path = self.root / "content" / "artifacts" / f"{day}-{sequence}.json"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return path

    def commit_artifact(
        self,
        document: dict,
        *,
        day: str,
        sequence: int = 1,
    ) -> Path:
        path = self.write_new_artifact(document, day=day, sequence=sequence)
        self.git("add", path.relative_to(self.root).as_posix())
        self.git("commit", "-qm", f"report {day}-{sequence}")
        return path

    def run_validator(
        self, *, input_only: bool = False, now: datetime = NOW
    ) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = validator.run_validation(
                input_path=self.input_path,
                report_date=REPORT_DATE,
                now=now,
                input_only=input_only,
                root=self.root,
            )
        return result, output.getvalue()

    def test_input_only_accepts_critical_partial_when_fallback_found_candidate(self) -> None:
        document = priority_document(status="partial")
        document["errors"] = [
            {
                "source": "spacexai",
                "endpoint": "news",
                "stage": "fetch",
                "message": "HTTP 403 Forbidden",
            }
        ]
        self.write_input(document)

        result, output = self.run_validator(input_only=True)

        self.assertEqual(result, 0, output)
        self.assertIn("1 required", output)

    def test_input_only_uses_explicit_critical_coverage_sufficiency(self) -> None:
        self.write_input(
            priority_document(
                candidates=[],
                status="partial",
                candidate_count=0,
                coverage_sufficient=True,
            )
        )
        result, output = self.run_validator(input_only=True)
        self.assertEqual(result, 0, output)

        for status in ("error", "partial"):
            with self.subTest(status=status):
                self.write_input(
                    priority_document(
                        candidates=[],
                        status=status,
                        candidate_count=0,
                        coverage_sufficient=False,
                    )
                )
                result, output = self.run_validator(input_only=True)
                self.assertEqual(result, 1)
                self.assertIn("coverage is insufficient", output)

    def test_input_requires_boolean_coverage_sufficiency(self) -> None:
        document = priority_document()
        document["sources"][0]["coverageSufficient"] = "yes"
        self.write_input(document)

        result, output = self.run_validator(input_only=True)

        self.assertEqual(result, 1)
        self.assertIn("coverageSufficient", output)

    def test_input_rejects_sufficient_source_with_unresolved_signals(self) -> None:
        document = priority_document()
        document["sources"][0]["unresolvedSignals"] = ["spacexai:grok-9-9"]
        self.write_input(document)

        result, output = self.run_validator(input_only=True)

        self.assertEqual(result, 1)
        self.assertIn("cannot be sufficient", output)

    def test_input_schema_freshness_date_and_window_are_strict(self) -> None:
        cases: list[tuple[str, object, str]] = [
            ("schemaVersion", 2, "schemaVersion"),
            ("windowHours", 0, "windowHours"),
            ("errors", {}, "must be an array"),
        ]
        for field, value, expected in cases:
            with self.subTest(field=field):
                document = priority_document()
                document[field] = value
                self.write_input(document)
                result, output = self.run_validator(input_only=True)
                self.assertEqual(result, 1)
                self.assertIn(expected, output)

        stale = priority_document()
        stale["generatedAt"] = "2026-08-13T00:00:00.000Z"
        stale["sources"][0]["fetchedAt"] = stale["generatedAt"]
        self.write_input(stale)
        result, output = self.run_validator(input_only=True)
        self.assertEqual(result, 1)
        self.assertIn("more than two hours old", output)

        wrong_day = priority_document()
        wrong_day["generatedAt"] = "2026-08-12T15:59:00.000Z"
        wrong_day["sources"][0]["fetchedAt"] = wrong_day["generatedAt"]
        self.write_input(wrong_day)
        result, output = self.run_validator(
            input_only=True,
            now=datetime(2026, 8, 12, 15, 59, tzinfo=timezone.utc),
        )
        self.assertEqual(result, 1)
        self.assertIn("Shanghai date", output)

    def test_input_rejects_future_and_out_of_window_candidate(self) -> None:
        future = priority_document()
        future["candidates"][0]["publishedAt"] = "2026-08-14T00:00:00.000Z"
        self.write_input(future)
        result, output = self.run_validator(input_only=True)
        self.assertEqual(result, 1)
        self.assertIn("future publication", output)

        old = priority_document()
        old["candidates"][0]["publishedAt"] = "2026-08-09T00:00:00.000Z"
        self.write_input(old)
        result, output = self.run_validator(input_only=True)
        self.assertEqual(result, 1)
        self.assertIn("collection window", output)

    def test_day_precision_uses_calendar_interval_for_window_boundary(self) -> None:
        document = priority_document()
        document["windowHours"] = 24
        # Midnight is 26h45 before generatedAt, but Aug 12 as a calendar day
        # overlaps the trailing 24-hour window and therefore remains valid.
        self.write_input(document)

        result, output = self.run_validator(input_only=True)

        self.assertEqual(result, 0, output)

    def test_input_rejects_bad_candidate_shape_and_missing_file(self) -> None:
        document = priority_document()
        document["candidates"][0]["required"] = "true"
        document["candidates"][0]["evidenceUrls"] = []
        document["candidates"][0]["matchTerms"] = ["Grok", "grok"]
        self.write_input(document)
        result, output = self.run_validator(input_only=True)
        self.assertEqual(result, 1)
        self.assertIn("required", output)
        self.assertIn("evidenceUrls", output)
        self.assertIn("duplicates", output)

        self.input_path.unlink()
        result, output = self.run_validator(input_only=True)
        self.assertEqual(result, 1)
        self.assertIn("does not exist", output)

    def test_new_main_covers_required_candidate_with_all_three_signals(self) -> None:
        self.write_input(priority_document())
        item = report_item(
            priority_ids=[CANDIDATE_ID],
            # Tracking-only differences and a fragment are normalized away.
            url=f"{OFFICIAL_URL}/?utm_source=daily#details",
            headline="ＳｐａｃｅＸＡＩ 发布 Ｇｒｏｋ ４．６",
        )
        self.write_new_artifact(artifact(REPORT_DATE, [item]))

        result, output = self.run_validator()

        self.assertEqual(result, 0, output)
        self.assertIn("1 covered today", output)

    def test_each_coverage_signal_is_mandatory(self) -> None:
        cases = {
            "priority id": report_item(priority_ids=None),
            "evidence": report_item(priority_ids=[CANDIDATE_ID], url="https://x.ai/"),
            "term": report_item(
                priority_ids=[CANDIDATE_ID],
                headline="SpaceXAI 发布 Grok 14.60",
                summary="Grok 14.60 已上线。",
            ),
        }
        for label, item in cases.items():
            with self.subTest(signal=label):
                # Give every case a fresh repository because artifacts are immutable inputs.
                path = self.root / "content" / "artifacts" / f"{REPORT_DATE}-1.json"
                if path.exists():
                    path.unlink()
                self.write_input(priority_document())
                self.write_new_artifact(artifact(REPORT_DATE, [item]))
                result, output = self.run_validator()
                self.assertEqual(result, 1)
                self.assertIn("not covered", output)

    def test_claim_outside_major_events_does_not_count(self) -> None:
        self.write_input(priority_document())
        self.write_new_artifact(
            artifact(
                REPORT_DATE,
                [report_item(priority_ids=[CANDIDATE_ID])],
                section_title="🌍 海外观察",
            )
        )

        result, output = self.run_validator()

        self.assertEqual(result, 1)
        self.assertIn("outside", output)
        self.assertIn("not covered", output)

    def test_new_artifact_rejects_unknown_and_duplicate_priority_claims(self) -> None:
        self.write_input(priority_document())
        items = [
            report_item(priority_ids=[CANDIDATE_ID]),
            report_item(priority_ids=[CANDIDATE_ID, "spacexai:typo"]),
        ]
        self.write_new_artifact(artifact(REPORT_DATE, items))

        result, output = self.run_validator()

        self.assertEqual(result, 1)
        self.assertIn("unknown priority id", output)
        self.assertIn("more than once", output)

    def test_head_today_explicit_coverage_is_accepted_without_new_addendum(self) -> None:
        self.write_input(priority_document())
        self.commit_artifact(
            artifact(REPORT_DATE, [report_item(priority_ids=[CANDIDATE_ID])]),
            day=REPORT_DATE,
        )

        result, output = self.run_validator()

        self.assertEqual(result, 0, output)
        self.assertIn("1 covered today", output)

    def test_head_legacy_strong_match_is_narrow_already_covered_exemption(self) -> None:
        self.write_input(priority_document())
        self.commit_artifact(
            artifact("2026-08-12", [report_item(priority_ids=None)]),
            day="2026-08-12",
        )

        result, output = self.run_validator()

        self.assertEqual(result, 0, output)
        self.assertIn("1 already covered in HEAD", output)

    def test_head_legacy_requires_both_exact_evidence_and_all_terms(self) -> None:
        for label, item in (
            ("wrong evidence", report_item(priority_ids=None, url="https://x.ai/")),
            (
                "wrong version",
                report_item(
                    priority_ids=None,
                    headline="SpaceXAI 发布 Grok 4.5",
                    summary="Grok 4.5 已上线。",
                ),
            ),
        ):
            with self.subTest(label=label):
                # Commit once per subtest is awkward in one Git history, so use
                # distinct earlier dates; neither should match the candidate.
                day = "2026-08-11" if label == "wrong evidence" else "2026-08-12"
                self.commit_artifact(artifact(day, [item]), day=day)

        self.write_input(priority_document())
        result, output = self.run_validator()
        self.assertEqual(result, 1)
        self.assertIn("not covered", output)

    def test_new_duplicate_of_head_legacy_match_fails(self) -> None:
        self.commit_artifact(
            artifact("2026-08-12", [report_item(priority_ids=None)]),
            day="2026-08-12",
        )
        self.write_input(priority_document())
        self.write_new_artifact(
            artifact(REPORT_DATE, [report_item(priority_ids=[CANDIDATE_ID])])
        )

        result, output = self.run_validator()

        self.assertEqual(result, 1)
        self.assertIn("already strongly covered in HEAD", output)

    def test_worktree_edit_to_head_artifact_cannot_fake_coverage(self) -> None:
        path = self.commit_artifact(
            artifact(
                REPORT_DATE,
                [
                    report_item(
                        priority_ids=None,
                        url="https://x.ai/",
                        headline="其他新闻",
                        summary="与旗舰模型无关。",
                    )
                ],
            ),
            day=REPORT_DATE,
        )
        # The mutable working copy now looks valid, but the validator must read HEAD.
        path.write_text(
            json.dumps(
                artifact(REPORT_DATE, [report_item(priority_ids=[CANDIDATE_ID])]),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.write_input(priority_document())

        result, output = self.run_validator()

        self.assertEqual(result, 1)
        self.assertIn("not covered", output)

    def test_nested_head_artifact_cannot_claim_historical_coverage(self) -> None:
        nested = self.root / "content" / "artifacts" / "nested"
        nested.mkdir()
        path = nested / "2026-08-12-1.json"
        path.write_text(
            json.dumps(
                artifact("2026-08-12", [report_item(priority_ids=[CANDIDATE_ID])]),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.git("add", path.relative_to(self.root).as_posix())
        self.git("commit", "-qm", "nested fake coverage")
        self.write_input(priority_document())

        result, output = self.run_validator()

        self.assertEqual(result, 1)
        self.assertIn("not covered", output)

    def test_optional_candidate_may_be_absent(self) -> None:
        self.write_input(priority_document(candidates=[candidate(required=False)]))

        result, output = self.run_validator()

        self.assertEqual(result, 0, output)
        self.assertIn("1 optional", output)

    def test_addendum_cannot_claim_an_optional_candidate(self) -> None:
        self.write_input(priority_document(candidates=[candidate(required=False)]))
        document = artifact(
            REPORT_DATE,
            [report_item(priority_ids=[CANDIDATE_ID])],
        )
        document["kind"] = "addendum"
        self.write_new_artifact(document, sequence=2)

        result, output = self.run_validator()

        self.assertEqual(result, 1)
        self.assertIn("addendum may only claim", output)

    def test_cli_supports_required_flags_and_input_only(self) -> None:
        self.write_input(priority_document())
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = validator.main(
                [
                    "--input",
                    str(self.input_path),
                    "--date",
                    REPORT_DATE,
                    "--now",
                    "2026-08-13T02:45:00Z",
                    "--input-only",
                ]
            )
        self.assertEqual(result, 0, output.getvalue())


class NormalizationUnitTests(unittest.TestCase):
    def test_url_normalization_is_exact_beyond_tracking_noise(self) -> None:
        self.assertEqual(
            validator.normalize_url("HTTPS://X.AI:443/news/grok-4-6/?utm_source=x#top"),
            validator.normalize_url(OFFICIAL_URL),
        )
        self.assertNotEqual(
            validator.normalize_url("https://x.ai/news/grok-4-6-v2"),
            validator.normalize_url(OFFICIAL_URL),
        )

    def test_ascii_term_matching_is_nfkc_casefolded_and_token_aware(self) -> None:
        self.assertTrue(validator.term_matches("Grok", "ＧＲＯＫ-4.6"))
        self.assertTrue(validator.term_matches("4.6", "Grok-4.6 is available"))
        self.assertTrue(validator.term_matches("Grok", "Grok4.6 is available"))
        self.assertTrue(validator.term_matches("4.6", "Grok4.6 is available"))
        self.assertFalse(validator.term_matches("Grok", "GrokBot is available"))
        self.assertFalse(validator.term_matches("4.6", "Grok 14.60 is available"))


if __name__ == "__main__":
    unittest.main()
