#!/usr/bin/env python3
"""Standalone Kalshi signal generation and reporting script.

Runs after kalshi_market_ingest.py (or independently). Generates signal
run summaries, market intelligence reports, and runs post-publish and
signal quality checks.

Usage:
    export GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json
    python scripts/kalshi_signals.py

    # Or with the project venv
    GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json \
        ./apps/api/.venv/bin/python scripts/kalshi_signals.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Load .env files if present (simple key=value parser)
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key not in os.environ:
            os.environ[key] = value


_load_dotenv(_ROOT / ".env.kalshi.local")
_load_dotenv(_ROOT / ".env.airflow.local")
_load_dotenv(_ROOT / ".env.kalshi.local.example")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BQ_PROJECT = os.getenv("BQ_PROJECT", "brainrot-453319")
BQ_DATASET_CORE = os.getenv("BQ_DATASET_CORE", "kalshi_core")
BQ_DATASET_SIGNAL = os.getenv("BQ_DATASET_SIGNAL", "kalshi_signal")
BQ_DATASET_OPS = os.getenv("BQ_DATASET_OPS", "kalshi_ops")

QUALITY_MAX_FRESHNESS_MINUTES = float(os.getenv("QUALITY_MAX_FRESHNESS_MINUTES", "15"))
QUALITY_MIN_EVENT_TITLE_COVERAGE = float(os.getenv("QUALITY_MIN_EVENT_TITLE_COVERAGE", "0.90"))
QUALITY_MAX_MARKET_LATEST_VIOLATIONS = int(os.getenv("QUALITY_MAX_MARKET_LATEST_VIOLATIONS", "0"))
QUALITY_MAX_EVENT_LATEST_VIOLATIONS = int(os.getenv("QUALITY_MAX_EVENT_LATEST_VIOLATIONS", "0"))
QUALITY_MAX_DUPLICATE_SIGNAL_ID_COUNT = int(os.getenv("QUALITY_MAX_DUPLICATE_SIGNAL_ID_COUNT", "0"))
QUALITY_MAX_NULL_SIGNAL_ENTITY_COUNT = int(os.getenv("QUALITY_MAX_NULL_SIGNAL_ENTITY_COUNT", "0"))

KALSHI_REPORT_TIMEZONE = os.getenv("KALSHI_REPORT_TIMEZONE", "America/New_York")
KALSHI_DAILY_REPORT_HOUR = int(os.getenv("KALSHI_DAILY_REPORT_HOUR", "8"))
KALSHI_DAILY_REPORT_MINUTE = int(os.getenv("KALSHI_DAILY_REPORT_MINUTE", "0"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("kalshi_signals")

# ---------------------------------------------------------------------------
# BigQuery helpers
# ---------------------------------------------------------------------------


def _get_bq_client():
    from google.cloud import bigquery

    return bigquery.Client(project=BQ_PROJECT)


def _table_ref(dataset: str, table: str) -> str:
    return f"{BQ_PROJECT}.{dataset}.{table}"


def _insert_rows(dataset: str, table: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    client = _get_bq_client()
    table_id = _table_ref(dataset, table)
    errors = client.insert_rows_json(table_id, rows)
    if errors:
        raise RuntimeError(f"BigQuery insert errors for {table_id}: {errors}")
    return len(rows)


def _run_query(sql: str, query_parameters: list[Any] | None = None):
    from google.cloud import bigquery

    client = _get_bq_client()
    config = bigquery.QueryJobConfig(query_parameters=query_parameters or [])
    job = client.query(sql, job_config=config)
    job.result()
    return job


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_cell(value: Any) -> str:
    if value is None:
        return "null"
    return json.dumps(value, separators=(",", ":"), default=str)


def _quality_row(
    run_id: str,
    check_name: str,
    status: str,
    metric_value: float | int,
    threshold_value: float | int,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "check_name": check_name,
        "status": status,
        "metric_value": metric_value,
        "threshold_value": threshold_value,
        "details": _json_cell(details or {}),
        "checked_at": _utcnow_iso(),
    }


def _current_local_time() -> datetime:
    return datetime.now(ZoneInfo(KALSHI_REPORT_TIMEZONE))


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------


def run_post_publish_quality_checks(run_id: str) -> dict[str, Any]:
    """Run post-publish quality checks on core tables."""
    log.info("Running post-publish quality checks ...")

    quality_sql = f"""
    WITH market_latest_counts AS (
      SELECT market_ticker, SUM(CASE WHEN is_latest THEN 1 ELSE 0 END) AS latest_true_count
      FROM `{_table_ref(BQ_DATASET_CORE, "market_state_core")}`
      GROUP BY market_ticker
    ),
    event_latest_counts AS (
      SELECT event_ticker, COUNT(*) AS row_count,
             SUM(CASE WHEN is_latest THEN 1 ELSE 0 END) AS latest_true_count
      FROM `{_table_ref(BQ_DATASET_CORE, "events_dim")}`
      GROUP BY event_ticker
    ),
    latest_market AS (
      SELECT market_ticker, event_ticker, status,
             COALESCE(volume_dollars, 0) AS volume_dollars,
             COALESCE(open_interest_dollars, 0) AS open_interest_dollars
      FROM `{_table_ref(BQ_DATASET_CORE, "market_state_core")}`
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY market_ticker
        ORDER BY snapshot_ts DESC, ingestion_date DESC, close_time DESC, event_ticker DESC
      ) = 1
    ),
    latest_event AS (
      SELECT event_ticker, title FROM `{_table_ref(BQ_DATASET_CORE, "events_dim")}`
    )
    SELECT
      (SELECT COUNT(*) FROM market_latest_counts WHERE latest_true_count != 1) AS market_latest_violations,
      (SELECT COUNT(*) FROM event_latest_counts WHERE row_count != 1 OR latest_true_count != 1) AS event_latest_violations,
      COALESCE(
        (
          SELECT SAFE_DIVIDE(
            COUNTIF(COALESCE(e.title, "") != "" AND NOT STARTS_WITH(COALESCE(e.title, ""), "KX")),
            COUNT(*)
          )
          FROM latest_market AS m
          LEFT JOIN latest_event AS e USING (event_ticker)
          WHERE LOWER(COALESCE(m.status, "")) IN ("open", "active")
            AND (m.volume_dollars > 0 OR m.open_interest_dollars > 0)
        ),
        1.0
      ) AS active_event_title_coverage
    """
    result = _run_query(quality_sql)
    row = next(iter(result.result()), None)
    checks = {
        "market_latest_violations": int(row["market_latest_violations"]) if row else 1,
        "event_latest_violations": int(row["event_latest_violations"]) if row else 1,
        "active_event_title_coverage": float(row["active_event_title_coverage"]) if row else 0.0,
    }

    rows = [
        _quality_row(run_id, "market_latest_violations",
                     "PASS" if checks["market_latest_violations"] <= QUALITY_MAX_MARKET_LATEST_VIOLATIONS else "FAIL",
                     checks["market_latest_violations"], QUALITY_MAX_MARKET_LATEST_VIOLATIONS),
        _quality_row(run_id, "event_latest_violations",
                     "PASS" if checks["event_latest_violations"] <= QUALITY_MAX_EVENT_LATEST_VIOLATIONS else "FAIL",
                     checks["event_latest_violations"], QUALITY_MAX_EVENT_LATEST_VIOLATIONS),
        _quality_row(run_id, "active_event_title_coverage",
                     "PASS" if checks["active_event_title_coverage"] >= QUALITY_MIN_EVENT_TITLE_COVERAGE else "FAIL",
                     checks["active_event_title_coverage"], QUALITY_MIN_EVENT_TITLE_COVERAGE),
    ]
    _insert_rows(BQ_DATASET_OPS, "quality_results", rows)

    any_failed = any(item["status"] == "FAIL" for item in rows)
    if any_failed:
        log.warning("Post-publish quality checks have FAILures: %s (continuing)", checks)
    else:
        log.info("Post-publish quality checks PASSED: %s", checks)

    return {"run_id": run_id, "status": "FAIL" if any_failed else "PASS", "checks": checks}


def write_signal_run_summary(run_id: str) -> dict[str, Any]:
    """Query signal feed and write a signal run summary to ops."""
    from google.cloud import bigquery

    log.info("Writing signal run summary ...")

    counts_sql = f"""
    SELECT signal_type, COUNT(*) AS signal_count
    FROM `{_table_ref(BQ_DATASET_SIGNAL, "vw_signal_feed_latest")}`
    GROUP BY signal_type ORDER BY signal_count DESC, signal_type
    """
    top_signals_sql = f"""
    SELECT signal_id, signal_type, entity_type, entity_id, title, score, severity, signal_ts
    FROM `{_table_ref(BQ_DATASET_SIGNAL, "vw_signal_feed_latest")}`
    ORDER BY score DESC, signal_ts DESC LIMIT 10
    """
    top_entities_sql = f"""
    SELECT entity_type, entity_id, ANY_VALUE(title) AS title, COUNT(*) AS signal_count, MAX(score) AS max_score
    FROM `{_table_ref(BQ_DATASET_SIGNAL, "vw_signal_feed_latest")}`
    GROUP BY entity_type, entity_id ORDER BY max_score DESC, signal_count DESC LIMIT 10
    """

    counts_rows = [dict(row.items()) for row in _run_query(counts_sql).result()]
    top_signals_rows = [dict(row.items()) for row in _run_query(top_signals_sql).result()]
    top_entities_rows = [dict(row.items()) for row in _run_query(top_entities_sql).result()]

    run_ts = datetime.now(timezone.utc)
    signal_count = sum(int(row["signal_count"]) for row in counts_rows)

    merge_sql = f"""
    MERGE `{_table_ref(BQ_DATASET_OPS, "signal_runs")}` AS t
    USING (
      SELECT @run_id AS run_id, @run_ts AS run_ts, @status AS status,
             @signal_count AS signal_count,
             PARSE_JSON(@signal_type_counts_json) AS signal_type_counts,
             PARSE_JSON(@top_entities_json) AS top_entities_json,
             PARSE_JSON(@top_signals_json) AS top_signals_json,
             CURRENT_TIMESTAMP() AS created_at
    ) AS s
    ON t.run_id = s.run_id
    WHEN MATCHED THEN UPDATE SET
      run_ts = s.run_ts, status = s.status, signal_count = s.signal_count,
      signal_type_counts = s.signal_type_counts, top_entities_json = s.top_entities_json,
      top_signals_json = s.top_signals_json, created_at = s.created_at
    WHEN NOT MATCHED THEN INSERT (
      run_id, run_ts, status, signal_count, signal_type_counts,
      top_entities_json, top_signals_json, created_at
    ) VALUES (
      s.run_id, s.run_ts, s.status, s.signal_count, s.signal_type_counts,
      s.top_entities_json, s.top_signals_json, s.created_at
    )
    """
    _run_query(
        merge_sql,
        [
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
            bigquery.ScalarQueryParameter("run_ts", "TIMESTAMP", run_ts),
            bigquery.ScalarQueryParameter("status", "STRING", "ok"),
            bigquery.ScalarQueryParameter("signal_count", "INT64", signal_count),
            bigquery.ScalarQueryParameter("signal_type_counts_json", "STRING", json.dumps(counts_rows, default=str)),
            bigquery.ScalarQueryParameter("top_entities_json", "STRING", json.dumps(top_entities_rows, default=str)),
            bigquery.ScalarQueryParameter("top_signals_json", "STRING", json.dumps(top_signals_rows, default=str)),
        ],
    )

    log.info("Signal run summary: %d total signals.", signal_count)
    return {
        "run_id": run_id,
        "run_ts": run_ts.isoformat(),
        "signal_count": signal_count,
        "status": "ok",
        "top_signals": top_signals_rows,
        "top_entities": top_entities_rows,
    }


def write_market_intelligence_reports(signal_run: dict[str, Any]) -> dict[str, Any]:
    """Generate and persist market intelligence reports."""
    from google.cloud import bigquery

    log.info("Writing market intelligence reports ...")

    top_markets_sql = f"""
    SELECT entity_id, title, signal_type, score, severity, signal_ts
    FROM `{_table_ref(BQ_DATASET_SIGNAL, "vw_signal_feed_latest")}`
    WHERE entity_type = 'market' ORDER BY score DESC, signal_ts DESC LIMIT 10
    """
    top_events_sql = f"""
    SELECT entity_id, title, signal_type, score, severity, signal_ts
    FROM `{_table_ref(BQ_DATASET_SIGNAL, "vw_signal_feed_latest")}`
    WHERE entity_type = 'event' ORDER BY score DESC, signal_ts DESC LIMIT 10
    """
    top_markets = [dict(row.items()) for row in _run_query(top_markets_sql).result()]
    top_events = [dict(row.items()) for row in _run_query(top_events_sql).result()]
    top_signals = signal_run.get("top_signals", [])
    local_now = _current_local_time()
    report_ts = datetime.now(timezone.utc)

    reports: list[tuple[str, str, dict[str, Any]]] = [
        (
            f"intraday-{local_now.strftime('%Y%m%d%H%M')}",
            "intraday",
            {
                "signal_count": signal_run.get("signal_count", 0),
                "top_signal_count": len(top_signals),
                "generated_local_time": local_now.isoformat(),
            },
        )
    ]
    if local_now.hour == KALSHI_DAILY_REPORT_HOUR and local_now.minute == KALSHI_DAILY_REPORT_MINUTE:
        reports.append(
            (
                f"daily-{local_now.strftime('%Y%m%d')}",
                "daily",
                {
                    "signal_count": signal_run.get("signal_count", 0),
                    "top_market_count": len(top_markets),
                    "top_event_count": len(top_events),
                    "generated_local_time": local_now.isoformat(),
                },
            )
        )

    merge_sql = f"""
    MERGE `{_table_ref(BQ_DATASET_OPS, "market_intelligence_reports")}` AS t
    USING (
      SELECT @report_id AS report_id, @report_ts AS report_ts, @report_type AS report_type,
             PARSE_JSON(@summary_json) AS summary_json,
             PARSE_JSON(@top_signals_json) AS top_signals_json,
             PARSE_JSON(@top_markets_json) AS top_markets_json,
             PARSE_JSON(@top_events_json) AS top_events_json,
             CURRENT_TIMESTAMP() AS created_at
    ) AS s
    ON t.report_id = s.report_id
    WHEN MATCHED THEN UPDATE SET
      report_ts = s.report_ts, report_type = s.report_type, summary_json = s.summary_json,
      top_signals_json = s.top_signals_json, top_markets_json = s.top_markets_json,
      top_events_json = s.top_events_json, created_at = s.created_at
    WHEN NOT MATCHED THEN INSERT (
      report_id, report_ts, report_type, summary_json,
      top_signals_json, top_markets_json, top_events_json, created_at
    ) VALUES (
      s.report_id, s.report_ts, s.report_type, s.summary_json,
      s.top_signals_json, s.top_markets_json, s.top_events_json, s.created_at
    )
    """
    rows_written = 0
    for report_id, report_type, summary in reports:
        _run_query(
            merge_sql,
            [
                bigquery.ScalarQueryParameter("report_id", "STRING", report_id),
                bigquery.ScalarQueryParameter("report_ts", "TIMESTAMP", report_ts),
                bigquery.ScalarQueryParameter("report_type", "STRING", report_type),
                bigquery.ScalarQueryParameter("summary_json", "STRING", json.dumps(summary, default=str)),
                bigquery.ScalarQueryParameter("top_signals_json", "STRING", json.dumps(top_signals, default=str)),
                bigquery.ScalarQueryParameter("top_markets_json", "STRING", json.dumps(top_markets, default=str)),
                bigquery.ScalarQueryParameter("top_events_json", "STRING", json.dumps(top_events, default=str)),
            ],
        )
        rows_written += 1

    log.info("Wrote %d market intelligence reports.", rows_written)
    return {
        "run_id": signal_run["run_id"],
        "rows_written": rows_written,
        "generated_at": report_ts.isoformat(),
        "report_types": [rt for _, rt, _ in reports],
    }


def run_signal_quality_checks(run_id: str) -> dict[str, Any]:
    """Run quality checks on signal data."""
    log.info("Running signal quality checks ...")

    signal_quality_sql = f"""
    WITH feed AS (
      SELECT * FROM `{_table_ref(BQ_DATASET_SIGNAL, "vw_signal_feed_latest")}`
    ),
    latest_run AS (
      SELECT MAX(run_ts) AS max_run_ts FROM `{_table_ref(BQ_DATASET_OPS, "signal_runs")}`
    )
    SELECT
      COUNT(*) - COUNT(DISTINCT signal_id) AS duplicate_signal_id_count,
      COUNTIF(entity_id IS NULL OR entity_id = '') AS null_signal_entity_count,
      COUNTIF(score IS NULL OR score = 0) AS zero_score_count,
      COUNTIF(title IS NULL OR title = '') AS null_title_count,
      COUNT(*) AS total_signal_count,
      IFNULL(TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), (SELECT max_run_ts FROM latest_run), MINUTE), 1e9) AS minutes_since_latest_signal_run
    FROM feed
    """
    result = _run_query(signal_quality_sql)
    row = next(iter(result.result()), None)
    checks = {
        "duplicate_signal_id_count": int(row["duplicate_signal_id_count"]) if row else 1,
        "null_signal_entity_count": int(row["null_signal_entity_count"]) if row else 1,
        "zero_score_count": int(row["zero_score_count"]) if row else 0,
        "null_title_count": int(row["null_title_count"]) if row else 0,
        "total_signal_count": int(row["total_signal_count"]) if row else 0,
        "minutes_since_latest_signal_run": float(row["minutes_since_latest_signal_run"]) if row else 1e9,
    }

    status_rows = [
        _quality_row(run_id, "duplicate_signal_id_count",
                     "PASS" if checks["duplicate_signal_id_count"] <= QUALITY_MAX_DUPLICATE_SIGNAL_ID_COUNT else "FAIL",
                     checks["duplicate_signal_id_count"], QUALITY_MAX_DUPLICATE_SIGNAL_ID_COUNT),
        _quality_row(run_id, "null_signal_entity_count",
                     "PASS" if checks["null_signal_entity_count"] <= QUALITY_MAX_NULL_SIGNAL_ENTITY_COUNT else "FAIL",
                     checks["null_signal_entity_count"], QUALITY_MAX_NULL_SIGNAL_ENTITY_COUNT),
        _quality_row(run_id, "minutes_since_latest_signal_run",
                     "PASS" if checks["minutes_since_latest_signal_run"] <= QUALITY_MAX_FRESHNESS_MINUTES else "FAIL",
                     checks["minutes_since_latest_signal_run"], QUALITY_MAX_FRESHNESS_MINUTES),
        _quality_row(run_id, "signal_total_count",
                     "PASS" if checks["total_signal_count"] > 0 else "FAIL",
                     checks["total_signal_count"], 1),
    ]
    _insert_rows(BQ_DATASET_OPS, "quality_results", status_rows)

    any_failed = any(item["status"] == "FAIL" for item in status_rows)
    if any_failed:
        log.warning("Signal quality checks have FAILures: %s (continuing)", checks)
    else:
        log.info("Signal quality checks PASSED: %s", checks)

    return {"run_id": run_id, "status": "FAIL" if any_failed else "PASS", "checks": checks}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    log.info("=== Kalshi Signals (single-shot) ===")

    run_id = str(uuid.uuid4())

    # 1. Post-publish quality checks on core tables
    run_post_publish_quality_checks(run_id)

    # 2. Write signal run summary
    signal_run = write_signal_run_summary(run_id)

    # 3. Write market intelligence reports
    write_market_intelligence_reports(signal_run)

    # 4. Run signal quality checks
    run_signal_quality_checks(run_id)

    log.info("=== Done. Kalshi signals complete for run %s. ===", run_id)


if __name__ == "__main__":
    main()
