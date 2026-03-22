#!/usr/bin/env python3
"""Standalone Kalshi WebSocket ingestion script.

Fetches top markets from the Kalshi REST API, connects to the WebSocket,
collects trade/ticker/orderbook messages for a listen window, and writes
everything to BigQuery.  No Airflow dependency.

Usage:
    # Load env and run
    export GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json
    python scripts/kalshi_ws_ingest.py

    # Or with the project venv
    GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json \
        ./apps/api/.venv/bin/python scripts/kalshi_ws_ingest.py
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Load .env.kalshi.local if present (simple key=value parser)
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
        # Only set if not already in environment (env vars take precedence)
        if key not in os.environ:
            os.environ[key] = value


# Load env files in priority order (first file wins for each key)
_load_dotenv(_ROOT / ".env.kalshi.local")       # project-specific overrides
_load_dotenv(_ROOT / ".env.airflow.local")       # has actual API keys
_load_dotenv(_ROOT / ".env.kalshi.local.example")  # defaults / fallback

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BQ_PROJECT = os.getenv("BQ_PROJECT", "brainrot-453319")
BQ_DATASET_RAW = os.getenv("BQ_DATASET_RAW", "kalshi_raw")
BQ_DATASET_STG = os.getenv("BQ_DATASET_STG", "kalshi_stg")
BQ_DATASET_OPS = os.getenv("BQ_DATASET_OPS", "kalshi_ops")

KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_API_BASE_URL = os.getenv(
    "KALSHI_API_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2"
)
# For local runs, point directly at the PEM file on disk
KALSHI_PRIVATE_KEY_PATH = os.getenv(
    "KALSHI_PRIVATE_KEY_PATH",
    os.getenv("KALSHI_PRIVATE_KEY_HOST", "./kalshi_private_key.pem"),
)
KALSHI_WS_ENDPOINT = os.getenv(
    "KALSHI_WS_ENDPOINT",
    "wss://api.elections.kalshi.com/trade-api/ws/v2",
)
KALSHI_WS_LISTEN_SECONDS = int(os.getenv("KALSHI_WS_LISTEN_SECONDS", "240"))
KALSHI_WS_MAX_MARKETS = int(os.getenv("KALSHI_WS_MAX_MARKETS", "25"))
KALSHI_WS_RECONNECT_DELAY = int(os.getenv("KALSHI_WS_RECONNECT_DELAY_SECONDS", "2"))
KALSHI_WS_MAX_RECONNECTS = int(os.getenv("KALSHI_WS_MAX_RECONNECTS", "3"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("kalshi_ws_ingest")


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


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_cell(value: Any) -> str:
    return json.dumps(value, default=str)


# ---------------------------------------------------------------------------
# Step 1: Fetch top markets from Kalshi REST API (no BQ dependency)
# ---------------------------------------------------------------------------


def fetch_top_markets(max_markets: int = KALSHI_WS_MAX_MARKETS) -> list[str]:
    """Get top open markets by volume directly from the Kalshi REST API."""
    import requests

    url = f"{KALSHI_API_BASE_URL}/markets"
    params = {
        "status": "open",
        "limit": max_markets,
    }
    log.info("Fetching top %d open markets from %s ...", max_markets, url)
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()

    data = resp.json()
    markets = data.get("markets", [])

    # Sort by volume descending and take top N
    markets.sort(key=lambda m: int(m.get("volume", 0) or 0), reverse=True)
    tickers = [m["ticker"] for m in markets[:max_markets] if m.get("ticker")]

    if not tickers:
        raise RuntimeError(
            f"No open markets returned from {url}. "
            "Check KALSHI_API_BASE_URL and network connectivity."
        )

    log.info("Resolved %d markets: %s", len(tickers), ", ".join(tickers[:5]))
    return tickers


# ---------------------------------------------------------------------------
# Step 2: WebSocket auth
# ---------------------------------------------------------------------------


def _build_ws_auth_headers() -> dict[str, str]:
    """Build Kalshi WebSocket authentication headers using RSA-PSS."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    if not KALSHI_API_KEY:
        raise ValueError("KALSHI_API_KEY is required for WebSocket auth.")

    key_path = Path(KALSHI_PRIVATE_KEY_PATH)
    if not key_path.exists():
        raise FileNotFoundError(
            f"Private key not found at {key_path}. "
            "Set KALSHI_PRIVATE_KEY_PATH to the correct path."
        )

    private_key = serialization.load_pem_private_key(
        key_path.read_bytes(), password=None
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
# Step 3: WebSocket session — connect, subscribe, collect messages
# ---------------------------------------------------------------------------


def run_ws_session(
    market_tickers: list[str],
) -> dict[str, Any]:
    """Connect to Kalshi WS, subscribe, and collect messages."""
    import websocket

    run_id = str(uuid.uuid4())
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    trades: list[dict] = []
    tickers: list[dict] = []
    orderbooks: list[dict] = []
    meta = {
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

    def _next_id() -> int:
        nonlocal cmd_id
        cmd_id += 1
        return cmd_id

    def _subscribe(ws_conn):
        ws_conn.send(json.dumps({
            "id": _next_id(),
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker", "trade"],
                "market_tickers": market_tickers,
            },
        }))
        ws_conn.send(json.dumps({
            "id": _next_id(),
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta"],
                "market_tickers": market_tickers,
            },
        }))
        log.info(
            "Subscribed to ticker+trade+orderbook_delta for %d markets.",
            len(market_tickers),
        )

    def _process(raw_msg: str) -> None:
        meta["messages_received"] += 1
        now_iso = _utcnow_iso()
        try:
            msg = json.loads(raw_msg)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type", "")

        if msg_type == "trade":
            meta["trade_messages"] += 1
            p = msg.get("msg", {})
            trades.append({
                "run_id": run_id,
                "sid": msg.get("sid"),
                "trade_id": p.get("trade_id"),
                "market_ticker": p.get("market_ticker"),
                "yes_price_dollars": str(p.get("yes_price_dollars", "")),
                "no_price_dollars": str(p.get("no_price_dollars", "")),
                "count_fp": str(p.get("count_fp", "")),
                "taker_side": p.get("taker_side"),
                "ts": p.get("ts"),
                "api_payload": _json_cell(msg),
                "received_at": now_iso,
                "ingestion_date": today_str,
            })

        elif msg_type == "ticker":
            meta["ticker_messages"] += 1
            p = msg.get("msg", {})
            tickers.append({
                "run_id": run_id,
                "sid": msg.get("sid"),
                "market_ticker": p.get("market_ticker"),
                "price_dollars": str(p.get("price_dollars", "")),
                "yes_bid_dollars": str(p.get("yes_bid_dollars", "")),
                "yes_ask_dollars": str(p.get("yes_ask_dollars", "")),
                "volume_fp": str(p.get("volume_fp", "")),
                "open_interest_fp": str(p.get("open_interest_fp", "")),
                "ts": p.get("ts"),
                "api_payload": _json_cell(msg),
                "received_at": now_iso,
                "ingestion_date": today_str,
            })

        elif msg_type in ("orderbook_delta", "orderbook_snapshot"):
            meta["orderbook_messages"] += 1
            p = msg.get("msg", {})
            orderbooks.append({
                "run_id": run_id,
                "sid": msg.get("sid"),
                "seq": msg.get("seq"),
                "market_ticker": p.get("market_ticker"),
                "snapshot_type": "snapshot" if msg_type == "orderbook_snapshot" else "delta",
                "api_payload": _json_cell(msg),
                "received_at": now_iso,
                "ingestion_date": today_str,
            })

    # --- Connect with reconnect logic ---
    session_start = time.monotonic()
    attempt = 0

    while True:
        elapsed = time.monotonic() - session_start
        remaining = KALSHI_WS_LISTEN_SECONDS - elapsed
        if remaining <= 0:
            break

        try:
            log.info(
                "Connecting to %s (attempt %d, %.0fs remaining)...",
                KALSHI_WS_ENDPOINT, attempt + 1, remaining,
            )
            ws = websocket.create_connection(
                KALSHI_WS_ENDPOINT,
                header=header_list,
                timeout=min(remaining, 30),
            )
            if meta["connected_at"] is None:
                meta["connected_at"] = _utcnow_iso()

            _subscribe(ws)

            while True:
                elapsed = time.monotonic() - session_start
                remaining = KALSHI_WS_LISTEN_SECONDS - elapsed
                if remaining <= 0:
                    break
                ws.settimeout(min(remaining, 15))
                try:
                    raw = ws.recv()
                    if raw:
                        _process(raw)
                except websocket.WebSocketTimeoutException:
                    continue

            ws.close()
            break

        except Exception as exc:
            attempt += 1
            meta["reconnect_count"] = attempt
            meta["error_message"] = str(exc)
            log.warning("WS error (attempt %d): %s", attempt, exc)
            if attempt > KALSHI_WS_MAX_RECONNECTS:
                log.error("Exceeded max reconnects (%d). Stopping.", KALSHI_WS_MAX_RECONNECTS)
                break
            time.sleep(KALSHI_WS_RECONNECT_DELAY * attempt)

    meta["disconnected_at"] = _utcnow_iso()
    meta["listen_duration_seconds"] = int(time.monotonic() - session_start)
    meta["markets_subscribed"] = len(market_tickers)
    meta["channels"] = "ticker,trade,orderbook_delta"

    log.info(
        "Session %s done: %d trades, %d tickers, %d orderbook msgs in %ds.",
        run_id, len(trades), len(tickers), len(orderbooks),
        meta["listen_duration_seconds"],
    )

    return {
        "run_id": run_id,
        "trades": trades,
        "tickers": tickers,
        "orderbooks": orderbooks,
        "meta": meta,
    }


