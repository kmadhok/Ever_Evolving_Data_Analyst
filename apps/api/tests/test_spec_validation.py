from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.default_spec import default_dashboard_spec
from app.spec_validation import validate_dashboard_spec


class FakeValidationRepo:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(max_tile_rows=500, bq_dash_dataset="kalshi_dash")
        self.available_views = {
            ("kalshi_dash", "vw_pipeline_heartbeat"): {"minutes_since_latest_market_snapshot"},
            ("kalshi_dash", "vw_quality_checks_24h"): {"checked_at"},
            ("kalshi_dash", "vw_ingestion_throughput_24h"): {"ts_minute"},
            ("kalshi_dash", "vw_endpoint_gap_24h"): {"ts_minute"},
            ("kalshi_dash", "vw_consumer_matchup_view"): {"event"},
            ("kalshi_dash", "vw_kpi_5m_24h"): {"kpi_ts"},
            ("kalshi_dash", "vw_event_activity_24h"): {"event_title"},
            ("kalshi_dash", "vw_simple_side_summary_24h"): {"event_title"},
            ("kalshi_dash", "vw_simple_side_flow_24h"): {"event_title"},
            ("kalshi_dash", "vw_simple_side_price_trend_24h"): {"hour_ts"},
            ("kalshi_dash", "vw_most_traded_markets_60m"): {"event_title"},
            ("kalshi_dash", "vw_trade_flow_6h"): {"ts_minute"},
            ("kalshi_dash", "vw_price_movers_60m"): {"event_title"},
            ("kalshi_dash", "vw_ds_feature_drift_24h"): {"market_ticker"},
            ("kalshi_dash", "vw_ds_label_coverage_24h"): {"total_trades_24h"},
            ("kalshi_dash", "vw_ds_retrain_signal_24h"): {"market_ticker"},
            ("kalshi_signal", "vw_signal_probability_shifts_24h"): {"title"},
            ("kalshi_signal", "vw_signal_volume_spikes_6h"): {"title"},
            ("kalshi_signal", "vw_signal_event_reactions_3h"): {"title"},
            ("kalshi_signal", "vw_signal_liquidity_deterioration_latest"): {"title"},
            ("kalshi_signal", "vw_signal_cross_market_inconsistencies"): {"title"},
        }

    def view_exists(self, dataset: str, view_name: str) -> bool:
        return (dataset, view_name) in self.available_views

    def missing_columns(self, dataset: str, view_name: str, columns: list[str]) -> list[str]:
        existing = self.available_views.get((dataset, view_name), set())
        return [column for column in columns if column not in existing]


class SpecValidationTests(unittest.TestCase):
    def test_default_spec_has_validation_errors_with_partial_schema_repo(self) -> None:
        repo = FakeValidationRepo()
        spec = default_dashboard_spec()
        result = validate_dashboard_spec(spec.dashboard_id, spec, repo)
        self.assertFalse(result.valid)
        self.assertTrue(any(issue.code == "columns_not_found" for issue in result.issues))

    def test_duplicate_tile_id_and_missing_role_detected(self) -> None:
        repo = FakeValidationRepo()
        spec = default_dashboard_spec()
        spec.refresh_seconds = 20
        spec.tiles = spec.tiles[:1]
        spec.tiles[0].tile_id = "duplicate"
        spec.tiles.append(spec.tiles[0].model_copy())
        result = validate_dashboard_spec(spec.dashboard_id, spec, repo)
        codes = {issue.code for issue in result.issues}
        self.assertIn("duplicate_tile_id", codes)
        self.assertIn("missing_role_coverage", codes)
        self.assertIn("refresh_seconds_out_of_bounds", codes)

    def test_unapproved_dataset_rejected(self) -> None:
        repo = FakeValidationRepo()
        spec = default_dashboard_spec()
        spec.tiles[0].dataset = "kalshi_core"
        result = validate_dashboard_spec(spec.dashboard_id, spec, repo)
        self.assertTrue(any(issue.code == "dataset_not_approved" for issue in result.issues))


if __name__ == "__main__":
    unittest.main()
