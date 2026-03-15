from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.default_spec import default_dashboard_spec
from app.live_validation import run_live_validation


class FakeLiveValidationRepo:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            app_env="test",
            gcp_project_id="brainrot-453319",
            bq_dash_dataset="kalshi_dash",
            bq_signal_dataset="kalshi_signal",
            bq_ops_dataset="kalshi_ops",
            bq_core_dataset="kalshi_core",
            max_tile_rows=500,
        )
        self.saved_runs = []

    def dataset_exists(self, dataset: str) -> bool:
        return True

    def get_dashboard_spec(self, dashboard_id: str):
        return default_dashboard_spec(dashboard_id), "default"

    def view_exists(self, dataset: str, view_name: str) -> bool:
        return True

    def missing_columns(self, dataset: str, view_name: str, columns: list[str]) -> list[str]:
        return []

    def fetch_tile_rows(self, dashboard_id: str, tile_id: str, limit: int = 1):
        return [{"tile_id": tile_id}]

    def fetch_signal_feed(self, limit: int = 1):
        return [{"signal_id": "s1"}]

    def record_live_validation_run(self, run) -> bool:
        self.saved_runs.append(run)
        return True


class LiveValidationTests(unittest.TestCase):
    def test_live_validation_passes_and_records(self) -> None:
        repo = FakeLiveValidationRepo()
        result = run_live_validation(repo, "kalshi_autonomous_v1", environment="test", write_run=True)
        self.assertEqual(result.status, "passed")
        self.assertEqual(len(repo.saved_runs), 1)
        self.assertIn("datasets", result.checks)


if __name__ == "__main__":
    unittest.main()
