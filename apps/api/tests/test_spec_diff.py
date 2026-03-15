from __future__ import annotations

import unittest

from app.default_spec import default_dashboard_spec
from app.spec_diff import diff_specs


class SpecDiffTests(unittest.TestCase):
    def test_detects_tile_move_and_refresh_change(self) -> None:
        current = default_dashboard_spec()
        candidate = current.model_copy(deep=True)
        tile = candidate.tiles.pop(2)
        candidate.tiles.insert(0, tile)
        candidate.refresh_seconds = 30

        diff = diff_specs(current, candidate)
        self.assertTrue(any(move.tile_id == tile.tile_id for move in diff.move_tiles))
        self.assertIsNotNone(diff.update_refresh_seconds)
        self.assertEqual(diff.update_refresh_seconds.to_seconds, 30)


if __name__ == "__main__":
    unittest.main()
