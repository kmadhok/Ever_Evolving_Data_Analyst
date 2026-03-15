from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException

from app.default_spec import default_dashboard_spec
from app.main import governance_decision, governance_proposals, validate_spec
from app.models import (
    DecisionPayload,
    GovernanceDecisionRequest,
    GovernedProposalRecord,
    ProposalPayload,
    SourceSignals,
    SpecDiff,
    DashboardSpecValidationRequest,
)
from app.policy import POLICY_VERSION


class FakeGovernanceRepo:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(max_tile_rows=500, bq_dash_dataset="kalshi_dash")
        self._proposal = self._make_record()

    def _make_record(self, status: str = "proposed", decision: DecisionPayload | None = None) -> GovernedProposalRecord:
        payload = ProposalPayload(
            proposal_id="p1",
            dashboard_id="kalshi_autonomous_v1",
            proposal_type="layout_priority",
            title="Promote heartbeat",
            details="Move heartbeat tile to the top",
            priority="low",
            status=status,
            risk_level="low",
            policy_version=POLICY_VERSION,
            source_signals=SourceSignals(metrics={"quality_failures_last_60m": 1}),
            spec_diff=SpecDiff(),
            idempotency_key="idem-1",
            created_at=datetime.now(timezone.utc),
        )
        return GovernedProposalRecord(
            proposal_id=payload.proposal_id,
            dashboard_id=payload.dashboard_id,
            proposal_type=payload.proposal_type,
            status=status,
            rationale=payload.details,
            created_at=payload.created_at,
            risk_level=payload.risk_level,
            policy_version=payload.policy_version,
            idempotency_key=payload.idempotency_key,
            payload=payload,
            decision=decision,
        )

    def list_governed_proposals(self, dashboard_id: str, limit: int = 20) -> list[GovernedProposalRecord]:
        return [self._proposal]

    def get_governed_proposal(self, proposal_id: str) -> GovernedProposalRecord | None:
        if proposal_id != "p1":
            return None
        return self._proposal

    def record_proposal_decision(self, payload: DecisionPayload) -> bool:
        self._proposal = self._make_record(status="decided", decision=payload)
        return True

    def view_exists(self, dataset: str, view_name: str) -> bool:
        return True

    def missing_columns(self, dataset: str, view_name: str, columns: list[str]) -> list[str]:
        return []


class MainGovernanceTests(unittest.TestCase):
    def test_governance_proposals_returns_records(self) -> None:
        repo = FakeGovernanceRepo()
        response = governance_proposals("kalshi_autonomous_v1", 20, repo)
        self.assertEqual(response.dashboard_id, "kalshi_autonomous_v1")
        self.assertEqual(len(response.proposals), 1)

    def test_governance_decision_records_transition(self) -> None:
        repo = FakeGovernanceRepo()
        response = governance_decision(
            "p1",
            GovernanceDecisionRequest(
                dashboard_id="kalshi_autonomous_v1",
                decision="approve_manual",
                decided_by="human_operator",
                decision_reason="Looks safe",
            ),
            repo,
        )
        self.assertTrue(response.accepted)
        self.assertEqual(response.status, "decided")
        self.assertEqual(response.policy_version, POLICY_VERSION)

    def test_governance_decision_returns_404_for_unknown_proposal(self) -> None:
        repo = FakeGovernanceRepo()
        with self.assertRaises(HTTPException) as ctx:
            governance_decision(
                "missing",
                GovernanceDecisionRequest(
                    dashboard_id="kalshi_autonomous_v1",
                    decision="reject",
                    decided_by="human_operator",
                    decision_reason="Unknown",
                ),
                repo,
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_validate_spec_endpoint_returns_valid_for_default_spec(self) -> None:
        repo = FakeGovernanceRepo()
        spec = default_dashboard_spec()
        response = validate_spec(DashboardSpecValidationRequest(dashboard_id=spec.dashboard_id, spec=spec), repo)
        self.assertTrue(response.valid)


if __name__ == "__main__":
    unittest.main()
