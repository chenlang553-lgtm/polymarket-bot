from datetime import datetime, timedelta, timezone
import unittest
import json
import tempfile
import os

from polymarket_bot.archive import JsonlWriter, WindowArchiveWriter
from polymarket_bot.config import StrategyConfig, load_config
from polymarket_bot.gamma import build_market_slug
from polymarket_bot.market_state import RollingState
from polymarket_bot.models import BestBidAsk, OutcomeSide, Position, SignalAction
from polymarket_bot.replay import format_replay_line, run_replay
from polymarket_bot.report import build_report
from polymarket_bot.strategy import StrategyEngine, default_size_buckets
from polymarket_bot.validate import validate_config


def _build_state(prices):
    config = StrategyConfig(size_buckets=default_size_buckets())
    state = RollingState(config)
    for price in prices:
        state.update_price(price)
    return state


class StrategyTests(unittest.TestCase):
    def test_load_config_applies_profile_overrides(self):
        handle, path = tempfile.mkstemp()
        os.close(handle)
        try:
            with open(path, "w") as saved:
                saved.write(
                    json.dumps(
                        {
                            "market": {},
                            "price_feed": {},
                            "strategy": {"min_edge": 0.04},
                            "execution": {"strategy_profile": "main"},
                            "wallet": {},
                            "logging": {},
                            "profiles": {"tight": {"strategy": {"min_edge": 0.07}}},
                        }
                    )
                )
            config = load_config(path, profile="tight")
            self.assertEqual(config.strategy.min_edge, 0.07)
            self.assertEqual(config.execution.strategy_profile, "tight")
        finally:
            os.unlink(path)

    def test_validate_config_catches_invalid_windows(self):
        handle, path = tempfile.mkstemp()
        os.close(handle)
        try:
            with open(path, "w") as saved:
                saved.write(
                    json.dumps(
                        {
                            "market": {"slug_prefix": "btc-updown-5m"},
                            "price_feed": {"symbol": "btcusdt"},
                            "strategy": {
                                "decision_window_start_seconds": 5,
                                "decision_window_end_seconds": 10,
                                "min_edge": 0.04,
                                "max_spread": 0.03,
                                "min_top_of_book_size": 1
                            },
                            "execution": {"mode": "paper"},
                            "wallet": {},
                            "logging": {
                                "window_close_path": "window_close.jsonl",
                                "activity_path": "activity.jsonl",
                                "market_state_path": "market_state.jsonl"
                            }
                        }
                    )
                )
            config = load_config(path)
            result = validate_config(config)
            self.assertTrue(result["errors"])
        finally:
            os.unlink(path)

    def test_validate_accepts_health_logging_fields(self):
        handle, path = tempfile.mkstemp()
        os.close(handle)
        try:
            with open(path, "w") as saved:
                saved.write(
                    json.dumps(
                        {
                            "market": {"slug_prefix": "btc-updown-5m"},
                            "price_feed": {"symbol": "btcusdt"},
                            "strategy": {
                                "decision_window_start_seconds": 45,
                                "decision_window_end_seconds": 8,
                                "min_edge": 0.04,
                                "max_spread": 0.03,
                                "min_top_of_book_size": 1
                            },
                            "execution": {"mode": "paper"},
                            "wallet": {},
                            "logging": {
                                "window_close_path": "window_close.jsonl",
                                "activity_path": "activity.jsonl",
                                "market_state_path": "market_state.jsonl",
                                "health_log_interval_seconds": 15,
                                "stale_data_threshold_seconds": 10
                            }
                        }
                    )
                )
            config = load_config(path)
            result = validate_config(config)
            self.assertFalse(result["errors"])
        finally:
            os.unlink(path)

    def test_build_report_summarizes_windows(self):
        report = build_report(
            [
                {
                    "strategyProfile": "main",
                    "strategyType": "fair_probability",
                    "realizedPnl": 1.5,
                    "closedAtMs": 1773407099026,
                    "activity": {"fillCount": 2},
                },
                {
                    "strategyProfile": "tight",
                    "strategyType": None,
                    "realizedPnl": -0.5,
                    "closedAtMs": 1773407399005,
                    "activity": {"fillCount": 0},
                },
            ]
        )
        self.assertEqual(report["summary"]["total_windows"], 2)
        self.assertEqual(report["summary"]["traded_windows"], 1)
        self.assertAlmostEqual(report["summary"]["total_realized_pnl"], 1.0)
        self.assertEqual(report["by_strategy"]["fair_probability"]["traded"], 1)
        self.assertEqual(report["by_profile"]["main"]["traded"], 1)

    def test_archive_writer_appends_jsonl_records(self):
        handle, path = tempfile.mkstemp()
        os.close(handle)
        try:
            writer = WindowArchiveWriter(path)
            writer.write({"recordType": "window_close", "marketSlug": "x"})
            with open(path) as saved:
                payload = json.loads(saved.readline())
            self.assertEqual(payload["marketSlug"], "x")
        finally:
            os.unlink(path)

    def test_jsonl_writer_appends_records(self):
        handle, path = tempfile.mkstemp()
        os.close(handle)
        try:
            writer = JsonlWriter(path)
            writer.write({"recordType": "activity", "action": "open"})
            with open(path) as saved:
                payload = json.loads(saved.readline())
            self.assertEqual(payload["action"], "open")
        finally:
            os.unlink(path)

    def test_replay_formats_state_records(self):
        line = format_replay_line(
            {
                "recordType": "state",
                "marketSlug": "btc-updown-5m-1773406800",
                "timeToExpirySec": 12,
                "spot": 82134.25,
                "yesBid": 0.46,
                "yesAsk": 0.47,
                "fairYes": 0.512,
                "fairNo": 0.488,
                "edgeYes": 0.042,
                "edgeNo": -0.052,
                "position": "flat",
            }
        )
        self.assertTrue(line.startswith("STATE window=btc-updown-5m-1773406800"))

    def test_run_replay_reads_jsonl_records(self):
        handle, path = tempfile.mkstemp()
        os.close(handle)
        try:
            writer = JsonlWriter(path)
            writer.write({"recordType": "window", "marketSlug": "x", "phase": "activated", "startTime": "a", "endTime": "b"})
            output = run_replay(path)
            self.assertIn("WINDOW window=x phase=activated", output)
        finally:
            os.unlink(path)

    def test_build_market_slug_rounds_to_5m_boundary(self):
        from datetime import datetime, timezone

        moment = datetime(2026, 3, 13, 13, 2, 41, tzinfo=timezone.utc)
        self.assertEqual(build_market_slug("btc-updown-5m", moment), "btc-updown-5m-1773406800")

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