# ---------------------------------------------------------------------------
# Step 4: Persist raw to BigQuery
# ---------------------------------------------------------------------------


def persist_raw(result: dict[str, Any]) -> dict[str, int]:
    """Insert raw WS messages into BigQuery raw tables."""
    trade_n = _insert_rows(BQ_DATASET_RAW, "ws_trades_raw", result["trades"])
    ticker_n = _insert_rows(BQ_DATASET_RAW, "ws_ticker_raw", result["tickers"])
    ob_n = _insert_rows(BQ_DATASET_RAW, "ws_orderbook_snapshots_raw", result["orderbooks"])
    log.info("Persisted raw: %d trades, %d tickers, %d orderbook rows.", trade_n, ticker_n, ob_n)
    return {"trades": trade_n, "tickers": ticker_n, "orderbooks": ob_n}


# ---------------------------------------------------------------------------
# Step 5: Build staging tables
# ---------------------------------------------------------------------------


def build_staging(run_id: str, raw_counts: dict[str, int]) -> None:
    """Transform raw WS data into typed staging tables."""
    from google.cloud import bigquery

    client = _get_bq_client()
    run_id_param = bigquery.ScalarQueryParameter("run_id", "STRING", run_id)

    if raw_counts["trades"] > 0:
        sql = f"""
            INSERT INTO `{_table_ref(BQ_DATASET_STG, "ws_trades_stg")}` (
                trade_id, market_ticker, yes_price_dollars, no_price_dollars,
                count_contracts, taker_side, created_time, received_at,
                source_run_id, ingestion_date
            )
            SELECT
                trade_id, market_ticker,
                SAFE_CAST(yes_price_dollars AS NUMERIC),
                SAFE_CAST(no_price_dollars AS NUMERIC),
                SAFE_CAST(count_fp AS NUMERIC),
                taker_side,
                TIMESTAMP_SECONDS(SAFE_CAST(ts AS INT64)),
                received_at, run_id, ingestion_date
            FROM `{_table_ref(BQ_DATASET_RAW, "ws_trades_raw")}`
            WHERE run_id = @run_id AND trade_id IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (PARTITION BY trade_id ORDER BY received_at) = 1
        """
        job = client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=[run_id_param]))
        job.result()
        log.info("Staged %d WS trades.", job.num_dml_affected_rows or 0)

    if raw_counts["tickers"] > 0:
        sql = f"""
            INSERT INTO `{_table_ref(BQ_DATASET_STG, "ws_ticker_stg")}` (
                market_ticker, price_dollars, yes_bid_dollars, yes_ask_dollars,
                volume_dollars, open_interest_dollars, snapshot_ts, received_at,
                source_run_id, ingestion_date
            )
            SELECT
                market_ticker,
                SAFE_CAST(price_dollars AS NUMERIC),
                SAFE_CAST(yes_bid_dollars AS NUMERIC),
                SAFE_CAST(yes_ask_dollars AS NUMERIC),
                SAFE_CAST(volume_fp AS NUMERIC),
                SAFE_CAST(open_interest_fp AS NUMERIC),
                TIMESTAMP_SECONDS(SAFE_CAST(ts AS INT64)),
                received_at, run_id, ingestion_date
            FROM `{_table_ref(BQ_DATASET_RAW, "ws_ticker_raw")}`
            WHERE run_id = @run_id AND market_ticker IS NOT NULL
        """
        job = client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=[run_id_param]))
        job.result()
        log.info("Staged %d WS ticker snapshots.", job.num_dml_affected_rows or 0)


# ---------------------------------------------------------------------------
# Step 6: Log session to ops table
# ---------------------------------------------------------------------------


def log_session(meta: dict[str, Any]) -> None:
    """Write session summary to ws_connection_log."""
    _insert_rows(BQ_DATASET_OPS, "ws_connection_log", [{
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
    }])
    log.info("Logged session %s to ws_connection_log.", meta["run_id"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    log.info("=== Kalshi WS Ingest (single-shot) ===")

    # 1. Fetch markets from REST API
    tickers = fetch_top_markets()

    # 2. Connect to WS + collect messages
    result = run_ws_session(tickers)

    # 3. Persist raw to BQ
    raw_counts = persist_raw(result)

    # 4. Build staging
    build_staging(result["run_id"], raw_counts)

    # 5. Log session
    log_session(result["meta"])

    total = (
        result["meta"]["trade_messages"]
        + result["meta"]["ticker_messages"]
        + result["meta"]["orderbook_messages"]
    )
    log.info(
        "=== Done. %d total messages collected in %ds. ===",
        total, result["meta"]["listen_duration_seconds"],
    )


if __name__ == "__main__":
    main()
