#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from google.cloud import bigquery

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = REPO_ROOT / "sql" / "bigquery"

SQL_GROUPS = {
    "kalshi-core": [
        "002_kalshi_baseline_tables.sql",
        "004_kalshi_dashboard_views.sql",
        "007_kalshi_ds_views.sql",
        "008_kalshi_event_ingestion_tables.sql",
    ],
    "kalshi-signals": [
        "009_kalshi_market_intelligence_signals.sql",
        "010_kalshi_signal_reporting_tables.sql",
    ],
    "kalshi-ws": [
        "011_kalshi_ws_tables.sql",
    ],
    "odds-core": [
        "001_baseline_tables.sql",
    ],
}


def load_sql(path: Path, project_id: str) -> str:
    text = path.read_text(encoding="utf-8")
    return text.replace("YOUR_PROJECT_ID", project_id).replace("brainrot-453319", project_id)


def _strip_leading_comments(stmt: str) -> str:
    """Remove leading SQL comment lines and blank lines from a statement."""
    lines = stmt.splitlines()
    while lines and (not lines[0].strip() or lines[0].strip().startswith("--")):
        lines.pop(0)
    return "\n".join(lines).strip()


def _split_statements(sql: str) -> list[str]:
    """Split a SQL file into individual statements on semicolons."""
    stmts = []
    for raw in sql.split(";"):
        cleaned = _strip_leading_comments(raw.strip())
        if cleaned:
            stmts.append(cleaned)
    return stmts


def apply_group(project_id: str, group: str, dry_run: bool) -> None:
    client = bigquery.Client(project=project_id)
    for filename in SQL_GROUPS[group]:
        path = SQL_DIR / filename
        sql = load_sql(path, project_id)
        statements = _split_statements(sql)
        for i, stmt in enumerate(statements, 1):
            print(f"Applying {filename} statement {i}/{len(statements)} to {project_id}")
            job = client.query(stmt, job_config=bigquery.QueryJobConfig(dry_run=dry_run, use_query_cache=False))
            job.result()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply ordered SQL groups to BigQuery.")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--group", choices=sorted(SQL_GROUPS), required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    apply_group(args.project_id, args.group, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
