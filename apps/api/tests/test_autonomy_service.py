from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.default_spec import default_dashboard_spec
from app.models import AgentProposal, DashboardSpecVersionRecord, DecisionPayload, GovernedProposalRecord, ProposalPayload, SourceSignals, SpecDiff
from app.policy import POLICY_VERSION
from app.autonomy_service import build_governed_payload, run_autonomy_cycle


class FakeAutonomyRepo:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(max_tile_rows=500, bq_dash_dataset="kalshi_dash")
        self.spec = default_dashboard_spec()
        self.records = []
        self.autonomy_runs = []
        self.active = DashboardSpecVersionRecord(
            dashboard_id="kalshi_autonomous_v1",
            version_id="v-current",
            status="active",
            created_at=datetime.now(timezone.utc),
            created_by="seed",
            notes="",
            spec=self.spec,
        )

    def get_dashboard_spec(self, dashboard_id: str):
        return self.spec, "default"

    def generate_agent_proposals(self, dashboard_id: str):
        return [
            AgentProposal(
                proposal_id="p1",
                dashboard_id=dashboard_id,
                proposal_type="layout_priority",
                title="Promote heartbeat",
                details="Move pipeline_heartbeat to first visible section.",
                priority="low",
                generated_at=datetime.now(timezone.utc),
            )
        ]

    def _proposal_idempotency_key(self, proposal: AgentProposal) -> str:
        return proposal.proposal_id

    def insert_governed_proposal(self, payload: ProposalPayload) -> bool:
        self.records.append(
            GovernedProposalRecord(
                proposal_id=payload.proposal_id,
                dashboard_id=payload.dashboard_id,
                proposal_type=payload.proposal_type,
                status=payload.status,
                rationale=payload.details,
                created_at=payload.created_at,
                risk_level=payload.risk_level,
                policy_version=payload.policy_version,
                idempotency_key=payload.idempotency_key,
                payload=payload,
                decision=None,
            )
        )
        return True

    def list_governed_proposals(self, dashboard_id: str, limit: int = 20):
        return self.records[:limit]

    def record_proposal_decision(self, payload: DecisionPayload) -> bool:
        record = self.records[0]
        self.records[0] = record.model_copy(update={"status": "decided", "decision": payload})
        return True

    def transition_proposal_status(self, proposal_id: str, status: str) -> bool:
        record = self.records[0]
        self.records[0] = record.model_copy(update={"status": status, "payload": record.payload.model_copy(update={"status": status})})
        return True

    def get_active_spec_record(self, dashboard_id: str):
        return self.active

    def activate_dashboard_spec(self, request, source_proposal_id=None, previous_version_id=None):
        return True, "v-new", "active"

    def view_exists(self, dataset: str, view_name: str) -> bool:
        return True

    def missing_columns(self, dataset: str, view_name: str, columns: list[str]) -> list[str]:
        return []

    def list_spec_versions(self, dashboard_id: str, limit: int = 20):
        return [self.active]

    def record_autonomy_run(self, run) -> bool:
        self.autonomy_runs.append(run)
        return True

    def latest_autonomy_run(self, dashboard_id: str):
        return self.autonomy_runs[-1] if self.autonomy_runs else None

    def latest_live_validation_run(self, dashboard_id: str):
        return None


class AutonomyServiceTests(unittest.TestCase):
    def test_build_governed_payload_has_diff(self) -> None:
        repo = FakeAutonomyRepo()
        proposal = repo.generate_agent_proposals("kalshi_autonomous_v1")[0]
        payload = build_governed_payload(repo, proposal)
        self.assertEqual(payload.policy_version, POLICY_VERSION)
        self.assertTrue(payload.spec_diff.move_tiles or payload.spec_diff.notes == [])

    def test_run_autonomy_cycle_generates_decides_and_applies(self) -> None:
        repo = FakeAutonomyRepo()
        result = run_autonomy_cycle(repo, "kalshi_autonomous_v1")
        self.assertEqual(result.generated, 1)
        self.assertEqual(result.decided, 1)
        self.assertEqual(result.applied, 1)
        self.assertEqual(result.policy_version, POLICY_VERSION)
        self.assertEqual(len(repo.autonomy_runs), 1)


if __name__ == "__main__":
    unittest.main()
