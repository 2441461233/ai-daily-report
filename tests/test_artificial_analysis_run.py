from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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


validator = load_script("validate_artificial_analysis_run")
sync_state = load_script("sync_artificial_analysis_state")


REPORT_DATE = "2026-08-17"
GENERATED_AT = datetime(2026, 8, 17, 1, 15, tzinfo=timezone.utc)


def snapshot(order: list[int] | None = None, *, first_score: int = 90) -> dict:
    order = list(range(10)) if order is None else order
    models = []
    for rank, model_number in enumerate(order, start=1):
        slug = f"model-{model_number}"
        score = first_score - model_number
        models.append(
            {
                "rank": rank,
                "slug": slug,
                "name": f"Model {model_number}",
                "creator": "Test Lab",
                "score": score,
                "estimated": False,
                "url": f"https://artificialanalysis.ai/models/{slug}",
            }
        )
    return {
        "schemaVersion": 1,
        "sourceUrl": validator.SOURCE_URL,
        "metric": validator.METRIC,
        "methodologyVersion": "4.1.1",
        "limit": 10,
        "models": models,
    }


def collector_document(previous: dict | None, current: dict) -> dict:
    changes = validator.expected_changes(previous, current)
    artifact = validator.expected_artifact(
        REPORT_DATE, GENERATED_AT, current, changes
    )
    return {
        "schemaVersion": 1,
        "reportDate": REPORT_DATE,
        "generatedAt": GENERATED_AT.isoformat().replace("+00:00", "Z"),
        "status": "baseline" if previous is None else "changed" if changes else "unchanged",
        "source": {
            "id": "artificial-analysis-models",
            "name": "Artificial Analysis LLM Leaderboard",
            "url": validator.SOURCE_URL,
            "methodologyUrl": validator.METHODOLOGY_URL,
            "metric": validator.METRIC,
            "method": "official_public_ssr_table",
        },
        "previousSnapshot": previous,
        "currentSnapshot": current,
        "previous": previous,
        "current": current,
        "changes": changes,
        "artifact": artifact,
    }


class ArtificialAnalysisRunValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "content" / "artifacts").mkdir(parents=True)
        self.input_path = self.root / "input.json"
        self.manifest_path = self.root / "changed.txt"
        self.snapshot_path = self.root / "content" / "artificial-analysis-snapshot.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_run(self, document: dict, snapshot_document: dict | None) -> None:
        self.input_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", "utf-8"
        )
        artifact = document["artifact"]
        manifest = ""
        if artifact is not None:
            path = self.root / artifact["path"]
            path.write_text(
                json.dumps(artifact["document"], ensure_ascii=False, indent=2) + "\n",
                "utf-8",
            )
            manifest = f"{artifact['path']}\n"
        self.manifest_path.write_text(manifest, "utf-8")
        if snapshot_document is not None:
            self.snapshot_path.write_text(
                json.dumps(snapshot_document, ensure_ascii=False, indent=2) + "\n",
                "utf-8",
            )

    def validate(self, snapshot_role: str) -> list[str]:
        return validator.validate_run(
            input_path=self.input_path,
            manifest_path=self.manifest_path,
            snapshot_path=self.snapshot_path,
            snapshot_role=snapshot_role,
            report_date=REPORT_DATE,
            now=GENERATED_AT,
            root=self.root,
        )

    def test_changed_run_validates_before_and_after_snapshot_sync(self) -> None:
        previous = snapshot()
        current = snapshot([1, 0, 2, 3, 4, 5, 6, 7, 8, 9])
        current["models"][0]["score"] = 91
        current["models"][1]["score"] = 90
        document = collector_document(previous, current)
        self.write_run(document, previous)

        self.assertEqual(self.validate("previous"), [])
        self.assertTrue(sync_state.sync(self.input_path, self.snapshot_path))
        self.assertEqual(self.validate("current"), [])
        self.assertFalse(sync_state.sync(self.input_path, self.snapshot_path))

    def test_baseline_accepts_missing_previous_snapshot(self) -> None:
        document = collector_document(None, snapshot())
        self.write_run(document, None)
        self.assertEqual(self.validate("previous"), [])

    def test_methodology_change_uses_the_official_methodology_attachment(self) -> None:
        previous = snapshot()
        current = snapshot()
        current["methodologyVersion"] = "4.2.0"
        document = collector_document(previous, current)
        self.write_run(document, previous)

        self.assertEqual(document["changes"][0]["type"], "methodology_changed")
        source_url = document["artifact"]["document"]["sections"][0]["items"][0][
            "sources"
        ][0]["url"]
        self.assertEqual(source_url, validator.METHODOLOGY_URL)
        self.assertEqual(self.validate("previous"), [])

    def test_first_recorded_methodology_version_is_a_change(self) -> None:
        previous = snapshot()
        previous["methodologyVersion"] = None
        current = snapshot()
        document = collector_document(previous, current)
        self.write_run(document, previous)

        self.assertEqual(document["changes"][0]["type"], "methodology_changed")
        self.assertIn(
            "未记录 → v4.1.1",
            document["artifact"]["document"]["sections"][0]["items"][0][
                "headline"
            ],
        )
        self.assertEqual(self.validate("previous"), [])

    def test_metadata_change_advances_snapshot_with_an_attachment(self) -> None:
        previous = snapshot()
        current = snapshot()
        current["models"][0]["estimated"] = True
        document = collector_document(previous, current)
        self.write_run(document, previous)

        self.assertEqual(document["changes"][0]["type"], "metadata_changed")
        self.assertIn(
            "分数标记“正式分”→“估算分”",
            document["artifact"]["document"]["sections"][0]["items"][0]["summary"],
        )
        self.assertEqual(self.validate("previous"), [])

    def test_tampered_diff_and_attachment_fail_closed(self) -> None:
        previous = snapshot()
        current = snapshot([1, 0, 2, 3, 4, 5, 6, 7, 8, 9])
        current["models"][0]["score"] = 91
        current["models"][1]["score"] = 90
        document = collector_document(previous, current)
        self.write_run(document, previous)
        document["changes"] = []
        self.input_path.write_text(json.dumps(document), "utf-8")
        artifact_path = self.root / document["artifact"]["path"]
        artifact_path.write_text("{}\n", "utf-8")

        messages = self.validate("previous")
        self.assertTrue(any("deterministic snapshot diff" in item for item in messages))
        self.assertTrue(any("trusted collector attachment" in item for item in messages))


if __name__ == "__main__":
    unittest.main()
