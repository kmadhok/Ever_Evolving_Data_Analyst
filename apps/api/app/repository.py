from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from google.api_core.exceptions import GoogleAPIError, NotFound
from google.cloud import bigquery

from .config import Settings
from .default_spec import default_dashboard_spec
from .models import AgentProposal, DashboardSpec, DashboardSpecWriteRequest, UsageEventRequest

SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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

    def _run(self, sql: str, params: Optional[list[Any]] = None):
        job_config = bigquery.QueryJobConfig(query_parameters=params or [])
        return self.client.query(sql, job_config=job_config).result()

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
        sql = f"SELECT {select_columns} FROM `{self._table(self.settings.bq_dash_dataset, view_name)}`"

        if tile.order_by:
            order_by = self._safe_ident(tile.order_by)
            order_dir = "ASC" if tile.order_dir.lower() == "asc" else "DESC"
            sql += f" ORDER BY {order_by} {order_dir}"

        sql += " LIMIT @limit"
        rows = self._run(sql, [bigquery.ScalarQueryParameter("limit", "INT64", final_limit)])
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

        table_id = self._table(self.settings.bq_ops_dataset, "agent_proposals")
        rows = []
        now = datetime.now(timezone.utc)
        for p in proposals:
            rows.append(
                {
                    "proposal_id": p.proposal_id,
                    "dashboard_id": p.dashboard_id,
                    "proposal_type": p.proposal_type,
                    "proposal_json": json.dumps(p.model_dump(mode="json"), separators=(",", ":"), default=str),
                    "rationale": p.details,
                    "status": "proposed",
                    "created_at": now.isoformat(),
                }
            )

        try:
            errors = self.client.insert_rows_json(table_id, rows)
            return 0 if errors else len(rows)
        except GoogleAPIError:
            return 0
