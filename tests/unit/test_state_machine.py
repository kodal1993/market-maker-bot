from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from state_machine import StateMachineEngine
from types_bot import ReentryState, StateMachineContext, StrategyState


class StateMachineEngineTests(unittest.TestCase):
    def test_sync_cycle_exits_when_elapsed_reaches_configured_cooldown(self) -> None:
        engine = StateMachineEngine(cycle_seconds=60.0)
        context = StateMachineContext(
            current_state=StrategyState.COOLDOWN,
            previous_state=StrategyState.ACCUMULATING.value,
            entered_cycle=5,
            transition_reason="loss_streak_limit",
            last_transition="ACCUMULATING->COOLDOWN:loss_streak_limit",
            last_transition_cycle=5,
            cooldown_until_cycle=999,
            entering_cooldown_reason="loss_streak_limit",
        )

        with (
            patch("state_machine.STATE_MACHINE_COOLDOWN_MINUTES", 1.0),
            patch("state_machine.STATE_MACHINE_MAX_COOLDOWN_MINUTES", 0.0),
        ):
            engine.sync_cycle(
                context=context,
                cycle_index=6,
                reentry_state=ReentryState(),
                portfolio_eth=0.0,
                min_eth_reserve=0.0,
            )

        self.assertEqual(context.current_state, StrategyState.IDLE)
        self.assertEqual(context.cooldown_exit_reason, "cooldown_elapsed")
        self.assertAlmostEqual(context.last_cooldown_elapsed_seconds, 60.0)
        self.assertIsNone(context.cooldown_until_cycle)

    def test_sync_cycle_forces_exit_after_max_cooldown_timeout(self) -> None:
        engine = StateMachineEngine(cycle_seconds=60.0)
        context = StateMachineContext(current_state=StrategyState.ACCUMULATING)

        with (
            patch("state_machine.STATE_MACHINE_COOLDOWN_MINUTES", 30.0),
            patch("state_machine.STATE_MACHINE_MAX_COOLDOWN_MINUTES", 2.0),
            patch("state_machine.STATE_MACHINE_LOSS_STREAK_LIMIT", 3),
        ):
            entered = engine.maybe_enter_cooldown(context, cycle_index=10, loss_streak=3)

            self.assertTrue(entered)
            self.assertEqual(context.current_state, StrategyState.COOLDOWN)
            self.assertEqual(context.entering_cooldown_reason, "loss_streak_limit")
            self.assertEqual(engine.cooldown_remaining_cycles(context, 11), 1)

            engine.sync_cycle(
                context=context,
                cycle_index=12,
                reentry_state=ReentryState(),
                portfolio_eth=0.0,
                min_eth_reserve=0.0,
            )

        self.assertEqual(context.current_state, StrategyState.IDLE)
        self.assertEqual(context.cooldown_exit_reason, "max_cooldown_timeout")
        self.assertAlmostEqual(context.last_cooldown_elapsed_seconds, 120.0)

    def test_serialize_includes_cooldown_audit_fields(self) -> None:
        engine = StateMachineEngine(cycle_seconds=60.0)
        context = StateMachineContext(
            current_state=StrategyState.COOLDOWN,
            previous_state=StrategyState.ACCUMULATING.value,
            entered_cycle=7,
            transition_reason="loss_streak_limit",
            last_transition="ACCUMULATING->COOLDOWN:loss_streak_limit",
            last_transition_cycle=7,
            cooldown_until_cycle=12,
            entering_cooldown_reason="loss_streak_limit",
        )

        payload = json.loads(engine.serialize(context, cycle_index=9))

        self.assertEqual(payload["entering_cooldown_reason"], "loss_streak_limit")
        self.assertEqual(payload["cooldown_elapsed_sec"], 120.0)
        self.assertEqual(payload["cooldown_exit_reason"], "")


if __name__ == "__main__":
    unittest.main()
