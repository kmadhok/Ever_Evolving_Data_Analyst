"""Airflow DAG skeleton for Kalshi-first autonomous market ingestion.

Design goals:
- Rate-limit aware polling (tier-based governance, not monthly credit governance).
- Cursor-based pagination for markets and trades endpoints.
- Bounded autonomous DE lane for additive schema/parser evolution.

Note: Keep this DAG as the primary source. Odds API can run as a companion DAG.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from airflow import DAG
from airflow.decorators import task
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import BranchPythonOperator

KALSHI_API_BASE_URL = os.getenv("KALSHI_API_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2")

KALSHI_MARKET_STATUS = os.getenv("KALSHI_MARKET_STATUS", "open")
KALSHI_SERIES_TICKER = os.getenv("KALSHI_SERIES_TICKER", "")
KALSHI_MARKET_PAGE_LIMIT = int(os.getenv("KALSHI_MARKET_PAGE_LIMIT", "100"))
KALSHI_TRADE_PAGE_LIMIT = int(os.getenv("KALSHI_TRADE_PAGE_LIMIT", "100"))
KALSHI_ORDERBOOK_DEPTH = int(os.getenv("KALSHI_ORDERBOOK_DEPTH", "10"))
KALSHI_TRADE_LOOKBACK_MINUTES = int(os.getenv("KALSHI_TRADE_LOOKBACK_MINUTES", "30"))

# Rate-limit governance. Keep conservative defaults for Basic tier (20 reads/s).
KALSHI_USAGE_TIER = os.getenv("KALSHI_USAGE_TIER", "basic")
KALSHI_CONFIGURED_READ_RPS = int(os.getenv("KALSHI_READ_RPS", "5"))
KALSHI_MAX_TIER_READ_RPS = int(os.getenv("KALSHI_MAX_TIER_READ_RPS", "20"))
KALSHI_MAX_REQUESTS_PER_RUN = int(os.getenv("KALSHI_MAX_REQUESTS_PER_RUN", "120"))
KALSHI_MAX_EVENT_PAGES = int(os.getenv("KALSHI_MAX_EVENT_PAGES", "60"))
KALSHI_MAX_MARKET_PAGES = int(os.getenv("KALSHI_MAX_MARKET_PAGES", "6"))
KALSHI_MAX_TRADE_PAGES = int(os.getenv("KALSHI_MAX_TRADE_PAGES", "4"))
KALSHI_MAX_ORDERBOOK_MARKETS = int(os.getenv("KALSHI_MAX_ORDERBOOK_MARKETS", "25"))
KALSHI_EVENT_BACKFILL_MAX_EVENTS = int(os.getenv("KALSHI_EVENT_BACKFILL_MAX_EVENTS", "20"))
KALSHI_EVENT_BACKFILL_LOOKBACK_HOURS = int(os.getenv("KALSHI_EVENT_BACKFILL_LOOKBACK_HOURS", "24"))
KALSHI_429_RETRY_DELAY_SECONDS = int(os.getenv("KALSHI_429_RETRY_DELAY_SECONDS", "2"))

# BigQuery identifiers are placeholders; wire your own client/operator in each task.
BQ_PROJECT = os.getenv("BQ_PROJECT", "YOUR_PROJECT_ID")
BQ_DATASET_RAW = os.getenv("BQ_DATASET_RAW", "kalshi_raw")
BQ_DATASET_STG = os.getenv("BQ_DATASET_STG", "kalshi_stg")
BQ_DATASET_CORE = os.getenv("BQ_DATASET_CORE", "kalshi_core")
BQ_DATASET_OPS = os.getenv("BQ_DATASET_OPS", "kalshi_ops")

QUALITY_MAX_FRESHNESS_MINUTES = float(os.getenv("QUALITY_MAX_FRESHNESS_MINUTES", "15"))
QUALITY_MAX_NULL_MARKET_TICKER_RATIO = float(os.getenv("QUALITY_MAX_NULL_MARKET_TICKER_RATIO", "0.01"))
QUALITY_MAX_DUPLICATE_TRADE_ID_RATIO = float(os.getenv("QUALITY_MAX_DUPLICATE_TRADE_ID_RATIO", "0.01"))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _require_bq_project() -> None:
    if not BQ_PROJECT or BQ_PROJECT == "YOUR_PROJECT_ID":
        raise ValueError("Set BQ_PROJECT to a valid GCP project ID before running this DAG.")
    if "." in BQ_PROJECT:
        raise ValueError(
            "BQ_PROJECT must be only the GCP project id (for example: brainrot-453319). "
            "Do not include dataset names in BQ_PROJECT."
        )


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


def _request_params_from_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    normalized: dict[str, Any] = {}
    for key, values in params.items():
        if len(values) == 1:
            normalized[key] = values[0]
        else:
            normalized[key] = values
    return normalized


def _json_cell(value: Any) -> str:
    if value is None:
        return "null"
    return json.dumps(value, separators=(",", ":"), default=str)


def _maybe_timestamp_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return datetime.fromtimestamp(float(text), tz=timezone.utc).isoformat()
        return text
    return None


def _api_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urlencode(params)
    url = f"{KALSHI_API_BASE_URL}{path}"
    if query:
        url = f"{url}?{query}"

    req = Request(url, method="GET")

    try:
        with urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
            headers = dict(resp.headers.items())
            status = int(getattr(resp, "status", 200))
    except HTTPError as err:
        raw = err.read().decode("utf-8") if err.fp else ""
        body = json.loads(raw) if raw else {}
        headers = dict(err.headers.items()) if err.headers else {}
        status = int(err.code)
    except URLError as err:
        body = {"error": {"code": "network_error", "message": str(err)}}
        headers = {}
        status = 599
    except Exception as err:  # pragma: no cover
        body = {"error": {"code": "unexpected_error", "message": str(err)}}
        headers = {}
        status = 599

    return {
        "request_url": url,
        "response_json": body,
        "response_headers": headers,
        "response_status": status,
        "fetched_at": _utcnow_iso(),
    }


def _rate_limited_get(path: str, params: dict[str, Any], read_rps: int, state: dict[str, float]) -> dict[str, Any]:
    min_interval = 1.0 / max(1, read_rps)
    elapsed = time.monotonic() - state["last_call"]
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    payload = _api_get(path, params)
    retryable_statuses = {429, 500, 502, 503, 504, 599}
    retries = 0
    while payload["response_status"] in retryable_statuses and retries < 2:
        time.sleep(max(1, KALSHI_429_RETRY_DELAY_SECONDS * (2**retries)))
        payload = _api_get(path, params)
        retries += 1
    state["last_call"] = time.monotonic()
    return payload


def _extract_items(response_json: Any, primary_key: str) -> list[dict[str, Any]]:
    if isinstance(response_json, dict):
        items = response_json.get(primary_key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    if isinstance(response_json, list):
        return [item for item in response_json if isinstance(item, dict)]
    return []


def _paginate(
    path: str,
    base_params: dict[str, Any],
    item_key: str,
    read_rps: int,
    max_pages: int,
    request_budget: int,
) -> dict[str, Any]:
    state = {"last_call": 0.0}
    params = dict(base_params)

    pages = 0
    requests_used = 0
    all_items: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    while pages < max_pages and requests_used < request_budget:
        response = _rate_limited_get(path, params, read_rps, state)
        requests_used += 1
        pages += 1

        response_json = response["response_json"]
        items = _extract_items(response_json, item_key)

        cursor = ""
        if isinstance(response_json, dict):
            cursor = str(response_json.get("cursor") or "")

        call_id = str(uuid.uuid4())
        calls.append(
            {
                "call_id": call_id,
                "endpoint": path,
                "request_url": response["request_url"],
                "response_status": response["response_status"],
                "response_headers": response["response_headers"],
                "response_cursor": cursor,
                "fetched_at": response["fetched_at"],
            }
        )
        for item in items:
            enriched = dict(item)
            enriched["_source_call_id"] = call_id
            enriched["_source_cursor"] = cursor
            enriched["_fetched_at"] = response["fetched_at"]
            all_items.append(enriched)

        if response["response_status"] >= 400:
            break
        if not cursor:
            break

        params["cursor"] = cursor

    return {
        "items": all_items,
        "calls": calls,
        "pages": pages,
        "requests_used": requests_used,
    }


with DAG(
    dag_id="kalshi_market_data_autonomous_de_v0",
    description="Kalshi-first, rate-limit-aware ingestion with bounded autonomous DE governance",
    start_date=datetime(2026, 1, 1),
    schedule="*/5 * * * *",
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "data-platform"},
    tags=["kalshi", "autonomous", "bigquery", "v0", "rate-limit"],
) as dag:
    start = EmptyOperator(task_id="start")
    stop_noop = EmptyOperator(task_id="stop_noop")
    end = EmptyOperator(task_id="end", trigger_rule="none_failed_min_one_success")

    @task(task_id="plan_rate_limits")
    def plan_rate_limits() -> dict[str, Any]:
        effective_read_rps = max(1, min(KALSHI_CONFIGURED_READ_RPS, KALSHI_MAX_TIER_READ_RPS))

        # Reserve request budget across endpoint groups including event metadata.
        remaining_budget = max(1, KALSHI_MAX_REQUESTS_PER_RUN)
        event_budget = min(KALSHI_MAX_EVENT_PAGES, max(5, remaining_budget // 2))
        remaining_budget = max(0, remaining_budget - event_budget)
        market_budget = min(KALSHI_MAX_MARKET_PAGES, max(1, remaining_budget // 2))
        remaining_budget = max(0, remaining_budget - market_budget)
        trade_budget = min(KALSHI_MAX_TRADE_PAGES, max(1, remaining_budget // 2))
        remaining_budget = max(0, remaining_budget - trade_budget)
        orderbook_budget = min(
            KALSHI_MAX_ORDERBOOK_MARKETS,
            remaining_budget,
        )

        return {
            "run_id": str(uuid.uuid4()),
            "decided_at": _utcnow_iso(),
            "usage_tier": KALSHI_USAGE_TIER,
            "configured_read_rps": KALSHI_CONFIGURED_READ_RPS,
            "effective_read_rps": effective_read_rps,
            "read_budget_per_run": KALSHI_MAX_REQUESTS_PER_RUN,
            "max_event_pages": event_budget,
            "max_market_pages": market_budget,
            "max_trade_pages": trade_budget,
            "max_orderbooks": orderbook_budget,
            "should_run": KALSHI_MAX_REQUESTS_PER_RUN > 0,
            "decision_reason": f"Run enabled with tier-safe request budget (events={event_budget})",
        }

    @task(task_id="record_rate_limit_plan")
    def record_rate_limit_plan(plan: dict[str, Any]) -> dict[str, Any]:
        row = {
            "run_id": plan["run_id"],
            "decided_at": plan["decided_at"],
            "usage_tier": plan.get("usage_tier"),
            "configured_read_rps": int(plan["configured_read_rps"]),
            "effective_read_rps": int(plan["effective_read_rps"]),
            "read_budget_per_run": int(plan["read_budget_per_run"]),
            "max_market_pages": int(plan["max_market_pages"]),
            "max_trade_pages": int(plan["max_trade_pages"]),
            "max_orderbooks": int(plan["max_orderbooks"]),
            "decision_reason": plan["decision_reason"],
        }
        _insert_rows(BQ_DATASET_OPS, "rate_limit_log", [row])
        return plan

    def _branch_rate_gate(ti, **_: Any) -> str | list[str]:
        plan = ti.xcom_pull(task_ids="plan_rate_limits")
        if not plan.get("should_run"):
            return "stop_noop"
        return ["fetch_markets", "fetch_recent_trades"]

    rate_gate = BranchPythonOperator(
        task_id="rate_gate",
        python_callable=_branch_rate_gate,
    )

    @task(task_id="fetch_markets")
    def fetch_markets(plan: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "status": KALSHI_MARKET_STATUS,
            "limit": KALSHI_MARKET_PAGE_LIMIT,
        }
        if KALSHI_SERIES_TICKER:
            params["series_ticker"] = KALSHI_SERIES_TICKER

        return _paginate(
            path="/markets",
            base_params=params,
            item_key="markets",
            read_rps=plan["effective_read_rps"],
            max_pages=plan["max_market_pages"],
            request_budget=plan["read_budget_per_run"],
        )

    @task(task_id="fetch_events")
    def fetch_events(markets: dict[str, Any], trades: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        state = {"last_call": 0.0}
        calls: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []

        event_scores: dict[str, float] = {}
        for market in markets.get("items", []):
            ticker = str(market.get("event_ticker") or "")
            if not ticker:
                continue
            score = _to_float(market.get("volume_dollars") or market.get("volume"))
            if ticker not in event_scores or score > event_scores[ticker]:
                event_scores[ticker] = score

        for trade in trades.get("items", []):
            market_ticker = str(trade.get("market_ticker") or trade.get("ticker") or "")
            if not market_ticker or "-" not in market_ticker:
                continue
            event_ticker = market_ticker.rsplit("-", 1)[0]
            score = _to_float(trade.get("count_contracts") or trade.get("count") or trade.get("quantity") or 1)
            event_scores[event_ticker] = event_scores.get(event_ticker, 0.0) + max(1.0, score)

        ranked_event_tickers = [
            ticker
            for ticker, _ in sorted(event_scores.items(), key=lambda kv: kv[1], reverse=True)
        ][: plan["max_event_pages"]]

        for event_ticker in ranked_event_tickers:
            response = _rate_limited_get(
                path=f"/events/{event_ticker}",
                params={},
                read_rps=plan["effective_read_rps"],
                state=state,
            )

            response_json = response["response_json"]
            event_payload = {}
            if isinstance(response_json, dict):
                event_payload = response_json.get("event") or response_json
            if not isinstance(event_payload, dict):
                event_payload = {}

            call_id = str(uuid.uuid4())
            calls.append(
                {
                    "call_id": call_id,
                    "endpoint": f"/events/{event_ticker}",
                    "request_url": response["request_url"],
                    "response_status": response["response_status"],
                    "response_headers": response["response_headers"],
                    "response_cursor": "",
                    "fetched_at": response["fetched_at"],
                }
            )

            enriched = dict(event_payload)
            enriched["_source_call_id"] = call_id
            enriched["_source_cursor"] = ""
            enriched["_fetched_at"] = response["fetched_at"]
            if "event_ticker" not in enriched:
                enriched["event_ticker"] = event_ticker
            items.append(enriched)

        return {
            "items": items,
            "calls": calls,
            "pages": len(calls),
            "requests_used": len(calls),
        }

    @task(task_id="fetch_recent_trades")
    def fetch_recent_trades(plan: dict[str, Any]) -> dict[str, Any]:
        min_ts = int((datetime.now(timezone.utc) - timedelta(minutes=KALSHI_TRADE_LOOKBACK_MINUTES)).timestamp())

        return _paginate(
            path="/markets/trades",
            base_params={
                "limit": KALSHI_TRADE_PAGE_LIMIT,
                "min_ts": min_ts,
            },
            item_key="trades",
            read_rps=plan["effective_read_rps"],
            max_pages=plan["max_trade_pages"],
            request_budget=plan["read_budget_per_run"],
        )

    @task(task_id="select_orderbook_markets")
    def select_orderbook_markets(markets: dict[str, Any], plan: dict[str, Any]) -> list[str]:
        market_items = markets.get("items", [])

        ranked = sorted(
            market_items,
            key=lambda item: (
                _to_float(item.get("volume_dollars") or item.get("volume")),
                _to_float(item.get("open_interest_dollars") or item.get("open_interest")),
            ),
            reverse=True,
        )

        tickers: list[str] = []
        for item in ranked:
            ticker = str(item.get("ticker") or "")
            if ticker:
                tickers.append(ticker)
            if len(tickers) >= plan["max_orderbooks"]:
                break

        return tickers

    @task(task_id="fetch_orderbooks")
    def fetch_orderbooks(tickers: list[str], plan: dict[str, Any]) -> dict[str, Any]:
        state = {"last_call": 0.0}
        calls: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []

        for ticker in tickers:
            response = _rate_limited_get(
                path=f"/markets/{ticker}/orderbook",
                params={"depth": KALSHI_ORDERBOOK_DEPTH},
                read_rps=plan["effective_read_rps"],
                state=state,
            )

            body = response["response_json"]
            call_id = str(uuid.uuid4())
            calls.append(
                {
                    "call_id": call_id,
                    "endpoint": f"/markets/{ticker}/orderbook",
                    "request_url": response["request_url"],
                    "response_status": response["response_status"],
                    "response_headers": response["response_headers"],
                    "response_cursor": "",
                    "fetched_at": response["fetched_at"],
                }
            )

            items.append(
                {
                    "_source_call_id": call_id,
                    "_source_cursor": "",
                    "market_ticker": ticker,
                    "payload": body,
                    "fetched_at": response["fetched_at"],
                    "status": response["response_status"],
                }
            )

        return {
            "calls": calls,
            "items": items,
            "requests_used": len(calls),
        }

    @task(task_id="persist_raw_payloads")
    def persist_raw_payloads(
        plan: dict[str, Any],
        events: dict[str, Any],
        markets: dict[str, Any],
        trades: dict[str, Any],
        orderbooks: dict[str, Any],
    ) -> dict[str, Any]:
        ingestion_date = datetime.now(timezone.utc).date().isoformat()
        event_calls = events.get("calls", [])
        market_calls = markets.get("calls", [])
        trade_calls = trades.get("calls", [])
        orderbook_calls = orderbooks.get("calls", [])
        all_calls = event_calls + market_calls + trade_calls + orderbook_calls

        api_call_rows: list[dict[str, Any]] = []
        for call in all_calls:
            api_call_rows.append(
                {
                    "call_id": call["call_id"],
                    "endpoint": call["endpoint"],
                    "request_url": call["request_url"],
                    "request_params": _json_cell(_request_params_from_url(call["request_url"])),
                    "response_status": int(call["response_status"]),
                    "response_headers": _json_cell(call.get("response_headers") or {}),
                    "response_cursor": call.get("response_cursor") or "",
                    "fetched_at": call["fetched_at"],
                    "ingestion_date": ingestion_date,
                }
            )
        _insert_rows(BQ_DATASET_RAW, "api_call_log", api_call_rows)

        event_rows: list[dict[str, Any]] = []
        for item in events.get("items", []):
            payload = dict(item)
            call_id = str(payload.pop("_source_call_id", ""))
            cursor = str(payload.pop("_source_cursor", ""))
            fetched_at = payload.pop("_fetched_at", None) or _utcnow_iso()
            event_rows.append(
                {
                    "call_id": call_id,
                    "cursor": cursor,
                    "event_ticker": payload.get("event_ticker"),
                    "series_ticker": payload.get("series_ticker"),
                    "title": payload.get("title") or payload.get("event_title"),
                    "sub_title": payload.get("sub_title") or payload.get("subtitle"),
                    "status": payload.get("status"),
                    "last_updated_ts": _maybe_timestamp_str(payload.get("last_updated_ts") or payload.get("updated_time")),
                    "api_payload": _json_cell(payload),
                    "fetched_at": fetched_at,
                    "ingestion_date": ingestion_date,
                }
            )
        _insert_rows(BQ_DATASET_RAW, "events_raw", event_rows)

        market_rows: list[dict[str, Any]] = []
        for item in markets.get("items", []):
            payload = dict(item)
            call_id = str(payload.pop("_source_call_id", ""))
            cursor = str(payload.pop("_source_cursor", ""))
            fetched_at = payload.pop("_fetched_at", None) or _utcnow_iso()
            market_rows.append(
                {
                    "call_id": call_id,
                    "cursor": cursor,
                    "market_ticker": payload.get("ticker"),
                    "event_ticker": payload.get("event_ticker"),
                    "series_ticker": payload.get("series_ticker"),
                    "status": payload.get("status"),
                    "close_time": _maybe_timestamp_str(payload.get("close_time") or payload.get("close_date")),
                    "api_payload": _json_cell(payload),
                    "fetched_at": fetched_at,
                    "ingestion_date": ingestion_date,
                }
            )
        _insert_rows(BQ_DATASET_RAW, "markets_raw", market_rows)

        trade_rows: list[dict[str, Any]] = []
        for item in trades.get("items", []):
            payload = dict(item)
            call_id = str(payload.pop("_source_call_id", ""))
            cursor = str(payload.pop("_source_cursor", ""))
            fetched_at = payload.pop("_fetched_at", None) or _utcnow_iso()
            trade_rows.append(
                {
                    "call_id": call_id,
                    "cursor": cursor,
                    "trade_id": payload.get("trade_id") or payload.get("id"),
                    "market_ticker": payload.get("market_ticker"),
                    "created_time": _maybe_timestamp_str(
                        payload.get("created_time")
                        or payload.get("created_ts")
                        or payload.get("created_at")
                    ),
                    "api_payload": _json_cell(payload),
                    "fetched_at": fetched_at,
                    "ingestion_date": ingestion_date,
                }
            )
        _insert_rows(BQ_DATASET_RAW, "trades_raw", trade_rows)

        orderbook_rows: list[dict[str, Any]] = []
        for item in orderbooks.get("items", []):
            payload = item.get("payload", {})
            orderbook_rows.append(
                {
                    "call_id": item.get("_source_call_id"),
                    "market_ticker": item.get("market_ticker"),
                    "api_payload": _json_cell(payload if isinstance(payload, dict) else {}),
                    "fetched_at": item.get("fetched_at") or _utcnow_iso(),
                    "ingestion_date": ingestion_date,
                }
            )
        _insert_rows(BQ_DATASET_RAW, "orderbooks_raw", orderbook_rows)

        return {
            "run_id": plan["run_id"],
            "event_calls": len(event_calls),
            "market_calls": len(market_calls),
            "trade_calls": len(trade_calls),
            "orderbook_calls": len(orderbook_calls),
            "event_call_ids": [call["call_id"] for call in event_calls],
            "market_call_ids": [call["call_id"] for call in market_calls],
            "trade_call_ids": [call["call_id"] for call in trade_calls],
            "orderbook_call_ids": [call["call_id"] for call in orderbook_calls],
            "persisted_at": _utcnow_iso(),
        }

    @task(task_id="build_staging")
    def build_staging(raw_summary: dict[str, Any]) -> dict[str, Any]:
        from google.cloud import bigquery

        event_call_ids = raw_summary.get("event_call_ids", [])
        market_call_ids = raw_summary.get("market_call_ids", [])
        trade_call_ids = raw_summary.get("trade_call_ids", [])
        orderbook_call_ids = raw_summary.get("orderbook_call_ids", [])

        event_rows_inserted = 0
        market_rows_inserted = 0
        trade_rows_inserted = 0
        orderbook_rows_inserted = 0

        if event_call_ids:
            events_sql = f"""
            INSERT INTO `{_table_ref(BQ_DATASET_STG, "events_stg")}` (
              event_ticker, series_ticker, title, sub_title,
              status, last_updated_ts, snapshot_ts, source_call_id, ingestion_date
            )
            SELECT
              COALESCE(NULLIF(event_ticker, ''), JSON_VALUE(api_payload, '$.event_ticker')) AS event_ticker,
              COALESCE(NULLIF(series_ticker, ''), JSON_VALUE(api_payload, '$.series_ticker')) AS series_ticker,
              COALESCE(NULLIF(title, ''), JSON_VALUE(api_payload, '$.title'), JSON_VALUE(api_payload, '$.event_title')) AS title,
              COALESCE(NULLIF(sub_title, ''), JSON_VALUE(api_payload, '$.sub_title'), JSON_VALUE(api_payload, '$.subtitle')) AS sub_title,
              COALESCE(NULLIF(status, ''), JSON_VALUE(api_payload, '$.status')) AS status,
              COALESCE(last_updated_ts, SAFE_CAST(JSON_VALUE(api_payload, '$.last_updated_ts') AS TIMESTAMP)) AS last_updated_ts,
              fetched_at AS snapshot_ts,
              call_id AS source_call_id,
              ingestion_date
            FROM `{_table_ref(BQ_DATASET_RAW, "events_raw")}`
            WHERE call_id IN UNNEST(@call_ids)
              AND COALESCE(NULLIF(event_ticker, ''), JSON_VALUE(api_payload, '$.event_ticker')) IS NOT NULL
            """
            job = _run_query(
                events_sql,
                [bigquery.ArrayQueryParameter("call_ids", "STRING", event_call_ids)],
            )
            event_rows_inserted = int(job.num_dml_affected_rows or 0)

        if market_call_ids:
            market_sql = f"""
            INSERT INTO `{_table_ref(BQ_DATASET_STG, "market_snapshots_stg")}` (
              market_ticker, event_ticker, series_ticker, status,
              yes_bid_dollars, yes_ask_dollars, no_bid_dollars, no_ask_dollars,
              last_price_dollars, volume_dollars, open_interest_dollars,
              close_time, snapshot_ts, source_call_id, ingestion_date
            )
            SELECT
              COALESCE(NULLIF(market_ticker, ''), JSON_VALUE(api_payload, '$.ticker')) AS market_ticker,
              COALESCE(NULLIF(event_ticker, ''), JSON_VALUE(api_payload, '$.event_ticker')) AS event_ticker,
              COALESCE(NULLIF(series_ticker, ''), JSON_VALUE(api_payload, '$.series_ticker')) AS series_ticker,
              COALESCE(NULLIF(status, ''), JSON_VALUE(api_payload, '$.status')) AS status,
              SAFE_CAST(COALESCE(JSON_VALUE(api_payload, '$.yes_bid_dollars'), JSON_VALUE(api_payload, '$.yes_bid')) AS NUMERIC) AS yes_bid_dollars,
              SAFE_CAST(COALESCE(JSON_VALUE(api_payload, '$.yes_ask_dollars'), JSON_VALUE(api_payload, '$.yes_ask')) AS NUMERIC) AS yes_ask_dollars,
              SAFE_CAST(COALESCE(JSON_VALUE(api_payload, '$.no_bid_dollars'), JSON_VALUE(api_payload, '$.no_bid')) AS NUMERIC) AS no_bid_dollars,
              SAFE_CAST(COALESCE(JSON_VALUE(api_payload, '$.no_ask_dollars'), JSON_VALUE(api_payload, '$.no_ask')) AS NUMERIC) AS no_ask_dollars,
              SAFE_CAST(COALESCE(JSON_VALUE(api_payload, '$.last_price_dollars'), JSON_VALUE(api_payload, '$.last_price')) AS NUMERIC) AS last_price_dollars,
              SAFE_CAST(COALESCE(JSON_VALUE(api_payload, '$.volume_dollars'), JSON_VALUE(api_payload, '$.volume')) AS NUMERIC) AS volume_dollars,
              SAFE_CAST(COALESCE(JSON_VALUE(api_payload, '$.open_interest_dollars'), JSON_VALUE(api_payload, '$.open_interest')) AS NUMERIC) AS open_interest_dollars,
              COALESCE(close_time, SAFE_CAST(JSON_VALUE(api_payload, '$.close_time') AS TIMESTAMP), SAFE_CAST(JSON_VALUE(api_payload, '$.close_date') AS TIMESTAMP)) AS close_time,
              fetched_at AS snapshot_ts,
              call_id AS source_call_id,
              ingestion_date
            FROM `{_table_ref(BQ_DATASET_RAW, "markets_raw")}`
            WHERE call_id IN UNNEST(@call_ids)
              AND COALESCE(NULLIF(market_ticker, ''), JSON_VALUE(api_payload, '$.ticker')) IS NOT NULL
            """
            job = _run_query(
                market_sql,
                [bigquery.ArrayQueryParameter("call_ids", "STRING", market_call_ids)],
            )
            market_rows_inserted = int(job.num_dml_affected_rows or 0)

        if trade_call_ids:
            trades_sql = f"""
            INSERT INTO `{_table_ref(BQ_DATASET_STG, "trades_stg")}` (
              trade_id, market_ticker, yes_price_dollars, no_price_dollars,
              count_contracts, taker_side, created_time, snapshot_ts, source_call_id, ingestion_date
            )
            SELECT
              COALESCE(NULLIF(trade_id, ''), JSON_VALUE(api_payload, '$.trade_id'), JSON_VALUE(api_payload, '$.id')) AS trade_id,
              COALESCE(NULLIF(market_ticker, ''), JSON_VALUE(api_payload, '$.market_ticker'), JSON_VALUE(api_payload, '$.ticker')) AS market_ticker,
              SAFE_CAST(COALESCE(JSON_VALUE(api_payload, '$.yes_price_dollars'), JSON_VALUE(api_payload, '$.yes_price')) AS NUMERIC) AS yes_price_dollars,
              SAFE_CAST(COALESCE(JSON_VALUE(api_payload, '$.no_price_dollars'), JSON_VALUE(api_payload, '$.no_price')) AS NUMERIC) AS no_price_dollars,
              SAFE_CAST(COALESCE(JSON_VALUE(api_payload, '$.count_contracts'), JSON_VALUE(api_payload, '$.count'), JSON_VALUE(api_payload, '$.quantity')) AS NUMERIC) AS count_contracts,
              COALESCE(JSON_VALUE(api_payload, '$.taker_side'), JSON_VALUE(api_payload, '$.side')) AS taker_side,
              COALESCE(created_time, SAFE_CAST(JSON_VALUE(api_payload, '$.created_time') AS TIMESTAMP), TIMESTAMP_SECONDS(SAFE_CAST(JSON_VALUE(api_payload, '$.created_ts') AS INT64))) AS created_time,
              fetched_at AS snapshot_ts,
              call_id AS source_call_id,
              ingestion_date
            FROM `{_table_ref(BQ_DATASET_RAW, "trades_raw")}`
            WHERE call_id IN UNNEST(@call_ids)
              AND COALESCE(NULLIF(trade_id, ''), JSON_VALUE(api_payload, '$.trade_id'), JSON_VALUE(api_payload, '$.id')) IS NOT NULL
              AND COALESCE(NULLIF(market_ticker, ''), JSON_VALUE(api_payload, '$.market_ticker'), JSON_VALUE(api_payload, '$.ticker')) IS NOT NULL
            """
            job = _run_query(
                trades_sql,
                [bigquery.ArrayQueryParameter("call_ids", "STRING", trade_call_ids)],
            )
            trade_rows_inserted = int(job.num_dml_affected_rows or 0)

        if orderbook_call_ids:
            orderbooks_sql = f"""
            INSERT INTO `{_table_ref(BQ_DATASET_STG, "orderbook_levels_stg")}` (
              market_ticker, side, price_dollars, quantity_contracts,
              level_rank, snapshot_ts, source_call_id, ingestion_date
            )
            WITH src AS (
              SELECT call_id, market_ticker, api_payload, fetched_at, ingestion_date
              FROM `{_table_ref(BQ_DATASET_RAW, "orderbooks_raw")}`
              WHERE call_id IN UNNEST(@call_ids)
            ),
            yes_levels AS (
              SELECT
                market_ticker,
                'yes' AS side,
                SAFE_CAST(COALESCE(JSON_VALUE(level, '$.price_dollars'), JSON_VALUE(level, '$.price'), JSON_VALUE(level, '$[0]')) AS NUMERIC) AS price_dollars,
                SAFE_CAST(COALESCE(JSON_VALUE(level, '$.quantity_contracts'), JSON_VALUE(level, '$.quantity'), JSON_VALUE(level, '$[1]')) AS NUMERIC) AS quantity_contracts,
                idx + 1 AS level_rank,
                fetched_at AS snapshot_ts,
                call_id AS source_call_id,
                ingestion_date
              FROM src, UNNEST(
                COALESCE(
                  JSON_QUERY_ARRAY(api_payload, '$.orderbook.yes_dollars'),
                  JSON_QUERY_ARRAY(api_payload, '$.orderbook.yes'),
                  JSON_QUERY_ARRAY(api_payload, '$.yes_dollars'),
                  JSON_QUERY_ARRAY(api_payload, '$.yes'),
                  JSON_QUERY_ARRAY(api_payload, '$.yes_levels'),
                  CAST([] AS ARRAY<JSON>)
                )
              ) AS level WITH OFFSET AS idx
            ),
            no_levels AS (
              SELECT
                market_ticker,
                'no' AS side,
                SAFE_CAST(COALESCE(JSON_VALUE(level, '$.price_dollars'), JSON_VALUE(level, '$.price'), JSON_VALUE(level, '$[0]')) AS NUMERIC) AS price_dollars,
                SAFE_CAST(COALESCE(JSON_VALUE(level, '$.quantity_contracts'), JSON_VALUE(level, '$.quantity'), JSON_VALUE(level, '$[1]')) AS NUMERIC) AS quantity_contracts,
                idx + 1 AS level_rank,
                fetched_at AS snapshot_ts,
                call_id AS source_call_id,
                ingestion_date
              FROM src, UNNEST(
                COALESCE(
                  JSON_QUERY_ARRAY(api_payload, '$.orderbook.no_dollars'),
                  JSON_QUERY_ARRAY(api_payload, '$.orderbook.no'),
                  JSON_QUERY_ARRAY(api_payload, '$.no_dollars'),
                  JSON_QUERY_ARRAY(api_payload, '$.no'),
                  JSON_QUERY_ARRAY(api_payload, '$.no_levels'),
                  CAST([] AS ARRAY<JSON>)
                )
              ) AS level WITH OFFSET AS idx
            )
            SELECT *
            FROM (
              SELECT * FROM yes_levels
              UNION ALL
              SELECT * FROM no_levels
            )
            WHERE market_ticker IS NOT NULL
            """
            job = _run_query(
                orderbooks_sql,
                [bigquery.ArrayQueryParameter("call_ids", "STRING", orderbook_call_ids)],
            )
            orderbook_rows_inserted = int(job.num_dml_affected_rows or 0)

        return {
            "run_id": raw_summary["run_id"],
            "event_call_ids": event_call_ids,
            "market_call_ids": market_call_ids,
            "trade_call_ids": trade_call_ids,
            "orderbook_call_ids": orderbook_call_ids,
            "staging_status": "ok",
            "event_rows_inserted": event_rows_inserted,
            "market_rows_inserted": market_rows_inserted,
            "trade_rows_inserted": trade_rows_inserted,
            "orderbook_rows_inserted": orderbook_rows_inserted,
            "built_at": _utcnow_iso(),
        }

    @task(task_id="run_quality_gates")
    def run_quality_gates(stg_summary: dict[str, Any]) -> dict[str, Any]:
        from google.cloud import bigquery

        market_call_ids = stg_summary.get("market_call_ids", [])
        trade_call_ids = stg_summary.get("trade_call_ids", [])
        run_id = stg_summary["run_id"]

        if not market_call_ids:
            checks = {
                "freshness_minutes": 1e9,
                "null_market_ticker_ratio": 1.0,
                "duplicate_trade_id_ratio": 1.0,
            }
        else:
            quality_sql = f"""
            WITH market_scope AS (
              SELECT * FROM `{_table_ref(BQ_DATASET_STG, "market_snapshots_stg")}`
              WHERE source_call_id IN UNNEST(@market_call_ids)
            ),
            trade_scope AS (
              SELECT * FROM `{_table_ref(BQ_DATASET_STG, "trades_stg")}`
              WHERE source_call_id IN UNNEST(@trade_call_ids)
            ),
            market_metrics AS (
              SELECT
                COUNT(*) AS total_rows,
                COUNTIF(market_ticker IS NULL OR market_ticker = '') AS null_rows,
                MAX(snapshot_ts) AS max_snapshot_ts
              FROM market_scope
            ),
            trade_metrics AS (
              SELECT
                COUNT(*) AS total_rows,
                COUNT(*) - COUNT(DISTINCT trade_id) AS duplicate_rows
              FROM trade_scope
            )
            SELECT
              IFNULL(TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), m.max_snapshot_ts, MINUTE), 1e9) AS freshness_minutes,
              IFNULL(SAFE_DIVIDE(m.null_rows, m.total_rows), 1.0) AS null_market_ticker_ratio,
              IFNULL(SAFE_DIVIDE(t.duplicate_rows, t.total_rows), 0.0) AS duplicate_trade_id_ratio
            FROM market_metrics AS m
            CROSS JOIN trade_metrics AS t
            """
            result = _run_query(
                quality_sql,
                [
                    bigquery.ArrayQueryParameter("market_call_ids", "STRING", market_call_ids),
                    bigquery.ArrayQueryParameter("trade_call_ids", "STRING", trade_call_ids),
                ],
            )
            row = next(iter(result.result()), None)
            checks = {
                "freshness_minutes": float(row["freshness_minutes"]) if row else 1e9,
                "null_market_ticker_ratio": float(row["null_market_ticker_ratio"]) if row else 1.0,
                "duplicate_trade_id_ratio": float(row["duplicate_trade_id_ratio"]) if row else 1.0,
            }

        status_by_check = {
            "freshness_minutes": "PASS" if checks["freshness_minutes"] <= QUALITY_MAX_FRESHNESS_MINUTES else "FAIL",
            "null_market_ticker_ratio": "PASS"
            if checks["null_market_ticker_ratio"] <= QUALITY_MAX_NULL_MARKET_TICKER_RATIO
            else "FAIL",
            "duplicate_trade_id_ratio": "PASS"
            if checks["duplicate_trade_id_ratio"] <= QUALITY_MAX_DUPLICATE_TRADE_ID_RATIO
            else "FAIL",
        }

        rows = [
            {
                "run_id": run_id,
                "check_name": "freshness_minutes",
                "status": status_by_check["freshness_minutes"],
                "metric_value": checks["freshness_minutes"],
                "threshold_value": QUALITY_MAX_FRESHNESS_MINUTES,
                "details": _json_cell({"market_call_count": len(market_call_ids)}),
                "checked_at": _utcnow_iso(),
            },
            {
                "run_id": run_id,
                "check_name": "null_market_ticker_ratio",
                "status": status_by_check["null_market_ticker_ratio"],
                "metric_value": checks["null_market_ticker_ratio"],
                "threshold_value": QUALITY_MAX_NULL_MARKET_TICKER_RATIO,
                "details": _json_cell({"market_call_count": len(market_call_ids)}),
                "checked_at": _utcnow_iso(),
            },
            {
                "run_id": run_id,
                "check_name": "duplicate_trade_id_ratio",
                "status": status_by_check["duplicate_trade_id_ratio"],
                "metric_value": checks["duplicate_trade_id_ratio"],
                "threshold_value": QUALITY_MAX_DUPLICATE_TRADE_ID_RATIO,
                "details": _json_cell({"trade_call_count": len(trade_call_ids)}),
                "checked_at": _utcnow_iso(),
            },
        ]
        _insert_rows(BQ_DATASET_OPS, "quality_results", rows)

        overall_status = "PASS" if all(status == "PASS" for status in status_by_check.values()) else "FAIL"
        return {"status": overall_status, "checks": checks, "run_id": run_id}

    def _branch_quality_gate(ti, **_: Any) -> str:
        quality = ti.xcom_pull(task_ids="run_quality_gates")
        return "propose_schema_changes" if quality.get("status") == "PASS" else "stop_noop"

    quality_gate = BranchPythonOperator(
        task_id="quality_gate",
        python_callable=_branch_quality_gate,
    )

    @task(task_id="propose_schema_changes")
    def propose_schema_changes() -> dict[str, Any]:
        # TODO: DE agent logic for additive schema/parser proposals.
        return {
            "proposal_id": str(uuid.uuid4()),
            "status": "NO_CHANGE",
            "risk_level": "low",
            "target": f"{BQ_PROJECT}.{BQ_DATASET_STG}.market_snapshots_stg",
            "proposed_at": _utcnow_iso(),
        }

    def _branch_policy_gate(ti, **_: Any) -> str:
        proposal = ti.xcom_pull(task_ids="propose_schema_changes")
        if proposal.get("status") == "NO_CHANGE":
            return "skip_schema_apply"
        risk = proposal.get("risk_level", "high").lower()
        return "apply_schema_change" if risk == "low" else "skip_schema_apply"

    policy_gate = BranchPythonOperator(
        task_id="policy_gate",
        python_callable=_branch_policy_gate,
    )

    @task(task_id="apply_schema_change")
    def apply_schema_change(_: dict[str, Any]) -> dict[str, Any]:
        # TODO: execute approved additive DDL in staging datasets only.
        return {"apply_status": "applied", "applied_at": _utcnow_iso()}

    skip_schema_apply = EmptyOperator(task_id="skip_schema_apply")

    @task(task_id="publish_core_tables", trigger_rule="none_failed_min_one_success")
    def publish_core_tables(stg_summary: dict[str, Any]) -> dict[str, Any]:
        from google.cloud import bigquery

        event_call_ids = stg_summary.get("event_call_ids", [])
        market_call_ids = stg_summary.get("market_call_ids", [])
        trade_call_ids = stg_summary.get("trade_call_ids", [])
        event_rows = 0
        market_rows = 0
        trade_rows = 0

        if event_call_ids:
            merge_events_sql = f"""
            MERGE `{_table_ref(BQ_DATASET_CORE, "events_dim")}` AS t
            USING (
              SELECT
                event_ticker,
                series_ticker,
                title,
                sub_title,
                status,
                last_updated_ts,
                snapshot_ts,
                ingestion_date
              FROM `{_table_ref(BQ_DATASET_STG, "events_stg")}`
              WHERE source_call_id IN UNNEST(@event_call_ids)
              QUALIFY ROW_NUMBER() OVER (
                PARTITION BY event_ticker
                ORDER BY snapshot_ts DESC, last_updated_ts DESC
              ) = 1
            ) AS s
            ON t.event_ticker = s.event_ticker
            WHEN MATCHED THEN UPDATE SET
              series_ticker = s.series_ticker,
              title = s.title,
              sub_title = s.sub_title,
              status = s.status,
              last_updated_ts = s.last_updated_ts,
              last_seen_snapshot_ts = s.snapshot_ts,
              is_latest = TRUE,
              updated_at = CURRENT_TIMESTAMP(),
              ingestion_date = s.ingestion_date
            WHEN NOT MATCHED THEN
              INSERT (
                event_ticker, series_ticker, title, sub_title, status,
                last_updated_ts, last_seen_snapshot_ts, is_latest, updated_at, ingestion_date
              )
              VALUES (
                s.event_ticker, s.series_ticker, s.title, s.sub_title, s.status,
                s.last_updated_ts, s.snapshot_ts, TRUE, CURRENT_TIMESTAMP(), s.ingestion_date
              )
            """
            event_job = _run_query(
                merge_events_sql,
                [bigquery.ArrayQueryParameter("event_call_ids", "STRING", event_call_ids)],
            )
            event_rows = int(event_job.num_dml_affected_rows or 0)

        if market_call_ids:
            insert_market_sql = f"""
            INSERT INTO `{_table_ref(BQ_DATASET_CORE, "market_state_core")}` (
              market_ticker, event_ticker, series_ticker, status,
              yes_bid_dollars, yes_ask_dollars, no_bid_dollars, no_ask_dollars,
              last_price_dollars, volume_dollars, open_interest_dollars,
              close_time, snapshot_ts, is_latest, ingestion_date
            )
            SELECT
              market_ticker, event_ticker, series_ticker, status,
              yes_bid_dollars, yes_ask_dollars, no_bid_dollars, no_ask_dollars,
              last_price_dollars, volume_dollars, open_interest_dollars,
              close_time, snapshot_ts, FALSE AS is_latest, ingestion_date
            FROM `{_table_ref(BQ_DATASET_STG, "market_snapshots_stg")}`
            WHERE source_call_id IN UNNEST(@market_call_ids)
            """
            market_job = _run_query(
                insert_market_sql,
                [bigquery.ArrayQueryParameter("market_call_ids", "STRING", market_call_ids)],
            )
            market_rows = int(market_job.num_dml_affected_rows or 0)

            mark_latest_sql = f"""
            UPDATE `{_table_ref(BQ_DATASET_CORE, "market_state_core")}` AS c
            SET is_latest = (c.snapshot_ts = latest.max_snapshot_ts)
            FROM (
              SELECT market_ticker, MAX(snapshot_ts) AS max_snapshot_ts
              FROM `{_table_ref(BQ_DATASET_CORE, "market_state_core")}`
              WHERE market_ticker IN (
                SELECT DISTINCT market_ticker
                FROM `{_table_ref(BQ_DATASET_STG, "market_snapshots_stg")}`
                WHERE source_call_id IN UNNEST(@market_call_ids)
              )
              GROUP BY market_ticker
            ) AS latest
            WHERE c.market_ticker = latest.market_ticker
            """
            _run_query(
                mark_latest_sql,
                [bigquery.ArrayQueryParameter("market_call_ids", "STRING", market_call_ids)],
            )

        if trade_call_ids:
            merge_trades_sql = f"""
            MERGE `{_table_ref(BQ_DATASET_CORE, "trade_prints_core")}` AS t
            USING (
              SELECT
                trade_id, market_ticker, yes_price_dollars, no_price_dollars,
                count_contracts, taker_side, created_time, snapshot_ts, ingestion_date
              FROM `{_table_ref(BQ_DATASET_STG, "trades_stg")}`
              WHERE source_call_id IN UNNEST(@trade_call_ids)
              QUALIFY ROW_NUMBER() OVER (PARTITION BY trade_id ORDER BY snapshot_ts DESC) = 1
            ) AS s
            ON t.trade_id = s.trade_id
            WHEN MATCHED THEN UPDATE SET
              market_ticker = s.market_ticker,
              yes_price_dollars = s.yes_price_dollars,
              no_price_dollars = s.no_price_dollars,
              count_contracts = s.count_contracts,
              taker_side = s.taker_side,
              created_time = s.created_time,
              snapshot_ts = s.snapshot_ts,
              ingestion_date = s.ingestion_date
            WHEN NOT MATCHED THEN
              INSERT (
                trade_id, market_ticker, yes_price_dollars, no_price_dollars,
                count_contracts, taker_side, created_time, snapshot_ts, ingestion_date
              )
              VALUES (
                s.trade_id, s.market_ticker, s.yes_price_dollars, s.no_price_dollars,
                s.count_contracts, s.taker_side, s.created_time, s.snapshot_ts, s.ingestion_date
              )
            """
            trade_job = _run_query(
                merge_trades_sql,
                [bigquery.ArrayQueryParameter("trade_call_ids", "STRING", trade_call_ids)],
            )
            trade_rows = int(trade_job.num_dml_affected_rows or 0)

        return {
            "publish_status": "ok",
            "run_id": stg_summary["run_id"],
            "event_rows": event_rows,
            "market_rows": market_rows,
            "trade_rows": trade_rows,
            "published_at": _utcnow_iso(),
        }

    @task(task_id="compute_kpis")
    def compute_kpis(publish_summary: dict[str, Any]) -> dict[str, Any]:
        from google.cloud import bigquery

        kpi_sql = f"""
        MERGE `{_table_ref(BQ_DATASET_CORE, "kpi_5m")}` AS t
        USING (
          WITH bucket AS (
            SELECT TIMESTAMP_SECONDS(300 * DIV(UNIX_SECONDS(CURRENT_TIMESTAMP()), 300)) AS kpi_ts
          ),
          latest_market AS (
            SELECT market_ticker, series_ticker, status, volume_dollars, open_interest_dollars
            FROM `{_table_ref(BQ_DATASET_CORE, "market_state_core")}`
            WHERE is_latest = TRUE
          ),
          trade_lookback AS (
            SELECT market_ticker, COUNT(*) AS trade_count_lookback
            FROM `{_table_ref(BQ_DATASET_CORE, "trade_prints_core")}`
            WHERE created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @lookback_minutes MINUTE)
            GROUP BY market_ticker
          )
          SELECT
            b.kpi_ts,
            m.series_ticker,
            COUNTIF(LOWER(m.status) IN ('open', 'active')) AS open_market_count,
            SUM(COALESCE(m.volume_dollars, 0)) AS total_volume_dollars,
            SUM(COALESCE(m.open_interest_dollars, 0)) AS total_open_interest_dollars,
            SUM(COALESCE(tl.trade_count_lookback, 0)) AS trade_count_lookback,
            CURRENT_TIMESTAMP() AS updated_at
          FROM latest_market AS m
          CROSS JOIN bucket AS b
          LEFT JOIN trade_lookback AS tl USING (market_ticker)
          GROUP BY b.kpi_ts, m.series_ticker
        ) AS s
        ON t.kpi_ts = s.kpi_ts AND IFNULL(t.series_ticker, '') = IFNULL(s.series_ticker, '')
        WHEN MATCHED THEN UPDATE SET
          open_market_count = s.open_market_count,
          total_volume_dollars = s.total_volume_dollars,
          total_open_interest_dollars = s.total_open_interest_dollars,
          trade_count_lookback = s.trade_count_lookback,
          updated_at = s.updated_at
        WHEN NOT MATCHED THEN INSERT (
          kpi_ts,
          series_ticker,
          open_market_count,
          total_volume_dollars,
          total_open_interest_dollars,
          trade_count_lookback,
          updated_at
        )
        VALUES (
          s.kpi_ts,
          s.series_ticker,
          s.open_market_count,
          s.total_volume_dollars,
          s.total_open_interest_dollars,
          s.trade_count_lookback,
          s.updated_at
        )
        """
        job = _run_query(
            kpi_sql,
            [bigquery.ScalarQueryParameter("lookback_minutes", "INT64", KALSHI_TRADE_LOOKBACK_MINUTES)],
        )
        return {
            "kpi_status": "ok",
            "run_id": publish_summary.get("run_id"),
            "rows_affected": int(job.num_dml_affected_rows or 0),
            "computed_at": _utcnow_iso(),
        }

    @task(task_id="backfill_event_titles")
    def backfill_event_titles(plan: dict[str, Any], publish_summary: dict[str, Any]) -> dict[str, Any]:
        from google.cloud import bigquery

        if KALSHI_EVENT_BACKFILL_MAX_EVENTS <= 0:
            return {
                "run_id": publish_summary.get("run_id"),
                "status": "SKIPPED",
                "reason": "KALSHI_EVENT_BACKFILL_MAX_EVENTS <= 0",
                "hydrated_events": 0,
            }

        candidate_sql = f"""
        WITH trade_scope AS (
          SELECT
            market_ticker,
            SUM(COALESCE(count_contracts, 0)) AS contracts
          FROM `{_table_ref(BQ_DATASET_CORE, "trade_prints_core")}`
          WHERE created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @lookback_hours HOUR)
          GROUP BY market_ticker
        ),
        event_scope AS (
          SELECT
            REGEXP_EXTRACT(market_ticker, r'^(.*)-[^-]+$') AS event_ticker,
            SUM(contracts) AS contracts
          FROM trade_scope
          GROUP BY event_ticker
        ),
        latest_dim AS (
          SELECT event_ticker, title
          FROM `{_table_ref(BQ_DATASET_CORE, "events_dim")}`
          WHERE is_latest = TRUE
        )
        SELECT e.event_ticker
        FROM event_scope AS e
        LEFT JOIN latest_dim AS d USING (event_ticker)
        WHERE e.event_ticker IS NOT NULL
          AND (d.event_ticker IS NULL OR d.title IS NULL OR STARTS_WITH(d.title, 'KX'))
        ORDER BY e.contracts DESC
        LIMIT @max_events
        """
        candidate_job = _run_query(
            candidate_sql,
            [
                bigquery.ScalarQueryParameter("lookback_hours", "INT64", KALSHI_EVENT_BACKFILL_LOOKBACK_HOURS),
                bigquery.ScalarQueryParameter("max_events", "INT64", KALSHI_EVENT_BACKFILL_MAX_EVENTS),
            ],
        )
        candidates = [str(row["event_ticker"]) for row in candidate_job.result() if row["event_ticker"]]
        if not candidates:
            return {
                "run_id": publish_summary.get("run_id"),
                "status": "NOOP",
                "hydrated_events": 0,
                "candidate_events": 0,
            }

        state = {"last_call": 0.0}
        read_rps = max(1, min(int(plan.get("effective_read_rps", 1)), 5))
        ingestion_date = datetime.now(timezone.utc).date().isoformat()
        api_call_rows: list[dict[str, Any]] = []
        event_rows: list[dict[str, Any]] = []

        for event_ticker in candidates:
            response = _rate_limited_get(
                path=f"/events/{event_ticker}",
                params={},
                read_rps=read_rps,
                state=state,
            )

            call_id = str(uuid.uuid4())
            api_call_rows.append(
                {
                    "call_id": call_id,
                    "endpoint": f"/events/{event_ticker}",
                    "request_url": response["request_url"],
                    "request_params": _json_cell(_request_params_from_url(response["request_url"])),
                    "response_status": int(response["response_status"]),
                    "response_headers": _json_cell(response.get("response_headers") or {}),
                    "response_cursor": "",
                    "fetched_at": response["fetched_at"],
                    "ingestion_date": ingestion_date,
                }
            )

            if int(response["response_status"]) >= 400:
                continue
            response_json = response.get("response_json")
            payload = response_json.get("event") if isinstance(response_json, dict) else {}
            if not isinstance(payload, dict) or not payload:
                continue

            event_rows.append(
                {
                    "call_id": call_id,
                    "cursor": "",
                    "event_ticker": payload.get("event_ticker") or event_ticker,
                    "series_ticker": payload.get("series_ticker"),
                    "title": payload.get("title") or payload.get("event_title"),
                    "sub_title": payload.get("sub_title") or payload.get("subtitle"),
                    "status": payload.get("status"),
                    "last_updated_ts": _maybe_timestamp_str(payload.get("last_updated_ts") or payload.get("updated_time")),
                    "api_payload": _json_cell(payload),
                    "fetched_at": response["fetched_at"],
                    "ingestion_date": ingestion_date,
                }
            )

        if api_call_rows:
            _insert_rows(BQ_DATASET_RAW, "api_call_log", api_call_rows)
        if not event_rows:
            return {
                "run_id": publish_summary.get("run_id"),
                "status": "NOOP",
                "hydrated_events": 0,
                "candidate_events": len(candidates),
                "api_calls_logged": len(api_call_rows),
            }

        _insert_rows(BQ_DATASET_RAW, "events_raw", event_rows)
        event_call_ids = [row["call_id"] for row in event_rows]

        events_sql = f"""
        INSERT INTO `{_table_ref(BQ_DATASET_STG, "events_stg")}` (
          event_ticker, series_ticker, title, sub_title,
          status, last_updated_ts, snapshot_ts, source_call_id, ingestion_date
        )
        SELECT
          COALESCE(NULLIF(event_ticker, ''), JSON_VALUE(api_payload, '$.event_ticker')) AS event_ticker,
          COALESCE(NULLIF(series_ticker, ''), JSON_VALUE(api_payload, '$.series_ticker')) AS series_ticker,
          COALESCE(NULLIF(title, ''), JSON_VALUE(api_payload, '$.title'), JSON_VALUE(api_payload, '$.event_title')) AS title,
          COALESCE(NULLIF(sub_title, ''), JSON_VALUE(api_payload, '$.sub_title'), JSON_VALUE(api_payload, '$.subtitle')) AS sub_title,
          COALESCE(NULLIF(status, ''), JSON_VALUE(api_payload, '$.status')) AS status,
          COALESCE(last_updated_ts, SAFE_CAST(JSON_VALUE(api_payload, '$.last_updated_ts') AS TIMESTAMP)) AS last_updated_ts,
          fetched_at AS snapshot_ts,
          call_id AS source_call_id,
          ingestion_date
        FROM `{_table_ref(BQ_DATASET_RAW, "events_raw")}`
        WHERE call_id IN UNNEST(@call_ids)
          AND COALESCE(NULLIF(event_ticker, ''), JSON_VALUE(api_payload, '$.event_ticker')) IS NOT NULL
        """
        stg_job = _run_query(
            events_sql,
            [bigquery.ArrayQueryParameter("call_ids", "STRING", event_call_ids)],
        )
        stg_rows = int(stg_job.num_dml_affected_rows or 0)

        merge_events_sql = f"""
        MERGE `{_table_ref(BQ_DATASET_CORE, "events_dim")}` AS t
        USING (
          SELECT
            event_ticker,
            series_ticker,
            title,
            sub_title,
            status,
            last_updated_ts,
            snapshot_ts,
            ingestion_date
          FROM `{_table_ref(BQ_DATASET_STG, "events_stg")}`
          WHERE source_call_id IN UNNEST(@event_call_ids)
          QUALIFY ROW_NUMBER() OVER (
            PARTITION BY event_ticker
            ORDER BY snapshot_ts DESC, last_updated_ts DESC
          ) = 1
        ) AS s
        ON t.event_ticker = s.event_ticker
        WHEN MATCHED THEN UPDATE SET
          series_ticker = s.series_ticker,
          title = s.title,
          sub_title = s.sub_title,
          status = s.status,
          last_updated_ts = s.last_updated_ts,
          last_seen_snapshot_ts = s.snapshot_ts,
          is_latest = TRUE,
          updated_at = CURRENT_TIMESTAMP(),
          ingestion_date = s.ingestion_date
        WHEN NOT MATCHED THEN
          INSERT (
            event_ticker, series_ticker, title, sub_title, status,
            last_updated_ts, last_seen_snapshot_ts, is_latest, updated_at, ingestion_date
          )
          VALUES (
            s.event_ticker, s.series_ticker, s.title, s.sub_title, s.status,
            s.last_updated_ts, s.snapshot_ts, TRUE, CURRENT_TIMESTAMP(), s.ingestion_date
          )
        """
        merge_job = _run_query(
            merge_events_sql,
            [bigquery.ArrayQueryParameter("event_call_ids", "STRING", event_call_ids)],
        )

        return {
            "run_id": publish_summary.get("run_id"),
            "status": "OK",
            "candidate_events": len(candidates),
            "hydrated_events": len(event_rows),
            "stg_rows": stg_rows,
            "core_rows_affected": int(merge_job.num_dml_affected_rows or 0),
            "api_calls_logged": len(api_call_rows),
            "backfilled_at": _utcnow_iso(),
        }

    @task(task_id="emit_run_summary")
    def emit_run_summary(kpi: dict[str, Any], backfill: dict[str, Any], plan: dict[str, Any]) -> None:
        # TODO: push to monitoring/alerting destination.
        print({"run_status": "success", "kpi": kpi, "event_backfill": backfill, "rate_plan": plan})

    plan = plan_rate_limits()
    plan_logged = record_rate_limit_plan(plan)

    markets = fetch_markets(plan)
    trades = fetch_recent_trades(plan)
    events = fetch_events(markets, trades, plan)
    orderbook_tickers = select_orderbook_markets(markets, plan)
    orderbooks = fetch_orderbooks(orderbook_tickers, plan)

    raw = persist_raw_payloads(plan, events, markets, trades, orderbooks)
    stg = build_staging(raw)
    quality = run_quality_gates(stg)
    proposal = propose_schema_changes()
    applied = apply_schema_change(proposal)
    published = publish_core_tables(stg)
    backfill = backfill_event_titles(plan, published)
    kpi = compute_kpis(published)
    summary = emit_run_summary(kpi, backfill, plan)

    start >> plan >> plan_logged >> rate_gate
    rate_gate >> stop_noop >> end
    rate_gate >> [markets, trades]
    markets >> orderbook_tickers >> orderbooks
    [events, markets, trades, orderbooks] >> raw >> stg >> quality >> quality_gate
    quality_gate >> stop_noop
    quality_gate >> proposal >> policy_gate
    policy_gate >> skip_schema_apply >> published
    policy_gate >> applied >> published
    published >> backfill >> kpi >> summary >> end
