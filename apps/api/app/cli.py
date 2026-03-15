from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .autonomy_service import governance_summary, run_autonomy_cycle
from .config import get_settings
from .live_validation import run_live_validation
from .repository import BigQueryRepository


def _repo() -> BigQueryRepository:
    return BigQueryRepository(get_settings())


def _print_payload(payload) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def cmd_env_check(_: argparse.Namespace) -> int:
    settings = get_settings()
    credential_path = settings.google_application_credentials or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    payload = {
        "app_env": settings.app_env,
        "gcp_project_id": settings.gcp_project_id,
        "datasets": {
            "dash": settings.bq_dash_dataset,
            "signal": settings.bq_signal_dataset,
            "ops": settings.bq_ops_dataset,
            "core": settings.bq_core_dataset,
        },
        "google_application_credentials": credential_path,
        "credentials_file_exists": bool(credential_path and Path(credential_path).exists()),
    }
    _print_payload(payload)
    return 0 if payload["credentials_file_exists"] else 1


def cmd_validate_live(args: argparse.Namespace) -> int:
    result = run_live_validation(
        _repo(),
        args.dashboard_id,
        environment=get_settings().app_env,
        write_run=not args.no_write_run,
    )
    _print_payload(result.model_dump(mode="json"))
    return 0 if result.status == "passed" else 1


def cmd_governance_run(args: argparse.Namespace) -> int:
    result = run_autonomy_cycle(_repo(), args.dashboard_id, mode=args.mode)
    _print_payload(result.model_dump(mode="json"))
    return 0 if result.failed == 0 else 1


def cmd_governance_summary(args: argparse.Namespace) -> int:
    result = governance_summary(_repo(), args.dashboard_id)
    _print_payload(result.model_dump(mode="json"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operational CLI for the Kalshi autonomy API workspace.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    env_check = subparsers.add_parser("env-check", help="Validate local configuration and credential discovery.")
    env_check.set_defaults(func=cmd_env_check)

    validate_live = subparsers.add_parser("validate-live", help="Run live BigQuery/dashboard validation checks.")
    validate_live.add_argument("--dashboard-id", default=get_settings().default_dashboard_id)
    validate_live.add_argument("--no-write-run", action="store_true", help="Do not persist the validation run to BigQuery.")
    validate_live.set_defaults(func=cmd_validate_live)

    governance_run = subparsers.add_parser("governance-run", help="Run the dashboard autonomy cycle without HTTP.")
    governance_run.add_argument("--dashboard-id", default=get_settings().default_dashboard_id)
    governance_run.add_argument("--mode", default="manual_cli")
    governance_run.set_defaults(func=cmd_governance_run)

    governance_status = subparsers.add_parser("governance-summary", help="Print the governance summary without HTTP.")
    governance_status.add_argument("--dashboard-id", default=get_settings().default_dashboard_id)
    governance_status.set_defaults(func=cmd_governance_summary)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
