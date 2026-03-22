"""Airflow DAG for Kalshi WebSocket real-time market data ingestion.

Connects to the Kalshi authenticated WebSocket API, subscribes to ticker,
trade, and orderbook_delta channels for the top-N markets (by volume), and
streams messages for a configurable listen window (default 4 minutes).

Runs every 5 minutes with max_active_runs=1.  Complements the existing REST
polling DAG by providing sub-second trade and price data.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from airflow import DAG
from airflow.decorators import task
from airflow.operators.empty import EmptyOperator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BQ_PROJECT = os.getenv("BQ_PROJECT", "YOUR_PROJECT_ID")
BQ_DATASET_RAW = os.getenv("BQ_DATASET_RAW", "kalshi_raw")
BQ_DATASET_STG = os.getenv("BQ_DATASET_STG", "kalshi_stg")
BQ_DATASET_CORE = os.getenv("BQ_DATASET_CORE", "kalshi_core")
BQ_DATASET_OPS = os.getenv("BQ_DATASET_OPS", "kalshi_ops")

KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_API_SECRET = os.getenv("KALSHI_API_SECRET", "")  # path to PEM file
KALSHI_WS_ENDPOINT = os.getenv(
    "KALSHI_WS_ENDPOINT",
    "wss://api.elections.kalshi.com/trade-api/ws/v2",
)
KALSHI_WS_LISTEN_SECONDS = int(os.getenv("KALSHI_WS_LISTEN_SECONDS", "240"))
KALSHI_WS_MAX_MARKETS = int(os.getenv("KALSHI_WS_MAX_MARKETS", "25"))
KALSHI_WS_RECONNECT_DELAY_SECONDS = int(
    os.getenv("KALSHI_WS_RECONNECT_DELAY_SECONDS", "2")
)
KALSHI_WS_MAX_RECONNECTS = int(os.getenv("KALSHI_WS_MAX_RECONNECTS", "3"))

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BigQuery helpers (same pattern as the REST DAG)
# ---------------------------------------------------------------------------


def _require_bq_project() -> None:
    if not BQ_PROJECT or BQ_PROJECT == "YOUR_PROJECT_ID":
        raise ValueError(
            "Set BQ_PROJECT to a valid GCP project ID before running this DAG."
        )
    if "." in BQ_PROJECT:
        raise ValueError(
            "BQ_PROJECT must be only the GCP project id. "
            "Do not include dataset names."
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


def _run_query(sql: str, parameters: list | None = None):
    from google.cloud import bigquery

    client = _get_bq_client()
    job_config = bigquery.QueryJobConfig()
    if parameters:
        job_config.query_parameters = parameters
    job = client.query(sql, job_config=job_config)
    return list(job.result())


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_cell(value: Any) -> str:
    return json.dumps(value, default=str)


# ---------------------------------------------------------------------------
# WebSocket auth
# ---------------------------------------------------------------------------


def _build_ws_auth_headers() -> dict[str, str]:
    """Build Kalshi WebSocket authentication headers using RSA-PSS."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    if not KALSHI_API_KEY:
        raise ValueError("KALSHI_API_KEY is required for WebSocket auth.")
    if not KALSHI_API_SECRET:
        raise ValueError(
            "KALSHI_API_SECRET (path to PEM private key) is required."
        )

    with open(KALSHI_API_SECRET, "rb") as f:
        private_key = serialization.load_pem_private_key(
            f.read(), password=None
        )

    timestamp_ms = str(int(time.time() * 1000))
    message = timestamp_ms + "GET" + "/trade-api/ws/v2"

    signature = private_key.sign(
        message.encode(),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )

    return {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
    }


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

