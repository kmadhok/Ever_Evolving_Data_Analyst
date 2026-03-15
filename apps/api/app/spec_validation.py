from __future__ import annotations

from typing import Iterable

from .models import DashboardSpec, DashboardSpecValidationResponse, TileSpec, ValidationIssue
from .policy import (
    AUTO_APPLY_MAX_REFRESH_SECONDS,
    AUTO_APPLY_MIN_REFRESH_SECONDS,
    APPROVED_AUTO_APPLY_DATASETS,
    dataset_is_auto_apply_approved,
)

VALID_ROLES = {"de", "analyst", "ds", "consumer"}


def _issue(severity: str, code: str, message: str, field: str | None = None) -> ValidationIssue:
    return ValidationIssue(severity=severity, code=code, message=message, field=field)


def _tile_dataset(default_dataset: str, tile: TileSpec) -> str:
    return tile.dataset or default_dataset


def validate_dashboard_spec(
    dashboard_id: str,
    spec: DashboardSpec,
    repository,
    *,
    enforce_auto_apply_rules: bool = True,
) -> DashboardSpecValidationResponse:
    issues: list[ValidationIssue] = []

    if spec.dashboard_id != dashboard_id:
        issues.append(
            _issue(
                "error",
                "dashboard_id_mismatch",
                f"Spec dashboard_id '{spec.dashboard_id}' does not match requested dashboard_id '{dashboard_id}'.",
                "dashboard_id",
            )
        )

    if spec.refresh_seconds < AUTO_APPLY_MIN_REFRESH_SECONDS or spec.refresh_seconds > AUTO_APPLY_MAX_REFRESH_SECONDS:
        issues.append(
            _issue(
                "error",
                "refresh_seconds_out_of_bounds",
                (
                    f"refresh_seconds must be between {AUTO_APPLY_MIN_REFRESH_SECONDS} and "
                    f"{AUTO_APPLY_MAX_REFRESH_SECONDS} for auto-apply."
                ),
                "refresh_seconds",
            )
        )

    seen_tile_ids: set[str] = set()
    role_counts = {role: 0 for role in VALID_ROLES}

    for idx, tile in enumerate(spec.tiles):
        tile_prefix = f"tiles[{idx}]"
        if tile.tile_id in seen_tile_ids:
            issues.append(
                _issue(
                    "error",
                    "duplicate_tile_id",
                    f"Duplicate tile_id '{tile.tile_id}' found.",
                    f"{tile_prefix}.tile_id",
                )
            )
        else:
            seen_tile_ids.add(tile.tile_id)

        invalid_roles = [role for role in tile.roles if role not in VALID_ROLES]
        if invalid_roles:
            issues.append(
                _issue(
                    "error",
                    "invalid_roles",
                    f"Tile '{tile.tile_id}' includes invalid roles: {', '.join(invalid_roles)}.",
                    f"{tile_prefix}.roles",
                )
            )
        for role in tile.roles:
            if role in role_counts:
                role_counts[role] += 1

        if tile.default_limit < 1 or tile.default_limit > repository.settings.max_tile_rows:
            issues.append(
                _issue(
                    "error",
                    "tile_limit_out_of_bounds",
                    (
                        f"Tile '{tile.tile_id}' default_limit must be between 1 and "
                        f"{repository.settings.max_tile_rows}."
                    ),
                    f"{tile_prefix}.default_limit",
                )
            )

        dataset = _tile_dataset(repository.settings.bq_dash_dataset, tile)
        if enforce_auto_apply_rules and not dataset_is_auto_apply_approved(dataset):
            issues.append(
                _issue(
                    "error",
                    "dataset_not_approved",
                    (
                        f"Tile '{tile.tile_id}' uses dataset '{dataset}', which is not in the approved "
                        f"auto-apply allowlist: {', '.join(sorted(APPROVED_AUTO_APPLY_DATASETS))}."
                    ),
                    f"{tile_prefix}.dataset",
                )
            )

        if not repository.view_exists(dataset, tile.view_name):
            issues.append(
                _issue(
                    "error",
                    "view_not_found",
                    f"Tile '{tile.tile_id}' references missing view '{dataset}.{tile.view_name}'.",
                    f"{tile_prefix}.view_name",
                )
            )
        elif tile.columns and tile.columns != ["*"]:
            missing_columns = repository.missing_columns(dataset, tile.view_name, tile.columns)
            if missing_columns:
                issues.append(
                    _issue(
                        "error",
                        "columns_not_found",
                        (
                            f"Tile '{tile.tile_id}' references missing columns in '{dataset}.{tile.view_name}': "
                            f"{', '.join(missing_columns)}."
                        ),
                        f"{tile_prefix}.columns",
                    )
                )

    for role, count in role_counts.items():
        if count == 0:
            issues.append(
                _issue(
                    "error",
                    "missing_role_coverage",
                    f"Spec leaves role '{role}' with zero visible tiles.",
                    "tiles",
                )
            )

    return DashboardSpecValidationResponse(
        dashboard_id=dashboard_id,
        valid=not any(issue.severity == "error" for issue in issues),
        issues=issues,
    )
