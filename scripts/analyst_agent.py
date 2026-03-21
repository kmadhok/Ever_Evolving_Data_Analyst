#!/usr/bin/env python3
"""Kalshi Data Analyst Agent — natural-language queries against BigQuery.

Usage:
    python scripts/analyst_agent.py ask "What are the top 10 markets by volume today?"
    python scripts/analyst_agent.py report --type daily
    python scripts/analyst_agent.py report --type signals
    python scripts/analyst_agent.py report --type market --ticker KXBTC
    python scripts/analyst_agent.py report --type daily --output reports/daily.md

Requires:
    OPENAI_API_KEY           — OpenAI API key
    GOOGLE_APPLICATION_CREDENTIALS — path to GCP service-account JSON
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from google.cloud import bigquery
from openai import OpenAI
from tabulate import tabulate

# ── Configuration ───────────────────────────────────────────────────────

GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "brainrot-453319")

DATASETS = {
    "raw": "kalshi_raw",
    "stg": "kalshi_stg",
    "core": "kalshi_core",
    "dash": "kalshi_dash",
    "signal": "kalshi_signal",
    "ops": "kalshi_ops",
}

MAX_ROWS = 1000
MAX_BYTES_BILLED = 500 * 1024 * 1024  # 500 MB guard-rail
OPENAI_MODEL = "gpt-4o"

# ── Schema context (fed to the LLM for SQL generation) ──────────────────

SCHEMA_CONTEXT = textwrap.dedent("""\
    You are a data analyst for a Kalshi prediction-market analytics platform.
    The BigQuery project is `{project}`.

    ## Datasets & Tables

    ### kalshi_core (source of truth — use these first)
    - **events_dim**: event_ticker (PK), series_ticker, title, sub_title, status,
      last_updated_ts, is_latest (BOOL — filter TRUE for current row).
    - **market_state_core**: market_ticker, event_ticker (FK→events_dim),
      series_ticker, status, yes_bid_dollars, yes_ask_dollars, no_bid_dollars,
      no_ask_dollars, last_price_dollars, volume_dollars, open_interest_dollars,
      close_time, snapshot_ts, is_latest (BOOL).
    - **trade_prints_core**: trade_id (PK), market_ticker (FK→market_state_core),
      yes_price_dollars, no_price_dollars, count_contracts, taker_side ('yes'/'no'),
      created_time, snapshot_ts.
    - **kpi_5m**: kpi_ts, series_ticker (NULL=all), open_market_count,
      total_volume_dollars, total_open_interest_dollars, trade_count_lookback.

    ### kalshi_stg (staging — parsed but not deduped)
    - **market_snapshots_stg**: same schema as market_state_core minus is_latest,
      plus source_call_id.
    - **trades_stg**: same as trade_prints_core plus source_call_id.
    - **orderbook_levels_stg**: market_ticker, side ('yes'/'no'), price_dollars,
      quantity_contracts, level_rank (1=top of book), snapshot_ts.

    ### kalshi_dash (pre-built dashboard views)
    - vw_pipeline_heartbeat, vw_quality_checks_24h, vw_top_active_markets_24h,
      vw_trade_flow_6h, vw_most_traded_markets_60m, vw_price_movers_60m,
      vw_simple_side_summary_24h, vw_consumer_matchup_view,
      vw_orderbook_top_snapshot.

    ### kalshi_signal (market intelligence signals)
    - **vw_signal_feed_latest**: signal_id, signal_type, signal_window,
      entity_type, entity_id, title, market_family, score (FLOAT64),
      severity (LOW/MEDIUM/HIGH), signal_ts, explanation_short, metrics_json.
    - Individual signal views: vw_signal_probability_shifts_24h,
      vw_signal_volume_spikes_6h, vw_signal_volatility_spikes_1h,
      vw_signal_liquidity_deterioration_latest,
      vw_signal_open_interest_change_12h,
      vw_signal_cross_market_inconsistencies,
      vw_signal_event_reactions_3h.
    - Helper views: vw_market_latest, vw_event_latest.

    ### kalshi_ops (operational metadata)
    - quality_results: run_id, check_name, status, metric_value,
      threshold_value, checked_at.
    - signal_runs: run_id, signal_count, signal_type_counts (JSON).
    - market_intelligence_reports: report_id, report_type, summary_json.

    ## Join Keys
    - event_ticker: events ↔ markets
    - market_ticker: markets ↔ trades ↔ orderbooks
    - series_ticker: groups related events (e.g., all HIGHTEMP)
    - trade_id: unique per trade

    ## Join Chain
    events_dim (event_ticker) → market_state_core (market_ticker) →
    trade_prints_core / orderbook_levels_stg

    ## Important Patterns
    1. Always use LEFT JOIN (data arrives asynchronously).
    2. Filter is_latest = TRUE on events_dim and market_state_core for
       current snapshots.
    3. Event title fallback: COALESCE(e.title, m.event_ticker,
       REGEXP_EXTRACT(t.market_ticker, r'^(.*)-[^-]+$')).
    4. market_ticker format: {{event_ticker}}-{{side_label}}
       (e.g., HIGHTEMP-25FEB01-T90.0).
    5. Use dashboard views (kalshi_dash.vw_*) when they already answer
       the question — they're cheaper.
    6. Use signal views (kalshi_signal.vw_signal_feed_latest) for anomaly
       questions.

    ## Rules
    - SELECT only. Never generate INSERT, UPDATE, DELETE, DROP, CREATE, ALTER.
    - Always include LIMIT (max {max_rows}).
    - Fully-qualify table names: `{project}.dataset.table`.
    - Use TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL ...) for time filters.
    - Use SAFE_DIVIDE for ratio calculations.
    - Return only the raw SQL — no markdown fences, no commentary.
