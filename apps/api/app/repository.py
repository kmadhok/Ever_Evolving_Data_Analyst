from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional

from google.api_core.exceptions import GoogleAPIError, NotFound
from google.cloud import bigquery

from .config import Settings
from .default_spec import default_dashboard_spec
from .models import (
    AgentProposal,
    AutonomyRunRecord,
    DashboardSpecVersionRecord,
    DashboardSpecValidationResponse,
    DecisionPayload,
    GovernedProposalRecord,
    LiveValidationRunRecord,
    ProposalPayload,
    ProposalStatus,
    ValidationIssue,
    DashboardSpec,
    DashboardSpecWriteRequest,
    SourceSignals,
    SpecDiff,
    TileSpec,
    UsageEventRequest,
)
from .policy import POLICY_VERSION, classify_risk_for_proposal_type, risk_level_for_priority

SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ALLOWED_STATUS_TRANSITIONS: dict[ProposalStatus, set[ProposalStatus]] = {
    "proposed": {"decided", "failed"},
    "decided": {"applied", "rejected", "failed"},
    "applied": {"rolled_back", "failed"},
    "rejected": set(),
    "failed": set(),
    "rolled_back": set(),
}


class BigQueryRepository:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = bigquery.Client(project=settings.gcp_project_id)

    def _table(self, dataset: str, table: str) -> str:
        return f"{self.settings.gcp_project_id}.{dataset}.{table}"

    @staticmethod
    def _safe_ident(value: str) -> str:
        if not SAFE_IDENT.match(value):
            raise ValueError(f"Invalid identifier: {value}")
        return value

    def _serialize(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: self._serialize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._serialize(v) for v in value]
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value

    def _tile_dataset(self, tile: TileSpec) -> str:
        if tile.dataset:
            return self._safe_ident(tile.dataset)
        return self.settings.bq_dash_dataset

    def _run(self, sql: str, params: Optional[list[Any]] = None):
        job_config = bigquery.QueryJobConfig(query_parameters=params or [])
        return self.client.query(sql, job_config=job_config).result()

    def view_exists(self, dataset: str, view_name: str) -> bool:
        try:
            self.client.get_table(self._table(dataset, view_name))
            return True
        except (GoogleAPIError, NotFound):
            return False

    def dataset_exists(self, dataset: str) -> bool:
        try:
            self.client.get_dataset(f"{self.settings.gcp_project_id}.{dataset}")
            return True
        except (GoogleAPIError, NotFound):
            return False

    def missing_columns(self, dataset: str, view_name: str, columns: Iterable[str]) -> list[str]:
        requested = [column for column in columns if column != "*"]
        if not requested:
            return []
        try:
            table = self.client.get_table(self._table(dataset, view_name))
        except (GoogleAPIError, NotFound):
            return requested

        existing = {field.name for field in getattr(table, "schema", [])}
        return [column for column in requested if column not in existing]

    @staticmethod
    def _proposal_payload_json(payload: ProposalPayload) -> dict[str, Any]:
        return payload.model_dump(mode="json", by_alias=True)

    @staticmethod
    def _decision_payload_json(payload: DecisionPayload) -> dict[str, Any]:
        return payload.model_dump(mode="json", by_alias=True)

    @staticmethod
    def _parse_json_cell(value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise ValueError("Expected JSON object payload.")
            return parsed
        if isinstance(value, dict):
            return value
        raise ValueError("Unsupported JSON payload type.")

    @staticmethod
    def _parse_json_value(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                return default
        return value

    @staticmethod
    def _transition_allowed(current_status: ProposalStatus, next_status: ProposalStatus) -> bool:
        return next_status in ALLOWED_STATUS_TRANSITIONS[current_status]

    @staticmethod
    def _proposal_idempotency_key(proposal: AgentProposal) -> str:
        raw = "|".join(
            [
                proposal.dashboard_id,
                proposal.proposal_type,
                proposal.title,
                proposal.details,
                proposal.priority,
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _proposal_payload_from_row(row: dict[str, Any]) -> ProposalPayload:
        raw_payload = BigQueryRepository._parse_json_cell(row["proposal_json"])
        try:
            payload = ProposalPayload.model_validate(raw_payload)
        except Exception:
            payload = ProposalPayload(
                proposal_id=row["proposal_id"],
                dashboard_id=row["dashboard_id"],
                proposal_type=row["proposal_type"],
                title=raw_payload.get("title", row["proposal_type"]),
                details=raw_payload.get("details", row.get("rationale") or ""),
                priority=raw_payload.get("priority", "medium"),
                status=row.get("status", "proposed"),
                risk_level=row.get("risk_level") or raw_payload.get("risk_level", "medium"),
                policy_version=row.get("policy_version") or raw_payload.get("policy_version", POLICY_VERSION),
                source_signals=SourceSignals.model_validate(raw_payload.get("source_signals", {"metrics": {}})),
                spec_diff=SpecDiff.model_validate(raw_payload.get("spec_diff", {})),
                idempotency_key=row.get("idempotency_key")
                or raw_payload.get("idempotency_key")
                or hashlib.sha256(row["proposal_id"].encode("utf-8")).hexdigest(),
                created_at=raw_payload.get("created_at", row["created_at"]),
                candidate_version_id=raw_payload.get("candidate_version_id"),
            )
        return payload.model_copy(update={"status": row.get("status", payload.status)})

    @staticmethod
    def _decision_payload_from_row(row: dict[str, Any]) -> DecisionPayload:
        return DecisionPayload.model_validate(
            {
                "proposal_id": row["proposal_id"],
                "dashboard_id": row["dashboard_id"],
                "decision": row["decision"],
                "decided_by": row.get("decided_by") or "system",
                "decision_reason": row.get("decision_reason") or "",
                "decided_at": row["decided_at"],
                "policy_version": row.get("policy_version") or POLICY_VERSION,
                "candidate_version_id": row.get("candidate_version_id"),
            }
        )

    def _decision_for_proposal(self, proposal_id: str) -> Optional[DecisionPayload]:
        sql = f"""
        SELECT
          proposal_id,
          dashboard_id,
          decision,
          decided_by,
          decision_reason,
          decided_at,
          policy_version,
          candidate_version_id
        FROM `{self._table(self.settings.bq_ops_dataset, 'agent_decisions')}`
        WHERE proposal_id = @proposal_id
        ORDER BY decided_at DESC
        LIMIT 1
        """
        try:
            rows = list(
                self._run(sql, [bigquery.ScalarQueryParameter("proposal_id", "STRING", proposal_id)])
            )
        except GoogleAPIError:
            fallback_sql = f"""
            SELECT
              proposal_id,
              dashboard_id,
              decision,
              decided_by,
              decision_reason,
              decided_at
            FROM `{self._table(self.settings.bq_ops_dataset, 'agent_decisions')}`
            WHERE proposal_id = @proposal_id
            ORDER BY decided_at DESC
            LIMIT 1
            """
            rows = list(
                self._run(fallback_sql, [bigquery.ScalarQueryParameter("proposal_id", "STRING", proposal_id)])
            )
        if not rows:
            return None

        row = dict(rows[0].items())
        return self._decision_payload_from_row(self._serialize(row))

    def get_dashboard_spec(self, dashboard_id: str) -> tuple[DashboardSpec, str]:
        sql = f"""
        SELECT spec_json
        FROM `{self._table(self.settings.bq_ops_dataset, 'dashboard_spec_versions')}`
        WHERE dashboard_id = @dashboard_id
          AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 1
        """
        try:
            rows = list(
                self._run(
                    sql,
                    [bigquery.ScalarQueryParameter("dashboard_id", "STRING", dashboard_id)],
                )
            )
            if not rows:
                return default_dashboard_spec(dashboard_id), "default"

            raw_spec = rows[0]["spec_json"]
            if isinstance(raw_spec, str):
                spec_payload = json.loads(raw_spec)
            else:
                spec_payload = raw_spec
            return DashboardSpec.model_validate(spec_payload), "bq"
        except (GoogleAPIError, NotFound, ValueError, json.JSONDecodeError):
            return default_dashboard_spec(dashboard_id), "default"

    @staticmethod
    def _parse_version_notes(notes: str | None) -> dict[str, Any]:
        if not notes:
            return {}
        try:
            parsed = json.loads(notes)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {"raw_notes": notes}

    def _version_row_to_record(self, row: dict[str, Any]) -> DashboardSpecVersionRecord:
        metadata = self._parse_version_notes(row.get("notes"))
        spec = None
        raw_spec = row.get("spec_json")
        if raw_spec is not None:
            if isinstance(raw_spec, str):
                raw_spec = json.loads(raw_spec)
            spec = DashboardSpec.model_validate(raw_spec)
        return DashboardSpecVersionRecord(
            dashboard_id=row["dashboard_id"],
            version_id=row["version_id"],
            status=row["status"],
            created_at=row["created_at"],
            created_by=row.get("created_by") or "",
            notes=row.get("notes") or "",
            source_proposal_id=row.get("source_proposal_id") or metadata.get("source_proposal_id"),
            previous_version_id=row.get("previous_version_id") or metadata.get("previous_version_id"),
            spec=spec,
        )

    def save_dashboard_spec(self, request: DashboardSpecWriteRequest) -> tuple[bool, str, str]:
        version_id = f"v{request.spec.version}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        status = "active" if request.deactivate_existing else "candidate"

        try:
            if request.deactivate_existing:
                deactivate_sql = f"""
                UPDATE `{self._table(self.settings.bq_ops_dataset, 'dashboard_spec_versions')}`
                SET status = 'inactive'
                WHERE dashboard_id = @dashboard_id
                  AND status = 'active'
                """
                self._run(
                    deactivate_sql,
                    [bigquery.ScalarQueryParameter("dashboard_id", "STRING", request.dashboard_id)],
                )

            table_id = self._table(self.settings.bq_ops_dataset, "dashboard_spec_versions")
            row = {
                "dashboard_id": request.dashboard_id,
                "version_id": version_id,
                "spec_json": json.dumps(request.spec.model_dump(mode="json"), separators=(",", ":")),
                "status": status,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_by": request.created_by,
                "notes": request.notes,
            }
            errors = self.client.insert_rows_json(table_id, [row])
            return (not errors), version_id, status
        except GoogleAPIError:
            return False, version_id, status

    def list_spec_versions(self, dashboard_id: str, limit: int = 20) -> list[DashboardSpecVersionRecord]:
        final_limit = min(max(limit, 1), 100)
        sql = f"""
        SELECT
          dashboard_id,
          version_id,
          spec_json,
          status,
          created_at,
          created_by,
          notes,
          source_proposal_id,
          previous_version_id
        FROM `{self._table(self.settings.bq_ops_dataset, 'dashboard_spec_versions')}`
        WHERE dashboard_id = @dashboard_id
        ORDER BY created_at DESC
        LIMIT @limit
        """
        try:
            rows = list(
                self._run(
                    sql,
                    [
                        bigquery.ScalarQueryParameter("dashboard_id", "STRING", dashboard_id),
                        bigquery.ScalarQueryParameter("limit", "INT64", final_limit),
                    ],
                )
            )
        except GoogleAPIError:
            fallback_sql = f"""
            SELECT
              dashboard_id,
              version_id,
              spec_json,
              status,
              created_at,
              created_by,
              notes
            FROM `{self._table(self.settings.bq_ops_dataset, 'dashboard_spec_versions')}`
            WHERE dashboard_id = @dashboard_id
            ORDER BY created_at DESC
            LIMIT @limit
            """
            try:
                rows = list(
                    self._run(
                        fallback_sql,
                        [
                            bigquery.ScalarQueryParameter("dashboard_id", "STRING", dashboard_id),
                            bigquery.ScalarQueryParameter("limit", "INT64", final_limit),
                        ],
                    )
                )
            except GoogleAPIError:
                return []
        return [self._version_row_to_record(self._serialize(dict(row.items()))) for row in rows]

    def get_active_spec_record(self, dashboard_id: str) -> Optional[DashboardSpecVersionRecord]:
        versions = self.list_spec_versions(dashboard_id=dashboard_id, limit=20)
        return next((version for version in versions if version.status == "active"), None)

    def get_previous_active_spec_record(self, dashboard_id: str) -> Optional[DashboardSpecVersionRecord]:
        versions = self.list_spec_versions(dashboard_id=dashboard_id, limit=20)
        active_seen = False
        for version in versions:
            if version.status == "active" and not active_seen:
                active_seen = True
                continue
            if version.status == "inactive":
                return version
        return None

    def activate_dashboard_spec(
        self,
        request: DashboardSpecWriteRequest,
        *,
        source_proposal_id: Optional[str] = None,
        previous_version_id: Optional[str] = None,
    ) -> tuple[bool, str, str]:
        version_id = f"v{request.spec.version}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        status = "active"
        metadata = self._parse_version_notes(request.notes)
        if source_proposal_id:
            metadata["source_proposal_id"] = source_proposal_id
        if previous_version_id:
            metadata["previous_version_id"] = previous_version_id
        notes = json.dumps(metadata, separators=(",", ":")) if metadata else request.notes

        try:
            deactivate_sql = f"""
            UPDATE `{self._table(self.settings.bq_ops_dataset, 'dashboard_spec_versions')}`
            SET status = 'inactive'
            WHERE dashboard_id = @dashboard_id
              AND status = 'active'
            """
            self._run(
                deactivate_sql,
                [bigquery.ScalarQueryParameter("dashboard_id", "STRING", request.dashboard_id)],
            )

            table_id = self._table(self.settings.bq_ops_dataset, "dashboard_spec_versions")
            row = {
                "dashboard_id": request.dashboard_id,
                "version_id": version_id,
                "spec_json": json.dumps(request.spec.model_dump(mode="json"), separators=(",", ":")),
                "status": status,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_by": request.created_by,
                "notes": notes,
                "source_proposal_id": source_proposal_id,
                "previous_version_id": previous_version_id,
            }
            errors = self.client.insert_rows_json(table_id, [row])
            if errors:
                fallback_row = {
                    "dashboard_id": request.dashboard_id,
                    "version_id": version_id,
                    "spec_json": json.dumps(request.spec.model_dump(mode="json"), separators=(",", ":")),
                    "status": status,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "created_by": request.created_by,
                    "notes": notes,
                }
                errors = self.client.insert_rows_json(table_id, [fallback_row])
            return (not errors), version_id, status
        except GoogleAPIError:
            return False, version_id, status

    def restore_spec_version(
        self,
        dashboard_id: str,
        restore_version_id: str,
        *,
        restored_by: str,
        reason: str,
        source_proposal_id: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        versions = self.list_spec_versions(dashboard_id, limit=50)
        target = next((version for version in versions if version.version_id == restore_version_id), None)
        current = next((version for version in versions if version.status == "active"), None)
        if target is None or target.spec is None:
            return False, None

        ok, version_id, _ = self.activate_dashboard_spec(
            DashboardSpecWriteRequest(
                dashboard_id=dashboard_id,
                spec=target.spec,
                created_by=restored_by,
                notes=json.dumps(
                    {
                        "rollback_reason": reason,
                        "restored_from_version_id": restore_version_id,
                        "source_proposal_id": source_proposal_id,
                    },
                    separators=(",", ":"),
                ),
                deactivate_existing=True,
            ),
            source_proposal_id=source_proposal_id,
            previous_version_id=current.version_id if current else None,
        )
        return ok, version_id if ok else None

    def insert_governed_proposal(self, payload: ProposalPayload) -> bool:
        existing = self.find_governed_proposal_by_idempotency_key(payload.dashboard_id, payload.idempotency_key)
        if existing is not None:
            return True
        table_id = self._table(self.settings.bq_ops_dataset, "agent_proposals")
        row = {
            "proposal_id": payload.proposal_id,
            "dashboard_id": payload.dashboard_id,
            "proposal_type": payload.proposal_type,
            "proposal_json": self._proposal_payload_json(payload),
            "rationale": payload.details,
            "status": payload.status,
            "risk_level": payload.risk_level,
            "policy_version": payload.policy_version,
            "idempotency_key": payload.idempotency_key,
            "created_at": payload.created_at.isoformat(),
        }
        try:
            errors = self.client.insert_rows_json(table_id, [row])
            if errors:
                fallback_row = {
                    "proposal_id": payload.proposal_id,
                    "dashboard_id": payload.dashboard_id,
                    "proposal_type": payload.proposal_type,
                    "proposal_json": self._proposal_payload_json(payload),
                    "rationale": payload.details,
                    "status": payload.status,
                    "created_at": payload.created_at.isoformat(),
                }
                errors = self.client.insert_rows_json(table_id, [fallback_row])
            return not errors
        except GoogleAPIError:
            return False

    def find_governed_proposal_by_idempotency_key(
        self,
        dashboard_id: str,
        idempotency_key: str,
    ) -> Optional[GovernedProposalRecord]:
        sql = f"""
        SELECT
          proposal_id,
          dashboard_id,
          proposal_type,
          proposal_json,
          rationale,
          status,
          risk_level,
          policy_version,
          idempotency_key,
          created_at
        FROM `{self._table(self.settings.bq_ops_dataset, 'agent_proposals')}`
        WHERE dashboard_id = @dashboard_id
          AND idempotency_key = @idempotency_key
        ORDER BY created_at DESC
        LIMIT 1
        """
        try:
            rows = list(
                self._run(
                    sql,
                    [
                        bigquery.ScalarQueryParameter("dashboard_id", "STRING", dashboard_id),
                        bigquery.ScalarQueryParameter("idempotency_key", "STRING", idempotency_key),
                    ],
                )
            )
        except GoogleAPIError:
            try:
                proposals = self.list_governed_proposals(dashboard_id, limit=50)
            except Exception:
                return None
            return next((proposal for proposal in proposals if proposal.idempotency_key == idempotency_key), None)
        if not rows:
            return None

        row = self._serialize(dict(rows[0].items()))
        payload = self._proposal_payload_from_row(row)
        decision = self._decision_for_proposal(row["proposal_id"])
        return GovernedProposalRecord(
            proposal_id=row["proposal_id"],
            dashboard_id=row["dashboard_id"],
            proposal_type=row["proposal_type"],
            status=row["status"],
            rationale=row.get("rationale") or "",
            created_at=row["created_at"],
            risk_level=row.get("risk_level") or payload.risk_level,
            policy_version=row.get("policy_version") or payload.policy_version,
            idempotency_key=row.get("idempotency_key") or payload.idempotency_key,
            payload=payload,
            decision=decision,
        )

    def get_governed_proposal(self, proposal_id: str) -> Optional[GovernedProposalRecord]:
        sql = f"""
        SELECT
          proposal_id,
          dashboard_id,
          proposal_type,
          proposal_json,
          rationale,
          status,
          risk_level,
          policy_version,
          idempotency_key,
          created_at
        FROM `{self._table(self.settings.bq_ops_dataset, 'agent_proposals')}`
        WHERE proposal_id = @proposal_id
        ORDER BY created_at DESC
        LIMIT 1
        """
        try:
            rows = list(self._run(sql, [bigquery.ScalarQueryParameter("proposal_id", "STRING", proposal_id)]))
        except GoogleAPIError:
            fallback_sql = f"""
            SELECT
              proposal_id,
              dashboard_id,
              proposal_type,
              proposal_json,
              rationale,
              status,
              created_at
            FROM `{self._table(self.settings.bq_ops_dataset, 'agent_proposals')}`
            WHERE proposal_id = @proposal_id
            ORDER BY created_at DESC
            LIMIT 1
            """
            try:
                rows = list(
                    self._run(
                        fallback_sql,
                        [bigquery.ScalarQueryParameter("proposal_id", "STRING", proposal_id)],
                    )
                )
            except GoogleAPIError:
                return None

        if not rows:
            return None

        row = self._serialize(dict(rows[0].items()))
        payload = self._proposal_payload_from_row(row)
        decision = self._decision_for_proposal(proposal_id)
        return GovernedProposalRecord(
            proposal_id=row["proposal_id"],
            dashboard_id=row["dashboard_id"],
            proposal_type=row["proposal_type"],
            status=row["status"],
            rationale=row.get("rationale") or "",
            created_at=row["created_at"],
            risk_level=row.get("risk_level") or payload.risk_level,
            policy_version=row.get("policy_version") or payload.policy_version,
            idempotency_key=row.get("idempotency_key") or payload.idempotency_key,
            payload=payload,
            decision=decision,
        )

    def list_governed_proposals(
        self,
        dashboard_id: str,
        limit: int = 20,
    ) -> list[GovernedProposalRecord]:
        final_limit = min(max(limit, 1), 100)
        sql = f"""
        SELECT
          proposal_id,
          dashboard_id,
          proposal_type,
          proposal_json,
          rationale,
          status,
          risk_level,
          policy_version,
          idempotency_key,
          created_at
        FROM `{self._table(self.settings.bq_ops_dataset, 'agent_proposals')}`
        WHERE dashboard_id = @dashboard_id
        ORDER BY created_at DESC
        LIMIT @limit
        """
        try:
            rows = list(
                self._run(
                    sql,
                    [
                        bigquery.ScalarQueryParameter("dashboard_id", "STRING", dashboard_id),
                        bigquery.ScalarQueryParameter("limit", "INT64", final_limit),
                    ],
                )
            )
        except GoogleAPIError:
            fallback_sql = f"""
            SELECT
              proposal_id,
              dashboard_id,
              proposal_type,
              proposal_json,
              rationale,
              status,
              created_at
            FROM `{self._table(self.settings.bq_ops_dataset, 'agent_proposals')}`
            WHERE dashboard_id = @dashboard_id
            ORDER BY created_at DESC
            LIMIT @limit
            """
            try:
                rows = list(
                    self._run(
                        fallback_sql,
                        [
                            bigquery.ScalarQueryParameter("dashboard_id", "STRING", dashboard_id),
                            bigquery.ScalarQueryParameter("limit", "INT64", final_limit),
                        ],
                    )
                )
            except GoogleAPIError:
                return []

        results: list[GovernedProposalRecord] = []
        for raw_row in rows:
            row = self._serialize(dict(raw_row.items()))
            payload = self._proposal_payload_from_row(row)
            decision = self._decision_for_proposal(row["proposal_id"])
            results.append(
                GovernedProposalRecord(
                    proposal_id=row["proposal_id"],
                    dashboard_id=row["dashboard_id"],
                    proposal_type=row["proposal_type"],
                    status=row["status"],
                    rationale=row.get("rationale") or "",
                    created_at=row["created_at"],
                    risk_level=row.get("risk_level") or payload.risk_level,
                    policy_version=row.get("policy_version") or payload.policy_version,
                    idempotency_key=row.get("idempotency_key") or payload.idempotency_key,
                    payload=payload,
                    decision=decision,
                )
            )
        return results

    def list_governed_proposals_by_status(
        self,
        dashboard_id: str,
        statuses: Iterable[ProposalStatus],
        limit: int = 20,
    ) -> list[GovernedProposalRecord]:
        requested = {status for status in statuses}
        proposals = self.list_governed_proposals(dashboard_id, limit=max(limit, 50))
        return [proposal for proposal in proposals if proposal.status in requested][:limit]

    def transition_proposal_status(self, proposal_id: str, next_status: ProposalStatus) -> bool:
        proposal = self.get_governed_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f"Unknown proposal_id: {proposal_id}")
        if not self._transition_allowed(proposal.status, next_status):
            raise ValueError(f"Invalid status transition: {proposal.status} -> {next_status}")

        sql = f"""
        UPDATE `{self._table(self.settings.bq_ops_dataset, 'agent_proposals')}`
        SET status = @next_status
        WHERE proposal_id = @proposal_id
        """
        try:
            self._run(
                sql,
                [
                    bigquery.ScalarQueryParameter("next_status", "STRING", next_status),
                    bigquery.ScalarQueryParameter("proposal_id", "STRING", proposal_id),
                ],
            )
            return True
        except GoogleAPIError:
            return False

    def record_proposal_decision(self, payload: DecisionPayload) -> bool:
        proposal = self.get_governed_proposal(payload.proposal_id)
        if proposal is None:
            raise ValueError(f"Unknown proposal_id: {payload.proposal_id}")
        if proposal.dashboard_id != payload.dashboard_id:
            raise ValueError("Decision dashboard_id does not match proposal dashboard_id.")
        if proposal.decision is not None:
            raise ValueError(f"Proposal {payload.proposal_id} already has a recorded decision.")

        table_id = self._table(self.settings.bq_ops_dataset, "agent_decisions")
        row = {
            "proposal_id": payload.proposal_id,
            "dashboard_id": payload.dashboard_id,
            "decision": payload.decision,
            "decided_by": payload.decided_by,
            "decision_reason": payload.decision_reason,
            "decided_at": payload.decided_at.isoformat(),
            "policy_version": payload.policy_version,
            "candidate_version_id": payload.candidate_version_id,
        }
        try:
            errors = self.client.insert_rows_json(table_id, [row])
            if errors:
                fallback_row = {
                    "proposal_id": payload.proposal_id,
                    "dashboard_id": payload.dashboard_id,
                    "decision": payload.decision,
                    "decided_by": payload.decided_by,
                    "decision_reason": payload.decision_reason,
                    "decided_at": payload.decided_at.isoformat(),
                }
                errors = self.client.insert_rows_json(table_id, [fallback_row])
            if errors:
                return False
            return self.transition_proposal_status(payload.proposal_id, "decided")
        except GoogleAPIError:
            return False

    def record_autonomy_run(self, run: AutonomyRunRecord) -> bool:
        table_id = self._table(self.settings.bq_ops_dataset, "autonomy_runs")
        row = {
            "run_id": run.run_id,
            "dashboard_id": run.dashboard_id,
            "mode": run.mode,
            "status": run.status,
            "policy_version": run.policy_version,
            "generated_count": run.generated,
            "decided_count": run.decided,
            "applied_count": run.applied,
            "rejected_count": run.rejected,
            "failed_count": run.failed,
            "generated_proposal_ids": run.generated_proposal_ids,
            "decided_proposal_ids": run.decided_proposal_ids,
            "applied_version_ids": run.applied_version_ids,
            "validation_issues_json": [issue.model_dump(mode="json") for issue in run.validation_issues],
            "errors_json": run.errors,
            "message": run.message,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat(),
        }
        try:
            errors = self.client.insert_rows_json(table_id, [row])
            return not errors
        except GoogleAPIError:
            return False

    def latest_autonomy_run(self, dashboard_id: str) -> Optional[AutonomyRunRecord]:
        sql = f"""
        SELECT
          run_id,
          dashboard_id,
          mode,
          status,
          policy_version,
          generated_count,
          decided_count,
          applied_count,
          rejected_count,
          failed_count,
          generated_proposal_ids,
          decided_proposal_ids,
          applied_version_ids,
          validation_issues_json,
          errors_json,
          message,
          started_at,
          completed_at
        FROM `{self._table(self.settings.bq_ops_dataset, 'autonomy_runs')}`
        WHERE dashboard_id = @dashboard_id
        ORDER BY completed_at DESC
        LIMIT 1
        """
        try:
            rows = list(self._run(sql, [bigquery.ScalarQueryParameter("dashboard_id", "STRING", dashboard_id)]))
        except GoogleAPIError:
            return None
        if not rows:
            return None
        row = self._serialize(dict(rows[0].items()))
        raw_generated = self._parse_json_value(row.get("generated_proposal_ids"), [])
        raw_decided = self._parse_json_value(row.get("decided_proposal_ids"), [])
        raw_applied = self._parse_json_value(row.get("applied_version_ids"), [])
        raw_issues = self._parse_json_value(row.get("validation_issues_json"), [])
        raw_errors = self._parse_json_value(row.get("errors_json"), [])
        return AutonomyRunRecord(
            run_id=row["run_id"],
            dashboard_id=row["dashboard_id"],
            mode=row.get("mode") or "scheduled",
            status=row.get("status") or "completed",
            policy_version=row.get("policy_version") or POLICY_VERSION,
            generated=int(row.get("generated_count") or 0),
            decided=int(row.get("decided_count") or 0),
            applied=int(row.get("applied_count") or 0),
            rejected=int(row.get("rejected_count") or 0),
            failed=int(row.get("failed_count") or 0),
            generated_proposal_ids=[str(item) for item in raw_generated] if isinstance(raw_generated, list) else [],
            decided_proposal_ids=[str(item) for item in raw_decided] if isinstance(raw_decided, list) else [],
            applied_version_ids=[str(item) for item in raw_applied] if isinstance(raw_applied, list) else [],
            validation_issues=[ValidationIssue.model_validate(issue) for issue in raw_issues if isinstance(issue, dict)],
            errors=[str(item) for item in raw_errors] if isinstance(raw_errors, list) else [],
            message=row.get("message") or "",
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    def record_live_validation_run(self, run: LiveValidationRunRecord) -> bool:
        table_id = self._table(self.settings.bq_ops_dataset, "live_validation_runs")
        row = {
            "validation_id": run.validation_id,
            "dashboard_id": run.dashboard_id,
            "environment": run.environment,
            "status": run.status,
            "issues_json": [issue.model_dump(mode="json") for issue in run.issues],
            "checks_json": run.checks,
            "message": run.message,
            "checked_at": run.checked_at.isoformat(),
        }
        try:
            errors = self.client.insert_rows_json(table_id, [row])
            return not errors
        except GoogleAPIError:
            return False

    def latest_live_validation_run(self, dashboard_id: str) -> Optional[LiveValidationRunRecord]:
        sql = f"""
        SELECT
          validation_id,
          dashboard_id,
          environment,
          status,
          issues_json,
          checks_json,
          message,
          checked_at
        FROM `{self._table(self.settings.bq_ops_dataset, 'live_validation_runs')}`
        WHERE dashboard_id = @dashboard_id
        ORDER BY checked_at DESC
        LIMIT 1
        """
        try:
            rows = list(self._run(sql, [bigquery.ScalarQueryParameter("dashboard_id", "STRING", dashboard_id)]))
        except GoogleAPIError:
            return None
        if not rows:
            return None
        row = self._serialize(dict(rows[0].items()))
        raw_issues = self._parse_json_value(row.get("issues_json"), [])
        raw_checks = self._parse_json_value(row.get("checks_json"), {})
        return LiveValidationRunRecord(
            validation_id=row["validation_id"],
            dashboard_id=row["dashboard_id"],
            environment=row.get("environment") or self.settings.app_env,
            status=row.get("status") or "unknown",
            checked_at=row["checked_at"],
            issues=[ValidationIssue.model_validate(issue) for issue in raw_issues if isinstance(issue, dict)],
            checks=raw_checks if isinstance(raw_checks, dict) else {},
            message=row.get("message") or "",
        )

    def fetch_tile_rows(
        self, dashboard_id: str, tile_id: str, limit: Optional[int] = None
    ) -> list[dict[str, Any]]:
        spec, _ = self.get_dashboard_spec(dashboard_id)
        tile = next((item for item in spec.tiles if item.tile_id == tile_id), None)
        if tile is None:
            raise ValueError(f"Unknown tile_id: {tile_id}")

        view_name = self._safe_ident(tile.view_name)
        columns = tile.columns or ["*"]
        safe_columns = [self._safe_ident(c) for c in columns] if columns != ["*"] else ["*"]

        final_limit = min(max(limit or tile.default_limit, 1), self.settings.max_tile_rows)

        select_columns = ", ".join(safe_columns)
        dataset = self._tile_dataset(tile)
        sql = f"SELECT {select_columns} FROM `{self._table(dataset, view_name)}`"

        if tile.order_by:
            order_by = self._safe_ident(tile.order_by)
            order_dir = "ASC" if tile.order_dir.lower() == "asc" else "DESC"
            sql += f" ORDER BY {order_by} {order_dir}"

        sql += " LIMIT @limit"
        rows = self._run(sql, [bigquery.ScalarQueryParameter("limit", "INT64", final_limit)])
        return [self._serialize(dict(row.items())) for row in rows]

    def fetch_signal_feed(
        self,
        signal_type: Optional[str] = None,
        severity: Optional[str] = None,
        family: Optional[str] = None,
        window: Optional[str] = None,
        sort_by: str = "score",
        sort_dir: str = "desc",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        allowed_sort_fields = {
            "score",
            "signal_ts",
            "signal_type",
            "title",
            "market_family",
            "signal_window",
            "severity",
        }
        final_sort_by = sort_by if sort_by in allowed_sort_fields else "score"
        final_sort_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"
        final_limit = min(max(limit, 1), self.settings.max_tile_rows)

        where_clauses = ["1 = 1"]
        params: list[Any] = [bigquery.ScalarQueryParameter("limit", "INT64", final_limit)]

        if signal_type:
            where_clauses.append("signal_type = @signal_type")
            params.append(bigquery.ScalarQueryParameter("signal_type", "STRING", signal_type))
        if severity:
            where_clauses.append("severity = @severity")
            params.append(bigquery.ScalarQueryParameter("severity", "STRING", severity))
        if family:
            where_clauses.append("market_family = @family")
            params.append(bigquery.ScalarQueryParameter("family", "STRING", family))
        if window:
            where_clauses.append("signal_window = @window")
            params.append(bigquery.ScalarQueryParameter("window", "STRING", window))

        sql = f"""
        SELECT
          signal_id,
          signal_type,
          signal_window,
          entity_type,
          entity_id,
          title,
          market_family,
          score,
          severity,
          signal_ts,
          explanation_short,
          metrics_json
        FROM `{self._table(self.settings.bq_signal_dataset, 'vw_signal_feed_latest')}`
        WHERE {' AND '.join(where_clauses)}
        ORDER BY {self._safe_ident(final_sort_by)} {final_sort_dir}, signal_ts DESC
        LIMIT @limit
        """
        rows = self._run(sql, params)
        return [self._serialize(dict(row.items())) for row in rows]

    def record_usage_event(self, event: UsageEventRequest) -> bool:
        table_id = self._table(self.settings.bq_core_dataset, "dashboard_events")
        row = {
            "event_ts": datetime.now(timezone.utc).isoformat(),
            "user_id": event.user_id,
            "dashboard_id": event.dashboard_id,
            "action": event.action,
            "panel_id": event.panel_id,
            "filter_json": json.dumps(event.filters, separators=(",", ":"), default=str),
            "session_id": event.session_id,
            "ingestion_date": datetime.now(timezone.utc).date().isoformat(),
        }
        try:
            errors = self.client.insert_rows_json(table_id, [row])
            return not errors
        except GoogleAPIError:
            return False

    def generate_agent_proposals(self, dashboard_id: str) -> list[AgentProposal]:
        proposals: list[AgentProposal] = []
        now = datetime.now(timezone.utc)

        heartbeat_sql = f"SELECT * FROM `{self._table(self.settings.bq_dash_dataset, 'vw_pipeline_heartbeat')}` LIMIT 1"
        try:
            heartbeat_rows = list(self._run(heartbeat_sql))
            if heartbeat_rows:
                h = dict(heartbeat_rows[0].items())
                if int(h.get("quality_failures_last_60m", 0) or 0) > 0:
                    proposals.append(
                        AgentProposal(
                            proposal_id=str(uuid.uuid4()),
                            dashboard_id=dashboard_id,
                            proposal_type="layout_priority",
                            title="Prioritize Health Tile",
                            details="Quality failures detected in the last hour. Move pipeline heartbeat to top row and increase refresh rate.",
                            priority="high",
                            generated_at=now,
                        )
                    )
                if int(h.get("minutes_since_latest_trade", 0) or 0) > 15:
                    proposals.append(
                        AgentProposal(
                            proposal_id=str(uuid.uuid4()),
                            dashboard_id=dashboard_id,
                            proposal_type="data_freshness",
                            title="Trade Freshness Degradation",
                            details="Latest trade appears stale. Add a dedicated stale-data banner and route alert to DE agent.",
                            priority="high",
                            generated_at=now,
                        )
                    )
        except GoogleAPIError:
            pass

        usage_sql = f"""
        SELECT panel_id, COUNT(*) AS event_count
        FROM `{self._table(self.settings.bq_core_dataset, 'dashboard_events')}`
        WHERE dashboard_id = @dashboard_id
          AND event_ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
          AND panel_id IS NOT NULL
        GROUP BY panel_id
        ORDER BY event_count DESC
        LIMIT 1
        """
        try:
            usage_rows = list(
                self._run(
                    usage_sql,
                    [bigquery.ScalarQueryParameter("dashboard_id", "STRING", dashboard_id)],
                )
            )
            if usage_rows:
                top_panel = usage_rows[0]["panel_id"]
                proposals.append(
                    AgentProposal(
                        proposal_id=str(uuid.uuid4()),
                        dashboard_id=dashboard_id,
                        proposal_type="ux_adaptation",
                        title="Promote Most-Used Tile",
                        details=f"Tile '{top_panel}' had highest usage in the last 24h. Consider promoting it to first visible section.",
                        priority="medium",
                        generated_at=now,
                    )
                )
        except GoogleAPIError:
            pass

        if not proposals:
            proposals.append(
                AgentProposal(
                    proposal_id=str(uuid.uuid4()),
                    dashboard_id=dashboard_id,
                    proposal_type="no_change",
                    title="No Adaptation Needed",
                    details="No significant quality or usage drift detected; keep current dashboard spec.",
                    priority="low",
                    generated_at=now,
                )
            )

        return proposals

    def persist_proposals(self, proposals: list[AgentProposal]) -> int:
        if not proposals:
            return 0

        persisted = 0
        for p in proposals:
            payload = ProposalPayload(
                proposal_id=p.proposal_id,
                dashboard_id=p.dashboard_id,
                proposal_type=p.proposal_type,
                title=p.title,
                details=p.details,
                priority=p.priority,
                status="proposed",
                risk_level=classify_risk_for_proposal_type(p.proposal_type, risk_level_for_priority(p.priority)),
                policy_version=POLICY_VERSION,
                source_signals=SourceSignals(),
                spec_diff=SpecDiff(),
                idempotency_key=self._proposal_idempotency_key(p),
                created_at=p.generated_at,
            )
            if self.insert_governed_proposal(payload):
                persisted += 1

        return persisted
