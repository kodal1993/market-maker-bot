from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from config import HIGH_VOL_THRESHOLD, MAX_SPREAD_BPS, MIN_SPREAD_BPS, SPREAD_BPS
from strategy import calculate_spread


class SpreadControlTests(unittest.TestCase):
    def test_low_volatility_narrows_and_high_volatility_widens_spread(self) -> None:
        low_spread = calculate_spread(0.0)
        normal_spread = calculate_spread(HIGH_VOL_THRESHOLD * 0.75)
        high_spread = calculate_spread(HIGH_VOL_THRESHOLD * 2.0)

        self.assertLessEqual(low_spread, SPREAD_BPS)
        self.assertGreater(normal_spread, low_spread)
        self.assertGreater(high_spread, normal_spread)

    def test_spread_respects_min_and_max_limits(self) -> None:
        self.assertEqual(calculate_spread(0.0, spread_multiplier=0.01), MIN_SPREAD_BPS)
        self.assertEqual(calculate_spread(HIGH_VOL_THRESHOLD * 10.0, spread_multiplier=10.0), MAX_SPREAD_BPS)


if __name__ == "__main__":
    unittest.main()
