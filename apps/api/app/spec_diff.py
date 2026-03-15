from __future__ import annotations

from .models import DashboardSpec, RefreshSecondsChange, RoleVisibilityChange, SpecDiff, TileLimitChange, TileMove


def diff_specs(current: DashboardSpec, candidate: DashboardSpec) -> SpecDiff:
    diff = SpecDiff()

    current_positions = {tile.tile_id: idx for idx, tile in enumerate(current.tiles)}
    current_tiles = {tile.tile_id: tile for tile in current.tiles}

    for idx, tile in enumerate(candidate.tiles):
        if tile.tile_id in current_positions and current_positions[tile.tile_id] != idx:
            diff.move_tiles.append(
                TileMove(tile_id=tile.tile_id, from_index=current_positions[tile.tile_id], to_index=idx)
            )

        current_tile = current_tiles.get(tile.tile_id)
        if current_tile is None:
            diff.notes.append(f"added_tile:{tile.tile_id}")
            continue

        current_roles = set(current_tile.roles)
        candidate_roles = set(tile.roles)
        for role in sorted(current_roles | candidate_roles):
            if (role in current_roles) != (role in candidate_roles):
                diff.role_visibility_changes.append(
                    RoleVisibilityChange(tile_id=tile.tile_id, role=role, visible=role in candidate_roles)
                )

        if current_tile.default_limit != tile.default_limit:
            diff.tile_limit_changes.append(
                TileLimitChange(tile_id=tile.tile_id, **{"from": current_tile.default_limit, "to": tile.default_limit})
            )

    if current.refresh_seconds != candidate.refresh_seconds:
        diff.update_refresh_seconds = RefreshSecondsChange(
            **{"from": current.refresh_seconds, "to": candidate.refresh_seconds}
        )

    return diff