""").format(project=GCP_PROJECT, max_rows=MAX_ROWS)


# ── Report prompts ──────────────────────────────────────────────────────

REPORT_PROMPTS = {
    "daily": (
        "Generate a daily summary report covering:\n"
        "1. Total active events and open markets\n"
        "2. Top 10 markets by volume in last 24h (show event title, volume, last price)\n"
        "3. Top 5 biggest price movers in last 24h (show probability change)\n"
        "4. Trade activity summary (total trades, total contracts, avg price)\n"
        "5. Any notable signals from vw_signal_feed_latest (severity = 'HIGH')\n"
        "Run multiple queries as needed and combine into a coherent report."
    ),
    "signals": (
        "Generate a signal intelligence report covering:\n"
        "1. All HIGH severity signals from vw_signal_feed_latest\n"
        "2. Count of signals by type and severity\n"
        "3. Top entities (markets/events) with the most signals\n"
        "4. Any cross-market inconsistencies\n"
        "Highlight anything an analyst should investigate."
    ),
    "market": (
        "Generate a deep-dive analysis for the market ticker or series: {ticker}\n"
        "Cover:\n"
        "1. Current market state (price, volume, OI, bid/ask)\n"
        "2. Event context (title, status)\n"
        "3. Recent trade activity (last 24h: trade count, volume, avg price)\n"
        "4. Price movement history (first/last price, change)\n"
        "5. Any signals associated with this market/event\n"
        "6. Orderbook top-of-book if available"
    ),
}


# ── Helpers ─────────────────────────────────────────────────────────────

def _serialize(value: Any) -> Any:
    """Make BigQuery row values JSON-safe."""
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _is_safe_sql(sql: str) -> bool:
    """Reject anything that isn't a SELECT."""
    normalized = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)  # strip comments
    normalized = re.sub(r"/\*.*?\*/", "", normalized, flags=re.DOTALL)
    normalized = normalized.strip().rstrip(";").strip()
    dangerous = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|MERGE|GRANT|REVOKE)\b",
        re.IGNORECASE,
    )
    return not dangerous.search(normalized)


def _ensure_limit(sql: str) -> str:
    """Inject LIMIT if missing."""
    if re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        return sql
    return sql.rstrip().rstrip(";") + f"\nLIMIT {MAX_ROWS}"


def _run_query(bq: bigquery.Client, sql: str) -> list[dict[str, Any]]:
    """Execute a read-only BigQuery query with cost guard-rails."""
    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=MAX_BYTES_BILLED,
    )
    job = bq.query(sql, job_config=job_config)
    rows = list(job.result())
    return [_serialize(dict(row.items())) for row in rows]


def _format_table(rows: list[dict[str, Any]]) -> str:
    """Format rows as a terminal-friendly table."""
    if not rows:
        return "(no rows returned)"
    return tabulate(rows, headers="keys", tablefmt="simple", floatfmt=".4f")


