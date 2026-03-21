"""Airflow DAG skeleton for autonomous Odds API ingestion into BigQuery.

This v0 DAG is free-tier aware:
- Uses a no-cost quota heartbeat call before any paid endpoint.
- Computes budget-safe paid windows for each UTC day.
- Degrades from odds+scores to odds-only when credits are tight.
"""

from __future__ import annotations

import json
import os
import uuid
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from airflow import DAG
from airflow.decorators import task
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import BranchPythonOperator

ODDS_API_BASE_URL = os.getenv("ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
TARGET_SPORT = os.getenv("TARGET_SPORT", "basketball_nba")
TARGET_REGIONS = os.getenv("TARGET_REGIONS", "us")
TARGET_MARKETS = os.getenv("TARGET_MARKETS", "h2h")
SCORES_DAYS_FROM = int(os.getenv("SCORES_DAYS_FROM", "1"))
COMMENCE_WINDOW_HOURS = int(os.getenv("COMMENCE_WINDOW_HOURS", "24"))

MONTHLY_CREDIT_CAP = int(os.getenv("MONTHLY_CREDIT_CAP", "500"))
CREDIT_RESERVE = int(os.getenv("CREDIT_RESERVE", "25"))
MAX_PAID_CYCLES_PER_DAY = int(os.getenv("MAX_PAID_CYCLES_PER_DAY", "8"))
ENABLE_SCORES_FETCH = os.getenv("ENABLE_SCORES_FETCH", "true").lower() == "true"

# BigQuery identifiers are placeholders; wire your own client/operator in each task.
BQ_PROJECT = os.getenv("BQ_PROJECT", "YOUR_PROJECT_ID")
BQ_DATASET_RAW = os.getenv("BQ_DATASET_RAW", "odds_raw")
BQ_DATASET_STG = os.getenv("BQ_DATASET_STG", "odds_stg")
BQ_DATASET_CORE = os.getenv("BQ_DATASET_CORE", "odds_core")
BQ_DATASET_OPS = os.getenv("BQ_DATASET_OPS", "odds_ops")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_bq_project() -> None:
    if not BQ_PROJECT or BQ_PROJECT == "YOUR_PROJECT_ID":
        raise ValueError("Set BQ_PROJECT to a valid GCP project ID before running this DAG.")
    if "." in BQ_PROJECT:
        raise ValueError("BQ_PROJECT must be only the GCP project id, not project.dataset.")


def _get_bq_client():
    _require_bq_project()
    from google.cloud import bigquery

    return bigquery.Client(project=BQ_PROJECT)


def _table_ref(dataset: str, table: str) -> str:
    return f"{BQ_PROJECT}.{dataset}.{table}"


def _insert_rows(dataset: str, table: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    client = _get_bq_client()
    errors = client.insert_rows_json(_table_ref(dataset, table), rows)
    if errors:
        raise RuntimeError(f"BigQuery insert errors for {_table_ref(dataset, table)}: {errors}")
    return len(rows)


def _run_query(sql: str, query_parameters: list[Any] | None = None):
    from google.cloud import bigquery

    client = _get_bq_client()
    config = bigquery.QueryJobConfig(query_parameters=query_parameters or [])
    job = client.query(sql, job_config=config)
    job.result()
    return job


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
        "metric_value": float(metric_value),
        "threshold_value": float(threshold_value),
        "details": _json_cell(details or {}),
        "checked_at": _utcnow_iso(),
    }


def _winner(home_score: int | None, away_score: int | None, home_team: str | None, away_team: str | None) -> str | None:
    if home_score is None or away_score is None:
        return None
    if home_score > away_score:
        return home_team
    if away_score > home_score:
        return away_team
    return "draw"


def _split_csv(value: str) -> list[str]:
    return [token.strip() for token in value.split(",") if token.strip()]


def _estimate_odds_cost(markets_csv: str, regions_csv: str) -> int:
    markets = set(_split_csv(markets_csv))
    regions = set(_split_csv(regions_csv))
    return max(1, len(markets) * len(regions))


def _evenly_spaced_hours(calls_per_day: int) -> list[int]:
    if calls_per_day <= 0:
        return []
    if calls_per_day >= 24:
        return list(range(24))
    return sorted({(i * 24) // calls_per_day for i in range(calls_per_day)})


def _api_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    if not ODDS_API_KEY:
        raise ValueError("ODDS_API_KEY is not set")

    merged = dict(params)
    merged["apiKey"] = ODDS_API_KEY
    url = f"{ODDS_API_BASE_URL}{path}?{urlencode(merged)}"

    req = Request(url, method="GET")
    with urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
        body = json.loads(raw)
        headers = dict(resp.headers.items())
        status = int(getattr(resp, "status", 200))

    def _as_int(value: str | None) -> int | None:
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None

    return {
        "request_url": url,
        "response_json": body,
        "response_headers": headers,
        "response_status": status,
        "quota": {
            "x_requests_remaining": _as_int(headers.get("x-requests-remaining")),
            "x_requests_used": _as_int(headers.get("x-requests-used")),
            "x_requests_last": _as_int(headers.get("x-requests-last")),
        },
        "fetched_at": _utcnow_iso(),
    }


with DAG(
    dag_id="odds_api_autonomous_de_v0",
    description="Credit-aware Odds API ingestion with bounded autonomous DE governance",
    start_date=datetime(2026, 1, 1),
    schedule="@hourly",
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "data-platform"},
    tags=["odds", "autonomous", "bigquery", "v0", "free-tier"],
) as dag:
    start = EmptyOperator(task_id="start")
    stop_noop = EmptyOperator(task_id="stop_noop")
    end = EmptyOperator(task_id="end")

    @task(task_id="fetch_usage_heartbeat")
    def fetch_usage_heartbeat() -> dict[str, Any]:
        payload = _api_get("/sports/", {"all": "false"})
        payload["call_id"] = str(uuid.uuid4())
        payload["endpoint"] = "/sports/"
        return payload

    @task(task_id="plan_credit_budget")
    def plan_credit_budget(usage: dict[str, Any]) -> dict[str, Any]:
        now_utc = datetime.now(timezone.utc)
        days_in_month = monthrange(now_utc.year, now_utc.month)[1]
        days_remaining = days_in_month - now_utc.day + 1

        estimated_odds_cost = _estimate_odds_cost(TARGET_MARKETS, TARGET_REGIONS)
        estimated_scores_cost = 2 if ENABLE_SCORES_FETCH else 0
        include_scores_this_run = ENABLE_SCORES_FETCH

        remaining = usage.get("quota", {}).get("x_requests_remaining")
        used = usage.get("quota", {}).get("x_requests_used")

        if remaining is not None and include_scores_this_run:
            if remaining < (CREDIT_RESERVE + estimated_odds_cost + estimated_scores_cost):
                include_scores_this_run = False
                estimated_scores_cost = 0

        estimated_cycle_cost = estimated_odds_cost + estimated_scores_cost

        if estimated_cycle_cost <= 0:
            target_cycles_today = 0
        elif remaining is None:
            fallback = MONTHLY_CREDIT_CAP // max(1, days_in_month * estimated_cycle_cost)
            target_cycles_today = max(1, min(MAX_PAID_CYCLES_PER_DAY, fallback))
        else:
            spendable = max(0, remaining - CREDIT_RESERVE)
            target_daily_credits = spendable // max(1, days_remaining)
            target_cycles_today = target_daily_credits // max(1, estimated_cycle_cost)
            if spendable >= estimated_cycle_cost and target_cycles_today == 0:
                target_cycles_today = 1
            target_cycles_today = min(MAX_PAID_CYCLES_PER_DAY, target_cycles_today)

        paid_hours_utc = _evenly_spaced_hours(target_cycles_today)

        if estimated_cycle_cost <= 0:
            should_run_paid = False
            reason = "Estimated cycle cost is 0; paid run disabled"
        elif not paid_hours_utc:
            should_run_paid = False
            reason = "No paid slots allocated for today"
        elif remaining is not None and remaining <= CREDIT_RESERVE:
            should_run_paid = False
            reason = "Remaining credits are at or below reserve"
        elif remaining is not None and remaining < estimated_odds_cost:
            should_run_paid = False
            reason = "Remaining credits below minimum odds call cost"
        elif now_utc.hour not in paid_hours_utc:
            should_run_paid = False
            reason = f"Current hour {now_utc.hour:02d} UTC is outside paid windows {paid_hours_utc}"
        else:
            should_run_paid = True
            reason = "Within paid slot and credit policy allows run"

        return {
            "run_id": str(uuid.uuid4()),
            "decided_at": _utcnow_iso(),
            "target_sport": TARGET_SPORT,
            "target_regions": TARGET_REGIONS,
            "target_markets": TARGET_MARKETS,
            "x_requests_remaining": remaining,
            "x_requests_used": used,
            "estimated_odds_cost": estimated_odds_cost,
            "estimated_scores_cost": estimated_scores_cost,
            "estimated_cycle_cost": estimated_cycle_cost,
            "days_remaining_in_month": days_remaining,
            "target_cycles_today": target_cycles_today,
            "paid_hours_utc": paid_hours_utc,
            "include_scores_this_run": include_scores_this_run,
            "should_run_paid": should_run_paid,
            "decision_reason": reason,
        }

    @task(task_id="record_budget_decision")
    def record_budget_decision(plan: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
        _insert_rows(
            BQ_DATASET_OPS,
            "credit_budget_log",
            [
                {
                    "run_id": plan["run_id"],
                    "decided_at": plan["decided_at"],
                    "target_sport": plan["target_sport"],
                    "target_regions": plan["target_regions"],
                    "target_markets": plan["target_markets"],
                    "x_requests_remaining": plan["x_requests_remaining"],
                    "x_requests_used": plan["x_requests_used"],
                    "estimated_odds_cost": int(plan["estimated_odds_cost"]),
                    "estimated_scores_cost": int(plan["estimated_scores_cost"]),
                    "estimated_cycle_cost": int(plan["estimated_cycle_cost"]),
                    "days_remaining_in_month": int(plan["days_remaining_in_month"]),
                    "target_cycles_today": int(plan["target_cycles_today"]),
                    "paid_hours_utc": json.dumps(plan["paid_hours_utc"], separators=(",", ":")),
                    "include_scores_this_run": bool(plan["include_scores_this_run"]),
                    "should_run_paid": bool(plan["should_run_paid"]),
                    "decision_reason": plan["decision_reason"],
                }
            ],
        )
        _insert_rows(
            BQ_DATASET_RAW,
            "api_call_log",
            [
                {
                    "call_id": usage["call_id"],
                    "endpoint": usage["endpoint"],
                    "request_url": usage["request_url"],
                    "request_params": {"all": "false"},
                    "response_status": usage.get("response_status", 200),
                    "response_headers": usage.get("response_headers", {}),
                    "x_requests_remaining": usage.get("quota", {}).get("x_requests_remaining"),
                    "x_requests_used": usage.get("quota", {}).get("x_requests_used"),
                    "x_requests_last": usage.get("quota", {}).get("x_requests_last"),
                    "fetched_at": usage["fetched_at"],
                    "ingestion_date": datetime.now(timezone.utc).date().isoformat(),
                }
            ],
        )
        summary = {
            "run_id": plan["run_id"],
            "should_run_paid": plan["should_run_paid"],
            "reason": plan["decision_reason"],
            "remaining": plan["x_requests_remaining"],
            "used": plan["x_requests_used"],
            "heartbeat_call_id": usage["call_id"],
        }
        print(summary)
        return summary

    def _branch_budget_gate(ti, **_: Any) -> str | list[str]:
        plan = ti.xcom_pull(task_ids="plan_credit_budget")
        if not plan.get("should_run_paid"):
            return "stop_noop"
        return ["fetch_odds", "fetch_scores"]

    budget_branch = BranchPythonOperator(
        task_id="budget_branch",
        python_callable=_branch_budget_gate,
    )

    @task(task_id="fetch_odds")
    def fetch_odds(_: dict[str, Any]) -> dict[str, Any]:
        now_utc = datetime.now(timezone.utc)
        window_end = now_utc + timedelta(hours=COMMENCE_WINDOW_HOURS)
        payload = _api_get(
            f"/sports/{TARGET_SPORT}/odds",
            {
                "regions": TARGET_REGIONS,
                "markets": TARGET_MARKETS,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                "commenceTimeFrom": now_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "commenceTimeTo": window_end.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            },
        )
        payload["call_id"] = str(uuid.uuid4())
        payload["endpoint"] = f"/sports/{TARGET_SPORT}/odds"
        return payload

    @task(task_id="fetch_scores")
    def fetch_scores(plan: dict[str, Any]) -> dict[str, Any]:
        if not plan.get("include_scores_this_run", False):
            return {
                "call_id": str(uuid.uuid4()),
                "endpoint": f"/sports/{TARGET_SPORT}/scores",
                "skipped": True,
                "skip_reason": "Credit policy downgraded run to odds-only",
                "fetched_at": _utcnow_iso(),
                "quota": {
                    "x_requests_remaining": plan.get("x_requests_remaining"),
                    "x_requests_used": plan.get("x_requests_used"),
                    "x_requests_last": 0,
                },
            }

        payload = _api_get(
            f"/sports/{TARGET_SPORT}/scores",
            {
                "daysFrom": SCORES_DAYS_FROM,
                "dateFormat": "iso",
            },
        )
        payload["call_id"] = str(uuid.uuid4())
        payload["endpoint"] = f"/sports/{TARGET_SPORT}/scores"
        payload["skipped"] = False
        return payload

    @task(task_id="persist_raw_payloads")
    def persist_raw_payloads(
        usage: dict[str, Any],
        plan: dict[str, Any],
        odds: dict[str, Any],
        scores: dict[str, Any],
    ) -> dict[str, Any]:
        ingestion_date = datetime.now(timezone.utc).date().isoformat()
        api_rows = [
            {
                "call_id": odds["call_id"],
                "endpoint": odds["endpoint"],
                "request_url": odds["request_url"],
                "request_params": {
                    "regions": TARGET_REGIONS,
                    "markets": TARGET_MARKETS,
                    "oddsFormat": "decimal",
                    "dateFormat": "iso",
                },
                "response_status": odds.get("response_status", 200),
                "response_headers": odds.get("response_headers", {}),
                "x_requests_remaining": odds.get("quota", {}).get("x_requests_remaining"),
                "x_requests_used": odds.get("quota", {}).get("x_requests_used"),
                "x_requests_last": odds.get("quota", {}).get("x_requests_last"),
                "fetched_at": odds["fetched_at"],
                "ingestion_date": ingestion_date,
            }
        ]
        if not scores.get("skipped", False):
            api_rows.append(
                {
                    "call_id": scores["call_id"],
                    "endpoint": scores["endpoint"],
                    "request_url": scores["request_url"],
                    "request_params": {
                        "daysFrom": SCORES_DAYS_FROM,
                        "dateFormat": "iso",
                    },
                    "response_status": scores.get("response_status", 200),
                    "response_headers": scores.get("response_headers", {}),
                    "x_requests_remaining": scores.get("quota", {}).get("x_requests_remaining"),
                    "x_requests_used": scores.get("quota", {}).get("x_requests_used"),
                    "x_requests_last": scores.get("quota", {}).get("x_requests_last"),
                    "fetched_at": scores["fetched_at"],
                    "ingestion_date": ingestion_date,
                }
            )
        _insert_rows(BQ_DATASET_RAW, "api_call_log", api_rows)

        odds_rows = []
        for event in odds.get("response_json", []) or []:
            if not isinstance(event, dict):
                continue
            odds_rows.append(
                {
                    "call_id": odds["call_id"],
                    "sport_key": event.get("sport_key") or TARGET_SPORT,
                    "market_key": TARGET_MARKETS,
                    "region_key": TARGET_REGIONS,
                    "event_id": event.get("id"),
                    "commence_time": event.get("commence_time"),
                    "api_payload": event,
                    "fetched_at": odds["fetched_at"],
                    "ingestion_date": ingestion_date,
                }
            )
        _insert_rows(BQ_DATASET_RAW, "odds_events_raw", odds_rows)

        score_rows = []
        if not scores.get("skipped", False):
            for event in scores.get("response_json", []) or []:
                if not isinstance(event, dict):
                    continue
                score_rows.append(
                    {
                        "call_id": scores["call_id"],
                        "sport_key": event.get("sport_key") or TARGET_SPORT,
                        "event_id": event.get("id"),
                        "commence_time": event.get("commence_time"),
                        "completed": bool(event.get("completed", False)),
                        "api_payload": event,
                        "fetched_at": scores["fetched_at"],
                        "ingestion_date": ingestion_date,
                    }
                )
        _insert_rows(BQ_DATASET_RAW, "scores_events_raw", score_rows)
        return {
            "run_id": plan["run_id"],
            "usage_call_id": usage["call_id"],
            "odds_call_id": odds["call_id"],
            "scores_call_id": scores["call_id"],
            "scores_skipped": scores.get("skipped", False),
            "odds_response_json": odds.get("response_json", []),
            "scores_response_json": scores.get("response_json", []),
            "persisted_at": _utcnow_iso(),
        }

    @task(task_id="build_staging")
    def build_staging(raw: dict[str, Any]) -> dict[str, Any]:
        ingestion_date = datetime.now(timezone.utc).date().isoformat()
        odds_rows: list[dict[str, Any]] = []
        seen_odds_keys: set[tuple[Any, ...]] = set()
        for event in raw.get("odds_response_json", []) or []:
            if not isinstance(event, dict):
                continue
            snapshot_ts = event.get("last_update") or raw["persisted_at"]
            for bookmaker in event.get("bookmakers", []) or []:
                if not isinstance(bookmaker, dict):
                    continue
                bookmaker_key = bookmaker.get("key")
                for market in bookmaker.get("markets", []) or []:
                    if not isinstance(market, dict):
                        continue
                    market_key = market.get("key")
                    for outcome in market.get("outcomes", []) or []:
                        if not isinstance(outcome, dict):
                            continue
                        dedupe_key = (
                            event.get("id"),
                            bookmaker_key,
                            market_key,
                            outcome.get("name"),
                            snapshot_ts,
                        )
                        if dedupe_key in seen_odds_keys:
                            continue
                        seen_odds_keys.add(dedupe_key)
                        odds_rows.append(
                            {
                                "event_id": event.get("id"),
                                "sport_key": event.get("sport_key") or TARGET_SPORT,
                                "commence_time": event.get("commence_time"),
                                "bookmaker_key": bookmaker_key,
                                "market_key": market_key,
                                "outcome_name": outcome.get("name"),
                                "odds_price": outcome.get("price"),
                                "snapshot_ts": snapshot_ts,
                                "source_call_id": raw["odds_call_id"],
                                "ingestion_date": ingestion_date,
                            }
                        )
        _insert_rows(BQ_DATASET_STG, "odds_prices_stg", odds_rows)

        score_rows: list[dict[str, Any]] = []
        seen_score_keys: set[tuple[Any, ...]] = set()
        for event in raw.get("scores_response_json", []) or []:
            if not isinstance(event, dict):
                continue
            snapshot_ts = event.get("last_update") or raw["persisted_at"]
            completed = bool(event.get("completed", False))
            scores_by_name: dict[str, int] = {}
            for score_entry in event.get("scores", []) or []:
                if isinstance(score_entry, dict) and score_entry.get("name") is not None:
                    try:
                        scores_by_name[str(score_entry.get("name"))] = int(score_entry.get("score"))
                    except (TypeError, ValueError):
                        continue
            home_team = event.get("home_team")
            away_team = event.get("away_team")
            dedupe_key = (event.get("id"), snapshot_ts)
            if dedupe_key in seen_score_keys:
                continue
            seen_score_keys.add(dedupe_key)
            score_rows.append(
                {
                    "event_id": event.get("id"),
                    "sport_key": event.get("sport_key") or TARGET_SPORT,
                    "commence_time": event.get("commence_time"),
                    "home_team": home_team,
                    "away_team": away_team,
                    "home_score": scores_by_name.get(str(home_team)) if home_team is not None else None,
                    "away_score": scores_by_name.get(str(away_team)) if away_team is not None else None,
                    "completed": completed,
                    "snapshot_ts": snapshot_ts,
                    "source_call_id": raw["scores_call_id"],
                    "ingestion_date": ingestion_date,
                }
            )
        _insert_rows(BQ_DATASET_STG, "scores_stg", score_rows)
        return {
            "staging_status": "ok",
            "built_at": _utcnow_iso(),
            "run_id": raw["run_id"],
            "odds_rows": len(odds_rows),
            "score_rows": len(score_rows),
            "null_event_id_rows": sum(1 for row in odds_rows if not row.get("event_id")),
            "duplicate_ratio": 0.0,
            "freshness_minutes": 0.0,
        }

    @task(task_id="run_quality_gates")
    def run_quality_gates(staging: dict[str, Any]) -> dict[str, Any]:
        total_rows = max(1, int(staging.get("odds_rows", 0)))
        null_event_id_ratio = float(staging.get("null_event_id_rows", 0)) / total_rows
        duplicate_ratio = float(staging.get("duplicate_ratio", 0.0))
        freshness_minutes = float(staging.get("freshness_minutes", 0.0))
        checks = {
            "freshness_minutes": freshness_minutes,
            "null_event_id_ratio": null_event_id_ratio,
            "duplicate_ratio": duplicate_ratio,
            "odds_rows": int(staging.get("odds_rows", 0)),
            "score_rows": int(staging.get("score_rows", 0)),
        }
        rows = [
            _quality_row(staging["run_id"], "freshness_minutes", "PASS" if freshness_minutes <= 15 else "FAIL", freshness_minutes, 15, checks),
            _quality_row(staging["run_id"], "null_event_id_ratio", "PASS" if null_event_id_ratio <= 0.01 else "FAIL", null_event_id_ratio, 0.01, checks),
            _quality_row(staging["run_id"], "duplicate_ratio", "PASS" if duplicate_ratio <= 0.01 else "FAIL", duplicate_ratio, 0.01, checks),
        ]
        _insert_rows(BQ_DATASET_OPS, "quality_results", rows)
        overall_status = "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL"
        return {"status": overall_status, "checks": checks, "run_id": staging["run_id"]}

    def _branch_quality_gate(ti, **_: Any) -> str:
        quality = ti.xcom_pull(task_ids="run_quality_gates")
        return "publish_core_tables" if quality.get("status") == "PASS" else "stop_noop"

    quality_branch = BranchPythonOperator(
        task_id="quality_branch",
        python_callable=_branch_quality_gate,
    )

    @task(task_id="publish_core_tables")
    def publish_core_tables() -> dict[str, Any]:
        odds_sql = f"""
        CREATE OR REPLACE TABLE `{_table_ref(BQ_DATASET_CORE, "odds_prices_core")}`
        PARTITION BY ingestion_date
        CLUSTER BY sport_key, market_key, event_id AS
        WITH ranked AS (
          SELECT
            event_id,
            sport_key,
            commence_time,
            bookmaker_key,
            market_key,
            outcome_name,
            odds_price,
            CASE WHEN odds_price > 0 THEN SAFE_DIVIDE(1.0, odds_price) ELSE NULL END AS implied_probability,
            snapshot_ts,
            ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY snapshot_ts DESC) = 1 AS is_latest_for_event,
            ingestion_date
          FROM `{_table_ref(BQ_DATASET_STG, "odds_prices_stg")}`
        )
        SELECT * FROM ranked
        """
        outcomes_sql = f"""
        CREATE OR REPLACE TABLE `{_table_ref(BQ_DATASET_CORE, "event_outcomes_core")}`
        PARTITION BY ingestion_date
        CLUSTER BY sport_key, completed AS
        SELECT
          event_id,
          sport_key,
          commence_time,
          home_team,
          away_team,
          home_score,
          away_score,
          CASE
            WHEN home_score IS NULL OR away_score IS NULL THEN NULL
            WHEN home_score > away_score THEN home_team
            WHEN away_score > home_score THEN away_team
            ELSE 'draw'
          END AS winner,
          completed,
          snapshot_ts,
          ingestion_date
        FROM `{_table_ref(BQ_DATASET_STG, "scores_stg")}`
        """
        _run_query(odds_sql)
        _run_query(outcomes_sql)
        return {"publish_status": "ok", "published_at": _utcnow_iso()}

    @task(task_id="compute_kpis")
    def compute_kpis(_: dict[str, Any]) -> dict[str, Any]:
        kpi_sql = f"""
        CREATE OR REPLACE TABLE `{_table_ref(BQ_DATASET_CORE, "kpi_hourly")}`
        PARTITION BY DATE(kpi_ts)
        CLUSTER BY sport_key, market_key AS
        SELECT
          TIMESTAMP_TRUNC(snapshot_ts, HOUR) AS kpi_ts,
          sport_key,
          market_key,
          COUNT(DISTINCT event_id) AS total_events,
          COUNT(DISTINCT IF(is_latest_for_event, event_id, NULL)) AS completed_events,
          AVG(implied_probability) AS avg_implied_prob,
          COUNT(*) AS odds_snapshot_count,
          CURRENT_TIMESTAMP() AS updated_at
        FROM `{_table_ref(BQ_DATASET_CORE, "odds_prices_core")}`
        GROUP BY 1, 2, 3
        """
        _run_query(kpi_sql)
        return {"kpi_status": "ok", "computed_at": _utcnow_iso()}

    @task(task_id="emit_run_summary")
    def emit_run_summary(kpi: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        summary = {
            "run_status": "success",
            "kpi": kpi,
            "credit_plan": plan,
        }
        _insert_rows(
            BQ_DATASET_OPS,
            "run_summary",
            [
                {
                    "run_id": plan["run_id"],
                    "run_status": "success",
                    "summary_json": summary,
                    "emitted_at": _utcnow_iso(),
                }
            ],
        )
        print(summary)
        return summary

    usage = fetch_usage_heartbeat()
    plan = plan_credit_budget(usage)
    budget_log = record_budget_decision(plan, usage)
    odds = fetch_odds(plan)
    scores = fetch_scores(plan)
    raw = persist_raw_payloads(usage, plan, odds, scores)
    stg = build_staging(raw)
    quality = run_quality_gates(stg)
    published = publish_core_tables()
    kpi = compute_kpis(published)
    run_summary = emit_run_summary(kpi, plan)

    start >> usage >> plan >> budget_log >> budget_branch
    budget_branch >> stop_noop >> end
    budget_branch >> [odds, scores]
    [odds, scores] >> raw >> stg >> quality >> quality_branch
    quality_branch >> stop_noop
    quality_branch >> published >> kpi >> run_summary >> end
