from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.models import DecisionPayload, GovernedProposalRecord, ProposalPayload, SourceSignals, SpecDiff
from app.policy import POLICY_VERSION
from app.repository import BigQueryRepository


class RepositoryGovernanceTests(unittest.TestCase):
    def _repo(self) -> BigQueryRepository:
        return object.__new__(BigQueryRepository)

    def _proposal_record(self, status: str = "proposed", with_decision: bool = False) -> GovernedProposalRecord:
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
        decision = None
        if with_decision:
            decision = DecisionPayload(
                proposal_id="p1",
                dashboard_id="kalshi_autonomous_v1",
                decision="approve_manual",
                decided_by="human_operator",
                decision_reason="Reviewed",
                decided_at=datetime.now(timezone.utc),
                policy_version=POLICY_VERSION,
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

    def test_transition_rules_allow_expected_states(self) -> None:
        self.assertTrue(BigQueryRepository._transition_allowed("proposed", "decided"))
        self.assertTrue(BigQueryRepository._transition_allowed("decided", "applied"))
        self.assertFalse(BigQueryRepository._transition_allowed("applied", "proposed"))

    def test_transition_proposal_status_rejects_invalid_change(self) -> None:
        repo = self._repo()
        repo.get_governed_proposal = lambda proposal_id: self._proposal_record(status="applied")
        with self.assertRaises(ValueError):
            repo.transition_proposal_status("p1", "proposed")

    def test_record_proposal_decision_rejects_duplicate_decision(self) -> None:
        repo = self._repo()
        repo.get_governed_proposal = lambda proposal_id: self._proposal_record(status="proposed", with_decision=True)
        decision = DecisionPayload(
            proposal_id="p1",
            dashboard_id="kalshi_autonomous_v1",
            decision="approve_manual",
            decided_by="human_operator",
            decision_reason="Duplicate",
            decided_at=datetime.now(timezone.utc),
            policy_version=POLICY_VERSION,
        )
        with self.assertRaises(ValueError):
            repo.record_proposal_decision(decision)


if __name__ == "__main__":
    unittest.main()