def _chat(client: OpenAI, system: str, user: str, max_tokens: int = 4096) -> str:
    """Send a chat completion request and return the response text."""
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content.strip()


def _generate_sql(client: OpenAI, question: str) -> str:
    """Ask the LLM to generate a BigQuery SQL query for the question."""
    sql = _chat(client, SCHEMA_CONTEXT, question, max_tokens=2048)
    # Strip markdown fences if wrapped
    sql = re.sub(r"^```(?:sql)?\s*", "", sql)
    sql = re.sub(r"\s*```$", "", sql)
    return sql.strip()


def _format_answer(
    client: OpenAI,
    question: str,
    sql: str,
    rows: list[dict[str, Any]],
) -> str:
    """Ask the LLM to format query results into a human-readable answer."""
    row_preview = json.dumps(rows[:50], default=str, indent=2)
    total = len(rows)
    prompt = (
        f"The user asked: {question}\n\n"
        f"This SQL was executed:\n```sql\n{sql}\n```\n\n"
        f"It returned {total} row(s). Here are the results "
        f"({'first 50 shown' if total > 50 else 'all'}):\n"
        f"```json\n{row_preview}\n```\n\n"
        "Provide a clear, concise answer to the user's question based on "
        "these results. Use markdown formatting. Include a summary table "
        "if appropriate. Highlight key insights."
    )
    return _chat(client, "You are a data analyst writing a concise report.", prompt)


# ── Commands ────────────────────────────────────────────────────────────

