from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.default_spec import default_dashboard_spec
from app.models import DashboardSpecVersionRecord, ProposalPayload, SourceSignals, SpecDiff
from app.policy import POLICY_VERSION
from app.spec_activation import activate_candidate_spec
from app.spec_rollback import rollback_dashboard_spec


class FakeLifecycleRepo:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(max_tile_rows=500, bq_dash_dataset="kalshi_dash")
        self.active = DashboardSpecVersionRecord(
            dashboard_id="kalshi_autonomous_v1",
            version_id="v-current",
            status="active",
            created_at=datetime.now(timezone.utc),
            created_by="seed",
            notes="",
            spec=default_dashboard_spec(),
        )
        self.previous = DashboardSpecVersionRecord(
            dashboard_id="kalshi_autonomous_v1",
            version_id="v-previous",
            status="inactive",
            created_at=datetime.now(timezone.utc),
            created_by="seed",
            notes="",
            spec=default_dashboard_spec(),
        )
        self.transitions = []

    def view_exists(self, dataset: str, view_name: str) -> bool:
        return True

    def missing_columns(self, dataset: str, view_name: str, columns: list[str]) -> list[str]:
        return []

    def get_active_spec_record(self, dashboard_id: str):
        return self.active

    def activate_dashboard_spec(self, request, source_proposal_id=None, previous_version_id=None):
        self.active = DashboardSpecVersionRecord(
            dashboard_id=request.dashboard_id,
            version_id="v-new",
            status="active",
            created_at=datetime.now(timezone.utc),
            created_by=request.created_by,
            notes=request.notes,
            source_proposal_id=source_proposal_id,
            previous_version_id=previous_version_id,
            spec=request.spec,
        )
        return True, "v-new", "active"

    def transition_proposal_status(self, proposal_id: str, status: str) -> bool:
        self.transitions.append((proposal_id, status))
        return True

    def get_previous_active_spec_record(self, dashboard_id: str):
        return self.previous

    def restore_spec_version(self, dashboard_id: str, restore_version_id: str, *, restored_by: str, reason: str, source_proposal_id=None):
        return True, "v-restored"


class ActivationAndRollbackTests(unittest.TestCase):
    def _payload(self) -> ProposalPayload:
        return ProposalPayload(
            proposal_id="p1",
            dashboard_id="kalshi_autonomous_v1",
            proposal_type="layout_priority",
            title="Promote heartbeat",
            details="Move heartbeat tile to the top",
            priority="low",
            status="decided",
            risk_level="low",
            policy_version=POLICY_VERSION,
            source_signals=SourceSignals(metrics={"quality_failures_last_60m": 1}),
            spec_diff=SpecDiff(),
            idempotency_key="idem-1",
            created_at=datetime.now(timezone.utc),
        )

    def test_activate_candidate_spec_succeeds(self) -> None:
        repo = FakeLifecycleRepo()
        payload = self._payload()
        candidate = default_dashboard_spec()
        result = activate_candidate_spec(repo, payload, candidate, activated_by="autonomy_policy")
        self.assertTrue(result.accepted)
        self.assertEqual(result.status, "applied")
        self.assertIn(("p1", "applied"), repo.transitions)

    def test_rollback_marks_proposal_rolled_back(self) -> None:
        repo = FakeLifecycleRepo()
        result = rollback_dashboard_spec(
            repo,
            "kalshi_autonomous_v1",
            rollback_reason="Manual",
            rolled_back_by="human_operator",
            proposal_id="p1",
        )
        self.assertTrue(result.accepted)
        self.assertIn(("p1", "rolled_back"), repo.transitions)


if __name__ == "__main__":
    unittest.main()
