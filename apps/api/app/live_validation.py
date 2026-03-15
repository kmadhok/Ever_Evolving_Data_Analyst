from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable

from .default_spec import default_dashboard_spec
from .models import LiveValidationRunRecord, ValidationIssue
from .repository import BigQueryRepository
from .spec_validation import validate_dashboard_spec

ROLE_NAMES = ("de", "analyst", "ds", "consumer")


def _issue(code: str, message: str, *, severity: str = "error", field: str | None = None) -> ValidationIssue:
    return ValidationIssue(severity=severity, code=code, message=message, field=field)


def run_live_validation(
    repository: BigQueryRepository,
    dashboard_id: str,
    *,
    environment: str,
    write_run: bool = True,
) -> LiveValidationRunRecord:
    issues: list[ValidationIssue] = []
    checks: dict[str, object] = {
        "datasets": {},
        "roles": {},
        "signal_feed": {},
    }

    datasets = [
        repository.settings.bq_dash_dataset,
        repository.settings.bq_signal_dataset,
        repository.settings.bq_ops_dataset,
        repository.settings.bq_core_dataset,
    ]
    for dataset in datasets:
        exists = repository.dataset_exists(dataset)
        checks["datasets"][dataset] = exists
        if not exists:
            issues.append(_issue("dataset_missing", f"Dataset '{dataset}' is not accessible in project '{repository.settings.gcp_project_id}'."))

    spec, source = repository.get_dashboard_spec(dashboard_id)
    validation = validate_dashboard_spec(dashboard_id, spec, repository)
    checks["spec_source"] = source
    checks["spec_valid"] = validation.valid
    issues.extend(validation.issues)

    default_validation = validate_dashboard_spec(dashboard_id, default_dashboard_spec(dashboard_id), repository)
    checks["default_spec_valid"] = default_validation.valid
    if not default_validation.valid:
        issues.extend(
            _issue(
                "default_spec_invalid_live",
                issue.message,
                severity=issue.severity,
                field=issue.field,
            )
            for issue in default_validation.issues
        )

    for role in ROLE_NAMES:
        role_tiles = [tile for tile in spec.tiles if role in tile.roles]
        role_result: dict[str, object] = {"tile_count": len(role_tiles), "tile_fetch": "skipped"}
        if not role_tiles:
            issues.append(_issue("role_has_no_tiles", f"Role '{role}' has no visible tiles in the active spec.", field=f"roles.{role}"))
            checks["roles"][role] = role_result
            continue
        first_tile = role_tiles[0]
        try:
            rows = repository.fetch_tile_rows(dashboard_id=dashboard_id, tile_id=first_tile.tile_id, limit=1)
            role_result["tile_fetch"] = "ok"
            role_result["sample_tile_id"] = first_tile.tile_id
            role_result["row_count"] = len(rows)
            if len(rows) == 0:
                issues.append(
                    _issue(
                        "role_tile_empty",
                        f"Role '{role}' sample tile '{first_tile.tile_id}' returned zero rows in live validation.",
                        severity="warning",
                        field=f"roles.{role}",
                    )
                )
        except Exception as exc:  # pragma: no cover - depends on live datasets
            role_result["tile_fetch"] = "error"
            role_result["sample_tile_id"] = first_tile.tile_id
            role_result["error"] = str(exc)
            issues.append(
                _issue(
                    "role_tile_fetch_failed",
                    f"Role '{role}' sample tile '{first_tile.tile_id}' failed to query: {exc}",
                    field=f"roles.{role}",
                )
            )
        checks["roles"][role] = role_result

    try:
        signal_rows = repository.fetch_signal_feed(limit=1)
        checks["signal_feed"] = {"status": "ok", "row_count": len(signal_rows)}
    except Exception as exc:  # pragma: no cover - depends on live datasets
        checks["signal_feed"] = {"status": "error", "error": str(exc)}
        issues.append(_issue("signal_feed_failed", f"Signal feed query failed: {exc}", field="signal_feed"))

    status = "passed" if not any(issue.severity == "error" for issue in issues) else "failed"
    record = LiveValidationRunRecord(
        validation_id=str(uuid.uuid4()),
        dashboard_id=dashboard_id,
        environment=environment,
        status=status,
        checked_at=datetime.now(timezone.utc),
        issues=issues,
        checks=checks,
        message="Live validation passed." if status == "passed" else "Live validation failed.",
    )
    if write_run:
        repository.record_live_validation_run(record)
    return record
