from __future__ import annotations

import unittest

from app.policy import (
    AUTO_APPLY_MAX_REFRESH_SECONDS,
    AUTO_APPLY_MIN_REFRESH_SECONDS,
    POLICY_VERSION,
    dataset_is_auto_apply_approved,
    risk_level_for_priority,
)


class PolicyTests(unittest.TestCase):
    def test_policy_version_is_defined(self) -> None:
        self.assertEqual(POLICY_VERSION, "2026-03-beta-1")

    def test_priority_maps_to_risk(self) -> None:
        self.assertEqual(risk_level_for_priority("low"), "low")
        self.assertEqual(risk_level_for_priority("medium"), "medium")
        self.assertEqual(risk_level_for_priority("high"), "high")

    def test_dataset_allowlist(self) -> None:
        self.assertTrue(dataset_is_auto_apply_approved("kalshi_dash"))
        self.assertTrue(dataset_is_auto_apply_approved("kalshi_signal"))
        self.assertFalse(dataset_is_auto_apply_approved("kalshi_core"))

    def test_refresh_bounds(self) -> None:
        self.assertEqual(AUTO_APPLY_MIN_REFRESH_SECONDS, 30)
        self.assertEqual(AUTO_APPLY_MAX_REFRESH_SECONDS, 300)


if __name__ == "__main__":
    unittest.main()
