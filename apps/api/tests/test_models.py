from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from app.models import DecisionPayload, ProposalPayload, SourceSignals, SpecDiff
from app.policy import POLICY_VERSION


class ModelTests(unittest.TestCase):
    def test_proposal_payload_validates(self) -> None:
        payload = ProposalPayload(
            proposal_id="p1",
            dashboard_id="kalshi_autonomous_v1",
            proposal_type="layout_priority",
            title="Promote heartbeat",
            details="Move heartbeat tile to the top",
            policy_version=POLICY_VERSION,
            idempotency_key="abc123",
            created_at=datetime.now(timezone.utc),
            source_signals=SourceSignals(metrics={"quality_failures_last_60m": 1}),
            spec_diff=SpecDiff(),
        )
        self.assertEqual(payload.status, "proposed")
        self.assertEqual(payload.risk_level, "medium")

    def test_invalid_decision_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            DecisionPayload(
                proposal_id="p1",
                dashboard_id="kalshi_autonomous_v1",
                decision="invalid",
                decided_by="human_operator",
                decision_reason="Nope",
                decided_at=datetime.now(timezone.utc),
                policy_version=POLICY_VERSION,
            )


if __name__ == "__main__":
    unittest.main()
