from datetime import datetime, timedelta, timezone
import unittest

from polymarket_bot.config import StrategyConfig
from polymarket_bot.market_state import RollingState
from polymarket_bot.models import BestBidAsk, OutcomeSide, Position, SignalAction
from polymarket_bot.strategy import StrategyEngine, default_size_buckets


def _build_state(prices):
    config = StrategyConfig(size_buckets=default_size_buckets())
    state = RollingState(config)
    for price in prices:
        state.update_price(price)
    return state


class StrategyTests(unittest.TestCase):
    def test_open_signal_when_edge_is_large(self):
        engine = StrategyEngine(StrategyConfig(size_buckets=default_size_buckets()))
        state = _build_state([100.0 + i * 0.2 for i in range(40)])
        book = BestBidAsk(asset_id="yes", bid=0.45, ask=0.47, bid_size=100.0, ask_size=100.0)
        snapshot = engine.compute_snapshot(state, book, tau_seconds=20)
        signal = engine.evaluate(snapshot, book, position=None)
        self.assertEqual(signal.action, SignalAction.OPEN)
        self.assertEqual(signal.side, OutcomeSide.YES)

    def test_hold_when_spread_is_too_wide(self):
        engine = StrategyEngine(StrategyConfig(size_buckets=default_size_buckets()))
        state = _build_state([100.0 + (i % 2) * 0.05 for i in range(40)])
        book = BestBidAsk(asset_id="yes", bid=0.40, ask=0.50, bid_size=100.0, ask_size=100.0)
        snapshot = engine.compute_snapshot(state, book, tau_seconds=20)
        signal = engine.evaluate(snapshot, book, position=None)
        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertEqual(signal.reason, "spread_too_wide")

    def test_close_when_edge_decays(self):
        engine = StrategyEngine(StrategyConfig(size_buckets=default_size_buckets()))
        state = _build_state([100.0 + i * 0.12 for i in range(40)])
        book = BestBidAsk(asset_id="yes", bid=0.49, ask=0.50, bid_size=100.0, ask_size=100.0)
        snapshot = engine.compute_snapshot(state, book, tau_seconds=25)
        position = Position(
            side=OutcomeSide.YES,
            size=1.0,
            entry_price=0.40,
            edge_at_entry=1.0,
            opened_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        )
        signal = engine.evaluate(snapshot, book, position=position)
        self.assertEqual(signal.action, SignalAction.CLOSE)
        self.assertEqual(signal.reason, "edge_decayed")


if __name__ == "__main__":
    unittest.main()