with DAG(
    dag_id="kalshi_ws_realtime_v0",
    description="Near-real-time Kalshi market data via WebSocket",
    schedule="*/5 * * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["kalshi", "websocket", "realtime"],
    default_args={
        "owner": "data-eng",
        "retries": 1,
        "retry_delay": 30,
    },
):
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end", trigger_rule="none_failed_min_one_success")

    # ------------------------------------------------------------------
    # Task 1: Resolve top markets by volume from the core layer
    # ------------------------------------------------------------------
    @task(task_id="resolve_top_markets")
    def resolve_top_markets() -> list[str]:
        """Query core layer for top-N open markets by volume."""
        max_markets = KALSHI_WS_MAX_MARKETS
        sql = f"""
            SELECT market_ticker
            FROM `{_table_ref(BQ_DATASET_CORE, "market_state_core")}`
            WHERE status = 'open'
              AND is_latest = TRUE
            ORDER BY volume_dollars DESC
            LIMIT {max_markets}
        """
        try:
            rows = _run_query(sql)
            tickers = [row.market_ticker for row in rows if row.market_ticker]
        except Exception as exc:
            log.warning(
                "Could not query core layer for top markets: %s. "
                "Falling back to staging.",
                exc,
            )
            fallback_sql = f"""
                SELECT market_ticker
                FROM `{_table_ref(BQ_DATASET_STG, "market_snapshots_stg")}`
                WHERE status = 'open'
                  AND ingestion_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
                GROUP BY market_ticker
                ORDER BY MAX(SAFE_CAST(volume_dollars AS NUMERIC)) DESC
                LIMIT {max_markets}
            """
            rows = _run_query(fallback_sql)
            tickers = [
                row.market_ticker for row in rows if row.market_ticker
            ]

        if not tickers:
            raise RuntimeError(
                "No open markets found in core or staging layers. "
                "Ensure the REST DAG has run at least once."
            )

        log.info("Resolved %d markets for WS subscription.", len(tickers))
        return tickers

    # ------------------------------------------------------------------
    # Task 2: Open WebSocket session, subscribe, and collect messages
    # ------------------------------------------------------------------
    @task(task_id="open_ws_session")
    def open_ws_session(market_tickers: list[str]) -> dict[str, Any]:
        """Connect to Kalshi WS, subscribe to channels, collect messages."""
        import websocket

        run_id = str(uuid.uuid4())
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        trades: list[dict] = []
        tickers: list[dict] = []
        orderbooks: list[dict] = []
        session_meta = {
            "run_id": run_id,
            "connected_at": None,
            "disconnected_at": None,
            "messages_received": 0,
            "trade_messages": 0,
            "ticker_messages": 0,
            "orderbook_messages": 0,
            "reconnect_count": 0,
            "error_message": None,
            "ingestion_date": today_str,
        }

        auth_headers = _build_ws_auth_headers()
        header_list = [f"{k}: {v}" for k, v in auth_headers.items()]

        cmd_id = 0

        def _next_cmd_id() -> int:
            nonlocal cmd_id
            cmd_id += 1
            return cmd_id

        def _subscribe(ws_conn):
            """Send subscription commands for all channels."""
            # Subscribe to ticker + trade
            sub_public = {
                "id": _next_cmd_id(),
                "cmd": "subscribe",
                "params": {
                    "channels": ["ticker", "trade"],
                    "market_tickers": market_tickers,
                },
            }
            ws_conn.send(json.dumps(sub_public))
            log.info(
                "Subscribed to ticker+trade for %d markets.",
                len(market_tickers),
            )

            # Subscribe to orderbook_delta
            sub_ob = {
                "id": _next_cmd_id(),
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_tickers": market_tickers,
                },
            }
            ws_conn.send(json.dumps(sub_ob))
            log.info(
                "Subscribed to orderbook_delta for %d markets.",
                len(market_tickers),
            )

        def _process_message(raw_msg: str) -> None:
            """Parse and route a single WS message."""
            session_meta["messages_received"] += 1
            now_iso = _utcnow_iso()
            try:
                msg = json.loads(raw_msg)
            except json.JSONDecodeError:
                log.warning("Non-JSON message: %s", raw_msg[:200])
                return

            msg_type = msg.get("type", "")

            if msg_type == "trade":
                session_meta["trade_messages"] += 1
                payload = msg.get("msg", {})
                trades.append({
                    "run_id": run_id,
                    "sid": msg.get("sid"),
                    "trade_id": payload.get("trade_id"),
                    "market_ticker": payload.get("market_ticker"),
                    "yes_price_dollars": str(
                        payload.get("yes_price_dollars", "")
                    ),
                    "no_price_dollars": str(
                        payload.get("no_price_dollars", "")
                    ),
                    "count_fp": str(payload.get("count_fp", "")),
                    "taker_side": payload.get("taker_side"),
                    "ts": payload.get("ts"),
                    "api_payload": _json_cell(msg),
                    "received_at": now_iso,
                    "ingestion_date": today_str,
                })

            elif msg_type == "ticker":
                session_meta["ticker_messages"] += 1
                payload = msg.get("msg", {})
                tickers.append({
                    "run_id": run_id,
                    "sid": msg.get("sid"),
                    "market_ticker": payload.get("market_ticker"),
                    "price_dollars": str(payload.get("price_dollars", "")),
                    "yes_bid_dollars": str(
                        payload.get("yes_bid_dollars", "")
                    ),
                    "yes_ask_dollars": str(
                        payload.get("yes_ask_dollars", "")
                    ),
                    "volume_fp": str(payload.get("volume_fp", "")),
                    "open_interest_fp": str(
                        payload.get("open_interest_fp", "")
                    ),
                    "ts": payload.get("ts"),
                    "api_payload": _json_cell(msg),
                    "received_at": now_iso,
                    "ingestion_date": today_str,
                })

            elif msg_type in ("orderbook_delta", "orderbook_snapshot"):
                session_meta["orderbook_messages"] += 1
                payload = msg.get("msg", {})
                orderbooks.append({
                    "run_id": run_id,
                    "sid": msg.get("sid"),
                    "seq": msg.get("seq"),
                    "market_ticker": payload.get("market_ticker"),
                    "snapshot_type": (
                        "snapshot"
                        if msg_type == "orderbook_snapshot"
                        else "delta"
                    ),
                    "api_payload": _json_cell(msg),
                    "received_at": now_iso,
                    "ingestion_date": today_str,
                })

            # Silently ignore: subscribed, ok, error, heartbeat, etc.

        # --- Run the WebSocket session with reconnect logic ---
        listen_seconds = KALSHI_WS_LISTEN_SECONDS
        max_reconnects = KALSHI_WS_MAX_RECONNECTS
        reconnect_delay = KALSHI_WS_RECONNECT_DELAY_SECONDS

        session_start = time.monotonic()
        attempt = 0

        while True:
            elapsed = time.monotonic() - session_start
            remaining = listen_seconds - elapsed
            if remaining <= 0:
                break

            try:
                log.info(
                    "Connecting to %s (attempt %d, %.0fs remaining)...",
                    KALSHI_WS_ENDPOINT,
                    attempt + 1,
                    remaining,
                )
                ws = websocket.create_connection(
                    KALSHI_WS_ENDPOINT,
                    header=header_list,
                    timeout=min(remaining, 30),
                )

                if session_meta["connected_at"] is None:
                    session_meta["connected_at"] = _utcnow_iso()

                _subscribe(ws)

                # Listen until time runs out
                while True:
                    elapsed = time.monotonic() - session_start
                    remaining = listen_seconds - elapsed
                    if remaining <= 0:
                        break
                    ws.settimeout(min(remaining, 15))
                    try:
                        raw = ws.recv()
                        if raw:
                            _process_message(raw)
                    except websocket.WebSocketTimeoutException:
                        # Normal timeout — check if listen window expired
                        continue

                ws.close()
                break  # Clean exit

            except Exception as exc:
                attempt += 1
                session_meta["reconnect_count"] = attempt
                err_str = str(exc)
                log.warning("WS error (attempt %d): %s", attempt, err_str)
                session_meta["error_message"] = err_str

                if attempt > max_reconnects:
                    log.error(
                        "Exceeded max reconnects (%d). Stopping.",
                        max_reconnects,
                    )
                    break

                time.sleep(reconnect_delay * attempt)

        session_meta["disconnected_at"] = _utcnow_iso()
        session_meta["listen_duration_seconds"] = int(
            time.monotonic() - session_start
        )
        session_meta["markets_subscribed"] = len(market_tickers)
        session_meta["channels"] = "ticker,trade,orderbook_delta"

        log.info(
            "WS session %s complete: %d trades, %d tickers, %d orderbook msgs "
            "in %ds.",
            run_id,
            len(trades),
            len(tickers),
            len(orderbooks),
            session_meta["listen_duration_seconds"],
        )

        return {
            "run_id": run_id,
            "trades": trades,
            "tickers": tickers,
            "orderbooks": orderbooks,
            "session_meta": session_meta,
        }

    # ------------------------------------------------------------------
    # Task 3: Persist raw messages to BigQuery
    # ------------------------------------------------------------------
    @task(task_id="persist_raw")
    def persist_raw(ws_result: dict[str, Any]) -> dict[str, Any]:
        """Insert raw WS messages into BigQuery raw tables."""
        trade_count = _insert_rows(
            BQ_DATASET_RAW, "ws_trades_raw", ws_result["trades"]
        )
        ticker_count = _insert_rows(
            BQ_DATASET_RAW, "ws_ticker_raw", ws_result["tickers"]
        )
        ob_count = _insert_rows(
            BQ_DATASET_RAW,
            "ws_orderbook_snapshots_raw",
            ws_result["orderbooks"],
        )

        log.info(
            "Persisted raw: %d trades, %d tickers, %d orderbook rows.",
            trade_count,
            ticker_count,
            ob_count,
        )

        return {
            "run_id": ws_result["run_id"],
            "raw_trades": trade_count,
            "raw_tickers": ticker_count,
            "raw_orderbooks": ob_count,
            "session_meta": ws_result["session_meta"],
        }

    # ------------------------------------------------------------------
    # Task 4: Build staging tables from raw
    # ------------------------------------------------------------------
    @task(task_id="build_ws_staging")
    def build_ws_staging(raw_summary: dict[str, Any]) -> dict[str, Any]:
        """Transform raw WS data into typed staging tables."""
        from google.cloud import bigquery

        run_id = raw_summary["run_id"]

        # --- Stage trades ---
        trades_stg_sql = f"""
            INSERT INTO `{_table_ref(BQ_DATASET_STG, "ws_trades_stg")}` (
                trade_id, market_ticker, yes_price_dollars, no_price_dollars,
                count_contracts, taker_side, created_time, received_at,
                source_run_id, ingestion_date
            )
            SELECT
                trade_id,
                market_ticker,
                SAFE_CAST(yes_price_dollars AS NUMERIC) AS yes_price_dollars,
                SAFE_CAST(no_price_dollars AS NUMERIC) AS no_price_dollars,
                SAFE_CAST(count_fp AS NUMERIC) AS count_contracts,
                taker_side,
                TIMESTAMP_SECONDS(SAFE_CAST(ts AS INT64)) AS created_time,
                received_at,
                run_id AS source_run_id,
                ingestion_date
            FROM `{_table_ref(BQ_DATASET_RAW, "ws_trades_raw")}`
            WHERE run_id = @run_id
              AND trade_id IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY trade_id ORDER BY received_at ASC
            ) = 1
        """

        # --- Stage ticker snapshots ---
        ticker_stg_sql = f"""
            INSERT INTO `{_table_ref(BQ_DATASET_STG, "ws_ticker_stg")}` (
                market_ticker, price_dollars, yes_bid_dollars, yes_ask_dollars,
                volume_dollars, open_interest_dollars, snapshot_ts, received_at,
                source_run_id, ingestion_date
            )
            SELECT
                market_ticker,
                SAFE_CAST(price_dollars AS NUMERIC) AS price_dollars,
                SAFE_CAST(yes_bid_dollars AS NUMERIC) AS yes_bid_dollars,
                SAFE_CAST(yes_ask_dollars AS NUMERIC) AS yes_ask_dollars,
                SAFE_CAST(volume_fp AS NUMERIC) AS volume_dollars,
                SAFE_CAST(open_interest_fp AS NUMERIC) AS open_interest_dollars,
                TIMESTAMP_SECONDS(SAFE_CAST(ts AS INT64)) AS snapshot_ts,
                received_at,
                run_id AS source_run_id,
                ingestion_date
            FROM `{_table_ref(BQ_DATASET_RAW, "ws_ticker_raw")}`
            WHERE run_id = @run_id
              AND market_ticker IS NOT NULL
        """

        client = _get_bq_client()
        run_id_param = bigquery.ScalarQueryParameter(
            "run_id", "STRING", run_id
        )

        trade_rows = 0
        ticker_rows = 0

        if raw_summary["raw_trades"] > 0:
            job = client.query(
                trades_stg_sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[run_id_param]
                ),
            )
            result = job.result()
            trade_rows = job.num_dml_affected_rows or 0
            log.info("Staged %d WS trades.", trade_rows)

        if raw_summary["raw_tickers"] > 0:
            job = client.query(
                ticker_stg_sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[run_id_param]
                ),
            )
            result = job.result()
            ticker_rows = job.num_dml_affected_rows or 0
            log.info("Staged %d WS ticker snapshots.", ticker_rows)

        return {
            "run_id": run_id,
            "staged_trades": trade_rows,
            "staged_tickers": ticker_rows,
            "session_meta": raw_summary["session_meta"],
        }

    # ------------------------------------------------------------------
    # Task 5: Log the WS connection session to ops
    # ------------------------------------------------------------------
    @task(task_id="log_ws_connection")
    def log_ws_connection(staging_summary: dict[str, Any]) -> None:
        """Write a session summary row to ws_connection_log."""
        meta = staging_summary["session_meta"]
        row = {
            "run_id": meta["run_id"],
            "connected_at": meta["connected_at"],
            "disconnected_at": meta["disconnected_at"],
            "listen_duration_seconds": meta.get("listen_duration_seconds"),
            "markets_subscribed": meta.get("markets_subscribed"),
            "channels": meta.get("channels"),
            "messages_received": meta.get("messages_received", 0),
            "trade_messages": meta.get("trade_messages", 0),
            "ticker_messages": meta.get("ticker_messages", 0),
            "orderbook_messages": meta.get("orderbook_messages", 0),
            "reconnect_count": meta.get("reconnect_count", 0),
            "error_message": meta.get("error_message"),
            "ingestion_date": meta["ingestion_date"],
        }
        _insert_rows(BQ_DATASET_OPS, "ws_connection_log", [row])
        log.info("Logged WS session %s to ws_connection_log.", meta["run_id"])

    # ------------------------------------------------------------------
    # Wire up the DAG
    # ------------------------------------------------------------------
    top_markets = resolve_top_markets()
    ws_result = open_ws_session(top_markets)
    raw_summary = persist_raw(ws_result)
    staging_summary = build_ws_staging(raw_summary)
    connection_log = log_ws_connection(staging_summary)

    start >> top_markets
    connection_log >> end
