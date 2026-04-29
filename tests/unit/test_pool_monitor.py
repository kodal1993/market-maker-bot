from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dex.pool_monitor import PoolMonitor


class PoolMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_rpc_error_status_when_price_unavailable(self) -> None:
        monitor = object.__new__(PoolMonitor)
        monitor.fee_tier = 500
        monitor.min_interval_seconds = 8.0
        monitor._last_successful_pool_info = None
        monitor._last_successful_at = 0.0
        monitor._last_rpc_error = "429 Too Many Requests"

        monitor.get_current_price = AsyncMock(return_value=None)
        monitor.get_pool_liquidity = AsyncMock(return_value=123.0)
        monitor.get_volatility = AsyncMock(return_value=1.0)
        monitor.get_recent_volume = AsyncMock(return_value=10.0)

        info = await monitor.get_pool_info()

        self.assertEqual(info["status"], "rpc_error")
        self.assertEqual(info["error_reason"], "429 Too Many Requests")
        self.assertNotIn("price", info)

    async def test_reuses_cached_pool_info_within_min_interval(self) -> None:
        monitor = object.__new__(PoolMonitor)
        monitor.fee_tier = 500
        monitor.min_interval_seconds = 8.0
        monitor._last_rpc_error = None
        monitor._last_successful_pool_info = {
            "status": "ok",
            "price": 3000.0,
            "liquidity": 1000.0,
            "volatility": 0.5,
            "recent_volume": 50.0,
            "pool_fee": 500,
            "cached": False,
        }
        monitor._last_successful_at = 100.0

        with patch("dex.pool_monitor.time.time", return_value=105.0):
            info = await monitor.get_pool_info()

        self.assertEqual(info["status"], "ok")
        self.assertEqual(info["price"], 3000.0)
        self.assertTrue(info["cached"])


if __name__ == "__main__":
    unittest.main()
