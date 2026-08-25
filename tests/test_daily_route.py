from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import select_daily_route as route  # noqa: E402


class DailyRouteTests(unittest.TestCase):
    def test_early_quality_schedule_keeps_model_route(self) -> None:
        selected = route.select_route(
            now=datetime.fromisoformat("2026-08-25T08:40:00+08:00"),
            event_name="schedule",
            requested_route="quality",
            event_schedule="17 23 * * *",
        )
        self.assertEqual(selected, "quality")

    def test_late_quality_schedule_becomes_deadline_route(self) -> None:
        selected = route.select_route(
            now=datetime.fromisoformat("2026-08-25T11:13:07+08:00"),
            event_name="schedule",
            requested_route="quality",
            event_schedule="17 23 * * *",
        )
        self.assertEqual(selected, "deadline")

    def test_deadline_schedule_is_deterministic_even_before_cutoff(self) -> None:
        selected = route.select_route(
            now=datetime.fromisoformat("2026-08-25T08:47:00+08:00"),
            event_name="schedule",
            requested_route="quality",
            event_schedule="47 0 * * *",
        )
        self.assertEqual(selected, "deadline")

    def test_explicit_deadline_dispatch_wins_at_any_time(self) -> None:
        selected = route.select_route(
            now=datetime.fromisoformat("2026-08-25T07:00:00+08:00"),
            event_name="workflow_dispatch",
            requested_route="deadline",
            event_schedule="",
        )
        self.assertEqual(selected, "deadline")

    def test_explicit_quality_dispatch_can_run_after_cutoff(self) -> None:
        selected = route.select_route(
            now=datetime.fromisoformat("2026-08-25T12:00:00+08:00"),
            event_name="workflow_dispatch",
            requested_route="quality",
            event_schedule="",
        )
        self.assertEqual(selected, "quality")

    def test_cli_requires_timezone_aware_now(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "select_daily_route.py"),
                "--now",
                "2026-08-25T09:00:00",
                "--event-name",
                "schedule",
                "--requested-route",
                "quality",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timezone", result.stderr)


if __name__ == "__main__":
    unittest.main()
