from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from app.models import (
    AgentProposal,
    DashboardSpec,
    TileSpec,
    UsageEventRequest,
)


class ModelTests(unittest.TestCase):
    def test_tile_spec_defaults(self) -> None:
        tile = TileSpec(
            tile_id="test_tile",
            title="Test Tile",
            view_name="vw_test",
        )
        self.assertEqual(tile.viz_type, "table")
        self.assertEqual(tile.default_limit, 100)
        self.assertEqual(tile.order_dir, "desc")
        self.assertIn("de", tile.roles)
        self.assertIn("consumer", tile.roles)

    def test_dashboard_spec_validates(self) -> None:
        spec = DashboardSpec(
            dashboard_id="test_dash",
            title="Test Dashboard",
            tiles=[
                TileSpec(
                    tile_id="t1",
                    title="Tile 1",
                    view_name="vw_t1",
                )
            ],
        )
        self.assertEqual(spec.version, 1)
        self.assertEqual(spec.refresh_seconds, 60)
        self.assertEqual(len(spec.tiles), 1)

    def test_agent_proposal_validates(self) -> None:
        proposal = AgentProposal(
            proposal_id="p1",
            dashboard_id="kalshi_autonomous_v1",
            proposal_type="layout_priority",
            title="Promote heartbeat",
            details="Move heartbeat tile to the top",
            priority="high",
            generated_at=datetime.now(timezone.utc),
        )
        self.assertEqual(proposal.priority, "high")

    def test_invalid_viz_type_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TileSpec(
                tile_id="bad",
                title="Bad Tile",
                view_name="vw_bad",
                viz_type="pie_chart",  # type: ignore[arg-type]
            )

    def test_usage_event_validates(self) -> None:
        event = UsageEventRequest(
            dashboard_id="kalshi_autonomous_v1",
            action="view_tile",
            panel_id="t1",
        )
        self.assertIsNone(event.user_id)
        self.assertEqual(event.filters, {})


if __name__ == "__main__":
    unittest.main()
