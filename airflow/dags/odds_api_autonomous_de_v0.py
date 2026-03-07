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

    def _as_int(value: str | None) -> int | None:
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None

    return {
        "request_url": url,
        "response_json": body,
        "response_headers": headers,
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
        # TODO: insert plan row into odds_ops.credit_budget_log.
        # Also persist usage heartbeat headers in odds_raw.api_call_log.
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
        if plan.get("include_scores_this_run"):
            return ["fetch_odds", "fetch_scores"]
        return "fetch_odds"

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
        # TODO: insert into BigQuery:
        # - odds_raw.api_call_log for usage heartbeat + odds + scores
        # - odds_raw.odds_events_raw
        # - odds_raw.scores_events_raw (when scores["skipped"] is False)
        # - odds_ops.credit_budget_log with plan data
        return {
            "run_id": plan["run_id"],
            "usage_call_id": usage["call_id"],
            "odds_call_id": odds["call_id"],
            "scores_call_id": scores["call_id"],
            "scores_skipped": scores.get("skipped", False),
            "persisted_at": _utcnow_iso(),
        }

    @task(task_id="build_staging")
    def build_staging(_: dict[str, Any]) -> dict[str, Any]:
        # TODO: parse JSON payloads into odds_stg.odds_prices_stg and odds_stg.scores_stg.
        # Include dedupe keys: event_id + bookmaker_key + market_key + outcome_name + snapshot_ts.
        return {"staging_status": "ok", "built_at": _utcnow_iso()}

    @task(task_id="run_quality_gates")
    def run_quality_gates(_: dict[str, Any]) -> dict[str, Any]:
        # TODO: implement checks and write to odds_ops.quality_results.
        # Suggested checks: freshness, non-null event_id, duplicate ratio, row-count delta.
        return {
            "status": "PASS",
            "checks": {
                "freshness_minutes": 3.0,
                "null_event_id_ratio": 0.0,
                "duplicate_ratio": 0.0,
            },
        }

    def _branch_quality_gate(ti, **_: Any) -> str:
        quality = ti.xcom_pull(task_ids="run_quality_gates")
        return "propose_schema_changes" if quality.get("status") == "PASS" else "stop_noop"

    quality_branch = BranchPythonOperator(
        task_id="quality_branch",
        python_callable=_branch_quality_gate,
    )

    @task(task_id="propose_schema_changes")
    def propose_schema_changes() -> dict[str, Any]:
        # TODO: DE agent implementation.
        # Read doc hash + observed drift and emit proposal records into odds_ops.schema_change_proposals.
        return {
            "proposal_id": str(uuid.uuid4()),
            "status": "NO_CHANGE",
            "risk_level": "low",
            "target": f"{BQ_PROJECT}.{BQ_DATASET_STG}.odds_prices_stg",
            "proposed_at": _utcnow_iso(),
        }

    def _branch_policy_gate(ti, **_: Any) -> str:
        proposal = ti.xcom_pull(task_ids="propose_schema_changes")
        if proposal.get("status") == "NO_CHANGE":
            return "skip_schema_apply"
        risk = proposal.get("risk_level", "high").lower()
        return "apply_schema_change" if risk == "low" else "skip_schema_apply"

    policy_branch = BranchPythonOperator(
        task_id="policy_branch",
        python_callable=_branch_policy_gate,
    )

    @task(task_id="apply_schema_change")
    def apply_schema_change(_: dict[str, Any]) -> dict[str, Any]:
        # TODO: execute approved additive DDL against staging datasets only.
        return {"apply_status": "applied", "applied_at": _utcnow_iso()}

    skip_schema_apply = EmptyOperator(task_id="skip_schema_apply")

    @task(task_id="publish_core_tables")
    def publish_core_tables() -> dict[str, Any]:
        # TODO: MERGE/INSERT into odds_core.odds_prices_core and odds_core.event_outcomes_core.
        return {"publish_status": "ok", "published_at": _utcnow_iso()}

    @task(task_id="compute_kpis")
    def compute_kpis(_: dict[str, Any]) -> dict[str, Any]:
        # TODO: update odds_core.kpi_hourly.
        return {"kpi_status": "ok", "computed_at": _utcnow_iso()}

    @task(task_id="emit_run_summary")
    def emit_run_summary(kpi: dict[str, Any], plan: dict[str, Any]) -> None:
        # TODO: push to monitoring/alerting destination.
        print({"run_status": "success", "kpi": kpi, "credit_plan": plan})

    usage = fetch_usage_heartbeat()
    plan = plan_credit_budget(usage)
    budget_log = record_budget_decision(plan, usage)
    odds = fetch_odds(plan)
    scores = fetch_scores(plan)
    raw = persist_raw_payloads(usage, plan, odds, scores)
    stg = build_staging(raw)
    quality = run_quality_gates(stg)
    proposal = propose_schema_changes()
    applied = apply_schema_change(proposal)
    published = publish_core_tables()
    kpi = compute_kpis(published)
    run_summary = emit_run_summary(kpi, plan)

    start >> usage >> plan >> budget_log >> budget_branch
    budget_branch >> stop_noop >> end
    budget_branch >> [odds, scores]
    [odds, scores] >> raw >> stg >> quality >> quality_branch
    quality_branch >> stop_noop
    quality_branch >> proposal >> policy_branch
    policy_branch >> skip_schema_apply >> published
    policy_branch >> applied >> published
    published >> kpi >> run_summary >> end
