from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

from app import cli


class CliTests(unittest.TestCase):
    @mock.patch("app.cli.Path.exists", return_value=True)
    @mock.patch("app.cli.get_settings")
    def test_env_check_reports_credentials(self, settings_mock, _exists_mock) -> None:
        settings_mock.return_value = mock.Mock(
            app_env="test",
            gcp_project_id="brainrot-453319",
            bq_dash_dataset="kalshi_dash",
            bq_signal_dataset="kalshi_signal",
            bq_ops_dataset="kalshi_ops",
            bq_core_dataset="kalshi_core",
            google_application_credentials="/tmp/creds.json",
            default_dashboard_id="kalshi_autonomous_v1",
        )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = cli.main(["env-check"])
        payload = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["credentials_file_exists"])

    @mock.patch("app.cli.run_autonomy_cycle")
    @mock.patch("app.cli._repo")
    def test_governance_run_uses_cli_mode(self, repo_mock, run_mock) -> None:
        repo_mock.return_value = mock.Mock()
        run_mock.return_value = mock.Mock(
            failed=0,
            model_dump=lambda mode="json": {"dashboard_id": "kalshi_autonomous_v1", "failed": 0},
        )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = cli.main(["governance-run", "--dashboard-id", "kalshi_autonomous_v1"])
        self.assertEqual(exit_code, 0)
        run_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