def cmd_ask(args: argparse.Namespace) -> int:
    """Handle the 'ask' subcommand."""
    question = " ".join(args.question)
    if not question.strip():
        print("Error: provide a question.", file=sys.stderr)
        return 1

    llm = OpenAI()
    bq = bigquery.Client(project=GCP_PROJECT)

    # Step 1: Generate SQL
    print("Generating SQL...", file=sys.stderr)
    sql = _generate_sql(llm, question)
    print(f"\n--- SQL ---\n{sql}\n-----------\n", file=sys.stderr)

    # Step 2: Validate
    if not _is_safe_sql(sql):
        print("Error: generated SQL contains disallowed statements.", file=sys.stderr)
        return 1
    sql = _ensure_limit(sql)

    # Step 3: Execute
    print("Querying BigQuery...", file=sys.stderr)
    try:
        rows = _run_query(bq, sql)
    except Exception as exc:
        print(f"BigQuery error: {exc}", file=sys.stderr)
        # Retry once with error context
        print("Retrying with error context...", file=sys.stderr)
        retry_prompt = (
            f"The previous SQL failed with this error:\n{exc}\n\n"
            f"Original question: {question}\n\n"
            f"Failed SQL:\n{sql}\n\n"
            "Fix the SQL and return only the corrected query."
        )
        sql = _generate_sql(llm, retry_prompt)
        print(f"\n--- Retry SQL ---\n{sql}\n-----------------\n", file=sys.stderr)
        if not _is_safe_sql(sql):
            print("Error: retry SQL contains disallowed statements.", file=sys.stderr)
            return 1
        sql = _ensure_limit(sql)
        try:
            rows = _run_query(bq, sql)
        except Exception as exc2:
            print(f"BigQuery retry failed: {exc2}", file=sys.stderr)
            return 1

    # Step 4: Format answer
    print(f"Got {len(rows)} row(s). Formatting answer...\n", file=sys.stderr)
    answer = _format_answer(llm, question, sql, rows)
    print(answer)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Handle the 'report' subcommand."""
    report_type = args.type
    if report_type not in REPORT_PROMPTS:
        print(f"Error: unknown report type '{report_type}'. Use: {', '.join(REPORT_PROMPTS)}", file=sys.stderr)
        return 1

    prompt_template = REPORT_PROMPTS[report_type]
    if report_type == "market":
        if not args.ticker:
            print("Error: --ticker is required for market reports.", file=sys.stderr)
            return 1
        prompt = prompt_template.format(ticker=args.ticker)
    else:
        prompt = prompt_template

    llm = OpenAI()
    bq = bigquery.Client(project=GCP_PROJECT)

    # For reports, we do multi-step: generate queries, run them, then synthesize
    print(f"Generating {report_type} report...", file=sys.stderr)

    # Step 1: Ask LLM to generate multiple queries
    multi_sql_prompt = (
        f"{prompt}\n\n"
        "Return a JSON array of objects, each with 'label' (short description) "
        "and 'sql' (the BigQuery query). Return ONLY the JSON array, no markdown fences."
    )
    raw_json = _chat(llm, SCHEMA_CONTEXT, multi_sql_prompt)
    raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json)
    raw_json = re.sub(r"\s*```$", "", raw_json)

    try:
        queries = json.loads(raw_json)
    except json.JSONDecodeError:
        print("Error: failed to parse generated queries.", file=sys.stderr)
        print(raw_json, file=sys.stderr)
        return 1

    if not isinstance(queries, list):
        print("Error: expected a JSON array of queries.", file=sys.stderr)
        return 1

    # Step 2: Execute each query
    results: list[dict[str, Any]] = []
    for i, q in enumerate(queries):
        label = q.get("label", f"Query {i + 1}")
        sql = q.get("sql", "")
        if not sql or not _is_safe_sql(sql):
            print(f"  Skipping '{label}': unsafe or empty SQL", file=sys.stderr)
            continue
        sql = _ensure_limit(sql)
        print(f"  Running: {label}...", file=sys.stderr)
        try:
            rows = _run_query(bq, sql)
            results.append({"label": label, "sql": sql, "rows": rows, "error": None})
        except Exception as exc:
            results.append({"label": label, "sql": sql, "rows": [], "error": str(exc)})
            print(f"  Warning: '{label}' failed: {exc}", file=sys.stderr)

    # Step 3: Synthesize report
    print("Synthesizing report...\n", file=sys.stderr)
    result_summary = []
    for r in results:
        entry: dict[str, Any] = {"label": r["label"], "row_count": len(r["rows"])}
        if r["error"]:
            entry["error"] = r["error"]
        else:
            entry["sample_rows"] = r["rows"][:30]
        result_summary.append(entry)

    synthesis_prompt = (
        f"Report type: {report_type}\n"
        f"Request: {prompt}\n\n"
        f"Query results:\n```json\n{json.dumps(result_summary, default=str, indent=2)}\n```\n\n"
        "Write a comprehensive analyst report in markdown format. Include:\n"
        "- Executive summary\n"
        "- Key findings with data tables\n"
        "- Notable observations or anomalies\n"
        "- Recommendations for further investigation\n"
        f"\nTimestamp: {datetime.now(timezone.utc).isoformat()}"
    )
    report_text = _chat(
        llm,
        "You are a senior data analyst writing a comprehensive market intelligence report.",
        synthesis_prompt,
    )

    # Output
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_text, encoding="utf-8")
        print(f"Report saved to {output_path}", file=sys.stderr)
    else:
        print(report_text)

    return 0


# ── CLI ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Kalshi Data Analyst Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              %(prog)s ask "What are the top 10 markets by volume today?"
              %(prog)s ask "Show me HIGH severity signals"
              %(prog)s report --type daily
              %(prog)s report --type signals
              %(prog)s report --type market --ticker KXBTC
              %(prog)s report --type daily --output reports/daily_2026-03-20.md

            environment variables:
              OPENAI_API_KEY                  OpenAI API key (required)
              GOOGLE_APPLICATION_CREDENTIALS  GCP service-account JSON path
              GCP_PROJECT_ID                  BigQuery project (default: brainrot-453319)
        """),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ask
    ask_parser = sub.add_parser("ask", help="Ask a natural-language question")
    ask_parser.add_argument("question", nargs="+", help="The question to answer")

    # report
    report_parser = sub.add_parser("report", help="Generate an automated report")
    report_parser.add_argument(
        "--type", required=True, choices=["daily", "signals", "market"],
        help="Report type",
    )
    report_parser.add_argument("--ticker", default=None, help="Market/series ticker (for market reports)")
    report_parser.add_argument("--output", default=None, help="Save report to file instead of stdout")

    args = parser.parse_args()

    # Validate env
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable is required.", file=sys.stderr)
        print("  export OPENAI_API_KEY=sk-...", file=sys.stderr)
        return 1

    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds:
        print("Warning: GOOGLE_APPLICATION_CREDENTIALS not set; using default credentials.", file=sys.stderr)

    if args.command == "ask":
        return cmd_ask(args)
    elif args.command == "report":
        return cmd_report(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
