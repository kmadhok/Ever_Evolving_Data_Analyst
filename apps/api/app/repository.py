from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from google.api_core.exceptions import GoogleAPIError, NotFound
from google.cloud import bigquery

from .config import Settings
from .default_spec import default_dashboard_spec
from .models import (
    DashboardSpec,
    DashboardSpecWriteRequest,
    TileSpec,
    UsageEventRequest,
)

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

    def _tile_dataset(self, tile: TileSpec) -> str:
        if tile.dataset:
            return self._safe_ident(tile.dataset)
        return self.settings.bq_dash_dataset

    def _run(self, sql: str, params: Optional[list[Any]] = None):
        job_config = bigquery.QueryJobConfig(query_parameters=params or [])
        return self.client.query(sql, job_config=job_config).result()

    # ── Validation helpers ──────────────────────────────────────────────

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

    def missing_columns(self, dataset: str, view_name: str, columns: list[str]) -> list[str]:
        requested = [column for column in columns if column != "*"]
        if not requested:
            return []
        try:
            table = self.client.get_table(self._table(dataset, view_name))
        except (GoogleAPIError, NotFound):
            return requested

        existing = {field.name for field in getattr(table, "schema", [])}
        return [column for column in requested if column not in existing]

    # ── Dashboard spec ──────────────────────────────────────────────────

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

    # ── Tile data ───────────────────────────────────────────────────────

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

    # ── Signal feed ─────────────────────────────────────────────────────

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

    # ── Usage tracking ──────────────────────────────────────────────────

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
