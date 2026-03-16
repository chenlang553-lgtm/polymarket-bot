from datetime import datetime, timedelta, timezone
import unittest
import json
import tempfile
import os

from polymarket_bot.app import TradingApplication
from polymarket_bot.archive import JsonlWriter, WindowArchiveWriter
from polymarket_bot.config import ExecutionConfig, StrategyConfig, load_config
from polymarket_bot.gamma import build_market_slug
from polymarket_bot.market_state import RollingState
from polymarket_bot.models import BestBidAsk, OutcomeSide, Position, RuntimeState, SignalAction, StrategySnapshot, TradeSignal, WindowStats
from polymarket_bot.replay import format_replay_line, run_replay
from polymarket_bot.report import build_report
from polymarket_bot.strategy import StrategyEngine, default_size_buckets
from polymarket_bot.validate import validate_config
from polymarket_bot.ws import _parse_book_like_message, _parse_price_change_messages


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
                            "price_feed": {"provider": "binance"},
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
                            "price_feed": {"symbol": "btcusdt", "provider": "binance"},
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
                            "price_feed": {"symbol": "btcusdt", "provider": "binance"},
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
                                "stale_data_threshold_seconds": 10,
                                "shutdown_grace_seconds": 5,
                                "supervisor_restart_backoff_seconds": 2
                            }
                        }
                    )
                )
            config = load_config(path)
            result = validate_config(config)
            self.assertFalse(result["errors"])
        finally:
            os.unlink(path)

    def test_validate_rejects_invalid_market_order_price_buffer(self):
        handle, path = tempfile.mkstemp()
        os.close(handle)
        try:
            with open(path, "w") as saved:
                saved.write(
                    json.dumps(
                        {
                            "market": {"slug_prefix": "btc-updown-5m"},
                            "price_feed": {"symbol": "btcusdt", "provider": "binance"},
                            "strategy": {
                                "decision_window_start_seconds": 45,
                                "decision_window_end_seconds": 8,
                                "min_edge": 0.04,
                                "max_spread": 0.03,
                                "min_top_of_book_size": 1
                            },
                            "execution": {"mode": "paper", "market_order_price_buffer": -0.01},
                            "wallet": {},
                            "logging": {
                                "window_close_path": "window_close.jsonl",
                                "activity_path": "activity.jsonl",
                                "market_state_path": "market_state.jsonl",
                                "health_log_interval_seconds": 15,
                                "stale_data_threshold_seconds": 10,
                                "shutdown_grace_seconds": 5,
                                "supervisor_restart_backoff_seconds": 2
                            }
                        }
                    )
                )
            config = load_config(path)
            result = validate_config(config)
            self.assertTrue(result["errors"])
            self.assertIn("execution.market_order_price_buffer must be between 0 and 1", result["errors"])
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

    def test_strategy_smooths_fair_value_in_last_seconds(self):
        config = StrategyConfig(
            fair_smoothing_start_seconds=20,
            fair_smoothing_alpha=0.2,
            size_buckets=default_size_buckets(),
        )
        engine = StrategyEngine(config)
        state = _build_state([100.0 + i * 0.2 for i in range(40)])
        yes_book = BestBidAsk(asset_id="yes", bid=0.45, ask=0.47, bid_size=100.0, ask_size=100.0)
        no_book = BestBidAsk(asset_id="no", bid=0.53, ask=0.55, bid_size=100.0, ask_size=100.0)
        snapshot = engine.compute_snapshot(state, yes_book, no_book, tau_seconds=15, previous_fair_yes=0.5)
        self.assertTrue(0.5 < snapshot.fair_yes < 1.0)

    def test_sigma_floor_prevents_extreme_probability_from_tiny_move(self):
        config = StrategyConfig(sigma_floor=0.00005, size_buckets=default_size_buckets())
        engine = StrategyEngine(config)
        state = _build_state([100.0] * 80)
        state.open_price = 100.0
        state.last_x = 0.0001
        yes_book = BestBidAsk(asset_id="yes", bid=0.45, ask=0.47, bid_size=100.0, ask_size=100.0)
        no_book = BestBidAsk(asset_id="no", bid=0.53, ask=0.55, bid_size=100.0, ask_size=100.0)

        snapshot = engine.compute_snapshot(state, yes_book, no_book, tau_seconds=30)

        self.assertLess(snapshot.fair_yes, 0.8)

    def test_best_bid_ask_merges_with_fallback(self):
        primary = BestBidAsk(asset_id="yes", bid=None, ask=None, bid_size=0.0, ask_size=0.0, timestamp_ms=10)
        fallback = BestBidAsk(asset_id="yes", bid=0.45, ask=0.47, bid_size=10.0, ask_size=12.0, timestamp_ms=9)
        merged = primary.merged_with(fallback)
        self.assertEqual(merged.bid, 0.45)
        self.assertEqual(merged.ask, 0.47)

    def test_compute_snapshot_uses_market_prices_with_fair_cap(self):
        config = StrategyConfig(size_buckets=default_size_buckets())
        engine = StrategyEngine(config)
        state = _build_state([100.0 + i * 0.1 for i in range(80)])
        yes_book = BestBidAsk(asset_id="yes", bid=0.45, ask=0.47, bid_size=100.0, ask_size=100.0)
        no_book = BestBidAsk(asset_id="no", bid=0.53, ask=0.55, bid_size=100.0, ask_size=100.0)

        snapshot = engine.compute_snapshot(state, yes_book, no_book, tau_seconds=20)

        self.assertAlmostEqual(snapshot.yes_price, 0.46)
        self.assertAlmostEqual(snapshot.no_price, 0.54)
        self.assertAlmostEqual(snapshot.edge_yes, snapshot.fair_yes - 0.46)
        self.assertAlmostEqual(snapshot.edge_no, snapshot.fair_no - 0.54)
        self.assertLessEqual(snapshot.fair_yes, config.fair_value_cap)

    def test_evaluate_requires_both_prices_when_enabled(self):
        config = StrategyConfig(require_both_prices=True, min_abs_x=0.0, size_buckets=default_size_buckets())
        engine = StrategyEngine(config)
        snapshot = StrategySnapshot(
            fair_yes=0.8,
            fair_no=0.2,
            yes_price=0.4,
            no_price=None,
            edge_yes=0.4,
            edge_no=None,
            sigma_10=0.0,
            sigma_30=0.0,
            sigma_slow=0.0,
            sigma_eff=0.0,
            momentum_5=0.0,
            momentum_15=0.0,
            drift=0.0,
            x_t=0.0,
            tau_seconds=20,
            jump_adjusted=False,
            outlier_adjusted=False,
        )
        yes_book = BestBidAsk(asset_id="yes", bid=0.39, ask=0.41, bid_size=100.0, ask_size=100.0)
        no_book = BestBidAsk(asset_id="no", bid=None, ask=None, bid_size=0.0, ask_size=0.0)

        signal = engine.evaluate(snapshot, yes_book, no_book, position=None)

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertEqual(signal.reason, "incomplete_market_prices")

    def test_positive_edge_decay_does_not_force_close(self):
        config = StrategyConfig(edge_decay_close_threshold=0.0, min_abs_x=0.00025, size_buckets=default_size_buckets())
        engine = StrategyEngine(config)
        snapshot = StrategySnapshot(
            fair_yes=0.7,
            fair_no=0.3,
            yes_price=0.62,
            no_price=0.38,
            edge_yes=0.08,
            edge_no=-0.08,
            sigma_10=0.0,
            sigma_30=0.0,
            sigma_slow=0.0,
            sigma_eff=0.0,
            momentum_5=0.0,
            momentum_15=0.0,
            drift=0.0,
            x_t=0.0,
            tau_seconds=20,
            jump_adjusted=False,
            outlier_adjusted=False,
        )
        yes_book = BestBidAsk(asset_id="yes", bid=0.61, ask=0.63, bid_size=100.0, ask_size=100.0)
        no_book = BestBidAsk(asset_id="no", bid=0.37, ask=0.39, bid_size=100.0, ask_size=100.0)
        position = Position(side=OutcomeSide.YES, size=1.0, entry_price=0.50, edge_at_entry=0.40, opened_at=datetime.now(timezone.utc))

        signal = engine.evaluate(snapshot, yes_book, no_book, position=position)

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertEqual(signal.reason, "position_unchanged")

    def test_open_is_blocked_inside_no_trade_zone(self):
        config = StrategyConfig(min_abs_x=0.00025, require_both_prices=True, size_buckets=default_size_buckets())
        engine = StrategyEngine(config)
        snapshot = StrategySnapshot(
            fair_yes=0.9,
            fair_no=0.1,
            yes_price=0.5,
            no_price=0.5,
            edge_yes=0.4,
            edge_no=-0.4,
            sigma_10=0.0,
            sigma_30=0.0,
            sigma_slow=0.0,
            sigma_eff=0.0,
            momentum_5=0.0,
            momentum_15=0.0,
            drift=0.0,
            x_t=0.0001,
            tau_seconds=20,
            jump_adjusted=False,
            outlier_adjusted=False,
        )
        yes_book = BestBidAsk(asset_id="yes", bid=0.49, ask=0.51, bid_size=100.0, ask_size=100.0)
        no_book = BestBidAsk(asset_id="no", bid=0.49, ask=0.51, bid_size=100.0, ask_size=100.0)

        signal = engine.evaluate(snapshot, yes_book, no_book, position=None)

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertEqual(signal.reason, "inside_no_trade_zone")

    def test_execution_price_does_not_fall_back_to_bid(self):
        ask_book = BestBidAsk(asset_id="yes", bid=0.45, ask=0.47, last_trade_price=0.40)
        self.assertEqual(ask_book.execution_price(), 0.47)

        trade_book = BestBidAsk(asset_id="yes", bid=None, ask=None, last_trade_price=0.41)
        self.assertEqual(trade_book.execution_price(), 0.41)

        bid_only_book = BestBidAsk(asset_id="yes", bid=0.39, ask=None)
        self.assertEqual(bid_only_book.execution_price(), 0.39)

    def test_execution_price_uses_max_of_signal_and_best_ask(self):
        book = BestBidAsk(asset_id="yes", bid=0.45, ask=0.47, last_trade_price=0.40)
        self.assertEqual(book.execution_price_for(0.46), 0.47)
        self.assertEqual(book.execution_price_for(0.52), 0.52)

    def test_execution_price_applies_live_buffer_and_rounds_up_to_tick(self):
        book = BestBidAsk(asset_id="yes", bid=0.45, ask=0.47, last_trade_price=0.40, tick_size=0.01)
        self.assertEqual(book.execution_price_for(0.46, price_buffer=0.001, tick_size=0.01), 0.48)
        self.assertEqual(book.execution_price_for(0.52, price_buffer=0.001, tick_size=0.01), 0.53)

    def test_effective_book_rejects_stale_quotes(self):
        app = TradingApplication.__new__(TradingApplication)

        class _Strategy:
            book_fallback_max_age_seconds = 3

        class _Config:
            strategy = _Strategy()

        app.config = _Config()

        now = datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc)
        fresh_ms = int(now.timestamp() * 1000) - 1000
        stale_ms = int(now.timestamp() * 1000) - 10000

        app._latest_books = {
            "yes": BestBidAsk(asset_id="yes", bid=0.45, ask=0.47, bid_size=5, ask_size=5, timestamp_ms=stale_ms)
        }
        app._last_usable_books = {
            "yes": BestBidAsk(asset_id="yes", bid=0.46, ask=0.48, bid_size=5, ask_size=5, timestamp_ms=fresh_ms)
        }

        chosen = app._effective_book("yes", now)
        self.assertEqual(chosen.ask, 0.48)

        app._latest_books = {
            "yes": BestBidAsk(asset_id="yes", bid=0.45, ask=0.47, bid_size=5, ask_size=5, timestamp_ms=stale_ms)
        }
        app._last_usable_books = {
            "yes": BestBidAsk(asset_id="yes", bid=0.46, ask=0.48, bid_size=5, ask_size=5, timestamp_ms=stale_ms)
        }

        chosen = app._effective_book("yes", now)
        self.assertIsNone(chosen.ask)
        self.assertIsNone(chosen.bid)

    def test_effective_book_merges_partial_latest_with_cached_book(self):
        app = TradingApplication.__new__(TradingApplication)

        class _Strategy:
            book_fallback_max_age_seconds = 5

        class _Config:
            strategy = _Strategy()

        app.config = _Config()

        now = datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc)
        recent_ms = int(now.timestamp() * 1000) - 1000

        app._latest_books = {
            "yes": BestBidAsk(asset_id="yes", bid=0.44, ask=None, bid_size=5, ask_size=0, timestamp_ms=recent_ms)
        }
        app._last_usable_books = {
            "yes": BestBidAsk(asset_id="yes", bid=0.43, ask=0.47, bid_size=5, ask_size=7, timestamp_ms=recent_ms)
        }

        chosen = app._effective_book("yes", now)
        self.assertEqual(chosen.bid, 0.44)
        self.assertEqual(chosen.ask, 0.47)

    def test_cache_book_merges_partial_update_with_previous_book(self):
        app = TradingApplication.__new__(TradingApplication)
        app._last_usable_books = {
            "yes": BestBidAsk(asset_id="yes", bid=0.43, ask=0.47, bid_size=5, ask_size=7, timestamp_ms=100)
        }

        partial = BestBidAsk(asset_id="yes", bid=0.44, ask=None, bid_size=6, ask_size=0, timestamp_ms=200)
        app._maybe_cache_book("yes", partial)

        cached = app._last_usable_books["yes"]
        self.assertEqual(cached.bid, 0.44)
        self.assertEqual(cached.ask, 0.47)

    def test_apply_signal_risk_controls_blocks_same_side_reentry(self):
        app = TradingApplication.__new__(TradingApplication)

        class _Strategy:
            require_both_prices = True
            max_entries_per_window = 2
            max_flips_per_window = 1
            allow_same_side_reentry = False

        class _Config:
            strategy = _Strategy()

        app.config = _Config()
        app._window_entry_count = 1
        app._window_flip_count = 0
        app._seen_entry_sides = {OutcomeSide.YES}
        snapshot = StrategySnapshot(
            fair_yes=0.7,
            fair_no=0.3,
            yes_price=0.45,
            no_price=0.55,
            edge_yes=0.25,
            edge_no=-0.25,
            sigma_10=0.0,
            sigma_30=0.0,
            sigma_slow=0.0,
            sigma_eff=0.0,
            momentum_5=0.0,
            momentum_15=0.0,
            drift=0.0,
            x_t=0.0,
            tau_seconds=20,
            jump_adjusted=False,
            outlier_adjusted=False,
        )
        signal = TradeSignal(SignalAction.OPEN, side=OutcomeSide.YES, size=0.5, reason="open_edge_signal", snapshot=snapshot)

        blocked = app._apply_signal_risk_controls(signal)

        self.assertEqual(blocked.action, SignalAction.HOLD)
        self.assertEqual(blocked.reason, "same_side_reentry_blocked")

    def test_live_close_position_posts_sell_order(self):
        from polymarket_bot.execution import LiveExecutor

        posted = []

        class FakeClient:
            def create_market_order(self, order):
                return {"signed": order}

            def post_order(self, signed, order_type):
                posted.append((signed, order_type))

        class FakeOrder:
            def __init__(self, token_id, amount, side, price=None, order_type=None):
                self.token_id = token_id
                self.amount = amount
                self.side = side
                self.price = price
                self.order_type = order_type

        class FakeOrderType:
            FOK = "fok"
        class FakeOptions:
            def __init__(self, tick_size=None, neg_risk=None):
                self.tick_size = tick_size
                self.neg_risk = neg_risk

        executor = LiveExecutor.__new__(LiveExecutor)
        executor.execution = type("Exec", (), {"order_type": "fok", "fixed_order_notional": 1.0})()
        executor._market_order_args_cls = FakeOrder
        executor._order_type_cls = FakeOrderType
        executor._partial_create_order_options_cls = FakeOptions
        executor._sell_constant = "SELL"
        executor._client = FakeClient()
        executor._can_derive = False

        market = type("Market", (), {"yes_token_id": "Y", "no_token_id": "N", "tick_size": 0.01, "neg_risk": False})()
        position = Position(side=OutcomeSide.NO, size=1.25, entry_price=0.6, edge_at_entry=0.08, opened_at=datetime.now(timezone.utc))

        executor.close_position(market, position)

        self.assertEqual(len(posted), 1)
        signed, order_type = posted[0]
        self.assertEqual(order_type, "fok")
        self.assertEqual(signed["signed"].token_id, "N")
        self.assertEqual(signed["signed"].amount, 1.25)
        self.assertEqual(signed["signed"].side, "SELL")

    def test_live_open_uses_notional_for_market_buy_amount(self):
        from polymarket_bot.execution import LiveExecutor

        submitted = []

        executor = LiveExecutor.__new__(LiveExecutor)
        executor.execution = type("Exec", (), {"order_type": "fok", "fixed_order_notional": 1.0})()
        executor._can_derive = False
        executor._buy_constant = "BUY"
        executor._submit_with_auth_retry = lambda market, token_id, amount, side, price: submitted.append(
            (token_id, amount, side, price)
        ) or {"success": True, "status": "filled", "filled_size": 50.0, "avg_price": 0.02}

        market = type("Market", (), {"yes_token_id": "Y", "no_token_id": "N", "tick_size": 0.01, "neg_risk": False})()
        signal = type("Signal", (), {
            "side": OutcomeSide.YES,
            "size": 1.0,
            "reason": "test",
            "snapshot": type("Snap", (), {"edge_yes": 0.1, "edge_no": -0.1})(),
        })()

        position = executor.open_position(market, signal, 0.02)

        self.assertEqual(len(submitted), 1)
        token_id, amount, side, price = submitted[0]
        self.assertEqual(token_id, "Y")
        self.assertEqual(side, "BUY")
        self.assertAlmostEqual(amount, 1.0)
        self.assertAlmostEqual(price, 0.02)
        self.assertAlmostEqual(position.size, 50.0)
        self.assertAlmostEqual(executor.last_report["requested_notional"], 1.0)
        self.assertAlmostEqual(executor.last_report["requested_size"], 50.0)

    def test_book_message_uses_true_best_bid_and_ask(self):
        book = _parse_book_like_message(
            {
                "event_type": "book",
                "asset_id": "yes",
                "bids": [
                    {"price": "0.41", "size": "5"},
                    {"price": "0.45", "size": "2"},
                    {"price": "0.43", "size": "4"},
                ],
                "asks": [
                    {"price": "0.52", "size": "3"},
                    {"price": "0.49", "size": "7"},
                    {"price": "0.51", "size": "1"},
                ],
                "last_trade_price": "0.48",
                "tick_size": "0.01",
                "timestamp": "123",
            },
            "yes",
        )
        self.assertEqual(book.bid, 0.45)
        self.assertEqual(book.ask, 0.49)
        self.assertEqual(book.last_trade_price, 0.48)

    def test_best_bid_ask_message_is_parsed(self):
        book = _parse_book_like_message(
            {
                "event_type": "best_bid_ask",
                "asset_id": "yes",
                "best_bid": "0.41",
                "best_ask": "0.43",
                "tick_size": "0.01",
                "timestamp": "123",
            },
            "yes",
        )
        self.assertEqual(book.bid, 0.41)
        self.assertEqual(book.ask, 0.43)
        self.assertEqual(book.last_trade_price, None)

    def test_price_change_messages_are_parsed(self):
        books = list(
            _parse_price_change_messages(
                {
                    "event_type": "price_change",
                    "timestamp": "123",
                    "price_changes": [
                        {"asset_id": "yes", "price": "0.44", "best_bid": "0.43", "best_ask": "0.45"},
                        {"asset_id": "no", "price": "0.56", "best_bid": "0.55", "best_ask": "0.57"},
                    ],
                },
                ["yes", "no"],
            )
        )
        self.assertEqual(len(books), 2)
        self.assertEqual(books[0].asset_id, "yes")
        self.assertEqual(books[0].last_trade_price, 0.44)
        self.assertEqual(books[0].bid, 0.43)
        self.assertEqual(books[0].ask, 0.45)

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
                "yesPrice": 0.465,
                "noPrice": 0.535,
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
        yes_book = BestBidAsk(asset_id="yes", bid=0.45, ask=0.47, bid_size=100.0, ask_size=100.0)
        no_book = BestBidAsk(asset_id="no", bid=0.53, ask=0.55, bid_size=100.0, ask_size=100.0)
        snapshot = engine.compute_snapshot(state, yes_book, no_book, tau_seconds=20)
        signal = engine.evaluate(snapshot, yes_book, no_book, position=None)
        self.assertEqual(signal.action, SignalAction.OPEN)
        self.assertEqual(signal.side, OutcomeSide.YES)
        self.assertEqual(signal.size, 1.0)

    def test_price_band_sizing_uses_cheap_contract_size(self):
        engine = StrategyEngine(StrategyConfig(min_abs_x=0.0, size_buckets=default_size_buckets()))
        snapshot = StrategySnapshot(
            fair_yes=0.2,
            fair_no=0.8,
            yes_price=0.04,
            no_price=0.96,
            edge_yes=0.16,
            edge_no=-0.16,
            sigma_10=0.0,
            sigma_30=0.0,
            sigma_slow=0.0,
            sigma_eff=0.0,
            momentum_5=0.0,
            momentum_15=0.0,
            drift=0.0,
            x_t=0.001,
            tau_seconds=20,
            jump_adjusted=False,
            outlier_adjusted=False,
        )
        yes_book = BestBidAsk(asset_id="yes", bid=0.03, ask=0.049, bid_size=100.0, ask_size=100.0)
        no_book = BestBidAsk(asset_id="no", bid=0.95, ask=0.97, bid_size=100.0, ask_size=100.0)

        signal = engine.evaluate(snapshot, yes_book, no_book, position=None)

        self.assertEqual(signal.action, SignalAction.OPEN)
        self.assertEqual(signal.side, OutcomeSide.YES)
        self.assertEqual(signal.size, 1.0)

    def test_paper_executor_uses_fixed_order_notional(self):
        from polymarket_bot.execution import PaperExecutor

        execution = ExecutionConfig(mode="paper", fixed_order_notional=1.0)
        executor = PaperExecutor(execution)
        signal = type(
            "Signal",
            (),
            {
                "side": OutcomeSide.YES,
                "size": 999.0,
                "reason": "test",
                "snapshot": type("Snap", (), {"edge_yes": 0.1, "edge_no": -0.1})(),
            },
        )()

        position = executor.open_position(None, signal, 0.25)

        self.assertAlmostEqual(position.size, 4.0)
        self.assertAlmostEqual(executor.last_report["requested_size"], 4.0)
        self.assertAlmostEqual(executor.last_report["requested_notional"], 1.0)
        self.assertAlmostEqual(executor.last_report["filled_notional"], 1.0)

    def test_hold_when_spread_is_too_wide(self):
        engine = StrategyEngine(StrategyConfig(size_buckets=default_size_buckets()))
        state = _build_state([100.0 + (i % 2) * 0.05 for i in range(40)])
        yes_book = BestBidAsk(asset_id="yes", bid=0.40, ask=0.50, bid_size=100.0, ask_size=100.0)
        no_book = BestBidAsk(asset_id="no", bid=0.50, ask=0.60, bid_size=100.0, ask_size=100.0)
        snapshot = engine.compute_snapshot(state, yes_book, no_book, tau_seconds=20)
        signal = engine.evaluate(snapshot, yes_book, no_book, position=None)
        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertEqual(signal.reason, "spread_too_wide")

    def test_close_when_edge_turns_negative(self):
        engine = StrategyEngine(StrategyConfig(edge_decay_close_threshold=0.0, size_buckets=default_size_buckets()))
        state = _build_state([100.0 + i * 0.12 for i in range(40)])
        yes_book = BestBidAsk(asset_id="yes", bid=0.99, ask=1.00, bid_size=100.0, ask_size=100.0)
        no_book = BestBidAsk(asset_id="no", bid=0.01, ask=0.02, bid_size=100.0, ask_size=100.0)
        snapshot = engine.compute_snapshot(state, yes_book, no_book, tau_seconds=25)
        position = Position(
            side=OutcomeSide.YES,
            size=1.0,
            entry_price=0.40,
            edge_at_entry=1.0,
            opened_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        )
        signal = engine.evaluate(snapshot, yes_book, no_book, position=position)
        self.assertEqual(signal.action, SignalAction.CLOSE)
        self.assertEqual(signal.reason, "edge_decayed")

    def test_live_open_sets_error_report_on_submission_failure(self):
        from polymarket_bot.execution import LiveExecutor

        executor = LiveExecutor.__new__(LiveExecutor)
        executor.execution = type("Exec", (), {"order_type": "fok", "fixed_order_notional": 1.0})()
        executor._can_derive = False
        executor._buy_constant = "BUY"
        executor._submit_with_auth_retry = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))

        market = type("Market", (), {"yes_token_id": "Y", "no_token_id": "N", "tick_size": 0.01, "neg_risk": False})()
        signal = type("Signal", (), {
            "side": OutcomeSide.YES,
            "size": 1.0,
            "reason": "test",
            "snapshot": type("Snap", (), {"edge_yes": 0.1, "edge_no": -0.1})(),
        })()

        with self.assertRaises(RuntimeError):
            executor.open_position(market, signal, 0.5)

        self.assertIsNotNone(executor.last_report)
        self.assertFalse(executor.last_report["success"])
        self.assertEqual(executor.last_report["status"], "rejected")
        self.assertAlmostEqual(executor.last_report["requested_size"], 2.0)
        self.assertAlmostEqual(executor.last_report["requested_notional"], 1.0)

    def test_flip_is_deferred_after_close_in_same_tick(self):
        app = TradingApplication.__new__(TradingApplication)

        now = datetime.now(timezone.utc)
        app.market = type("Market", (), {
            "start_time": now - timedelta(seconds=120),
            "end_time": now + timedelta(seconds=20),
            "yes_token_id": "Y",
            "no_token_id": "N",
            "slug": "m",
            "condition_id": "c",
        })()
        app.config = type("Cfg", (), {
            "logging": type("Log", (), {"active_only_last_seconds": 60})(),
            "strategy": type("Strat", (), {"book_fallback_max_age_seconds": 3})(),
            "execution": type("Exec", (), {"strategy_type": "fair_probability"})(),
        })()
        app._latest_trade_imbalance = 0.0
        app._latest_books = {
            "Y": BestBidAsk(asset_id="Y", bid=0.45, ask=0.47, bid_size=100, ask_size=100, timestamp_ms=int(now.timestamp()*1000)),
            "N": BestBidAsk(asset_id="N", bid=0.53, ask=0.55, bid_size=100, ask_size=100, timestamp_ms=int(now.timestamp()*1000)),
        }
        app._last_usable_books = {}
        app._order_in_flight = False
        app._inflight_since_ms = 0
        app._inflight_context = ""
        app._window_stats = WindowStats()
        app._write_activity_event = lambda **kwargs: None
        app._log_status = lambda now, snapshot: None

        snapshot = StrategySnapshot(
            fair_yes=0.6,
            fair_no=0.4,
            yes_price=0.47,
            no_price=0.55,
            edge_yes=0.13,
            edge_no=-0.15,
            sigma_10=0.01,
            sigma_30=0.01,
            sigma_slow=0.01,
            sigma_eff=0.01,
            momentum_5=0.0,
            momentum_15=0.0,
            drift=0.0,
            x_t=0.0,
            tau_seconds=20,
            jump_adjusted=False,
            outlier_adjusted=False,
        )

        class FakeStrategy:
            def compute_snapshot(self, **kwargs):
                return snapshot

            def evaluate(self, snapshot, yes_book, no_book, position):
                return TradeSignal(SignalAction.FLIP, side=OutcomeSide.NO, size=1.0, reason="flip", snapshot=snapshot)

        class FakeExecutor:
            def __init__(self):
                self.last_report = None
                self.open_calls = 0
                self.close_calls = 0

            def close_position(self, market, position):
                self.close_calls += 1
                self.last_report = {
                    "success": True,
                    "status": "filled",
                    "requested_size": position.size,
                    "filled_size": position.size,
                    "avg_price": position.entry_price,
                    "error": "",
                    "raw": None,
                }

            def open_position(self, market, signal, entry_price):
                self.open_calls += 1
                self.last_report = {
                    "success": True,
                    "status": "filled",
                    "requested_size": signal.size,
                    "filled_size": signal.size,
                    "avg_price": entry_price,
                    "error": "",
                    "raw": None,
                }
                return Position(side=signal.side, size=signal.size, entry_price=entry_price, edge_at_entry=0.1, opened_at=now)

            def reconcile_position(self, market, local_position):
                return local_position, True, "ok"

        app.strategy = FakeStrategy()
        app.executor = FakeExecutor()
        app.roll = type("Roll", (), {"open_price": 100.0, "last_price": 101.0})()
        app.state = RuntimeState(
            market=app.market,
            position=Position(side=OutcomeSide.YES, size=1.0, entry_price=0.45, edge_at_entry=0.2, opened_at=now),
            last_snapshot=None,
        )

        app._tick()

        self.assertEqual(app.executor.close_calls, 1)
        self.assertEqual(app.executor.open_calls, 0)
        self.assertIsNone(app.state.position)

    def test_live_open_is_blocked_when_reconcile_finds_existing_position(self):
        app = TradingApplication.__new__(TradingApplication)

        now = datetime.now(timezone.utc)
        app.market = type(
            "Market",
            (),
            {
                "slug": "btc-updown-5m-test",
                "condition_id": None,
                "yes_token_id": "Y",
                "no_token_id": "N",
                "start_time": now - timedelta(seconds=250),
                "end_time": now + timedelta(seconds=20),
            },
        )()

        class _StrategyCfg:
            require_both_prices = True
            max_entries_per_window = 2
            max_flips_per_window = 1
            allow_same_side_reentry = False
            book_fallback_max_age_seconds = 3

        class _ExecutionCfg:
            mode = "live"
            strategy_type = "main"

        class _LoggingCfg:
            active_only_last_seconds = 60

        class _Config:
            strategy = _StrategyCfg()
            execution = _ExecutionCfg()
            logging = _LoggingCfg()

        app.config = _Config()
        app.state = RuntimeState(market=app.market, position=None, last_snapshot=None)
        app.roll = type("Roll", (), {"open_price": 100.0, "last_price": 101.0})()
        app.health = type("Health", (), {})()
        app._latest_trade_imbalance = 0.0
        app._latest_books = {
            "Y": BestBidAsk(asset_id="Y", bid=0.45, ask=0.47, bid_size=100, ask_size=100, timestamp_ms=int(now.timestamp() * 1000)),
            "N": BestBidAsk(asset_id="N", bid=0.53, ask=0.55, bid_size=100, ask_size=100, timestamp_ms=int(now.timestamp() * 1000)),
        }
        app._last_usable_books = {}
        app._order_in_flight = False
        app._inflight_since_ms = 0
        app._inflight_context = ""
        app._last_preopen_reconcile_ms = 0
        app._window_stats = WindowStats()
        app._window_entry_count = 0
        app._window_flip_count = 0
        app._seen_entry_sides = set()
        app._last_status_second = None
        app._last_wait_log_second = None
        app._write_activity_event = lambda **kwargs: None
        app._log_status = lambda now, snapshot: None
        app._log_health = lambda now=None: None

        snapshot = StrategySnapshot(
            fair_yes=0.62,
            fair_no=0.38,
            yes_price=0.47,
            no_price=0.55,
            edge_yes=0.15,
            edge_no=-0.17,
            sigma_10=0.01,
            sigma_30=0.01,
            sigma_slow=0.01,
            sigma_eff=0.01,
            momentum_5=0.0,
            momentum_15=0.0,
            drift=0.0,
            x_t=0.0,
            tau_seconds=20,
            jump_adjusted=False,
            outlier_adjusted=False,
        )

        class FakeStrategy:
            def compute_snapshot(self, **kwargs):
                return snapshot

            def evaluate(self, snapshot, yes_book, no_book, position):
                return TradeSignal(SignalAction.OPEN, side=OutcomeSide.YES, size=1.0, reason="open", snapshot=snapshot)

        class FakeExecutor:
            def __init__(self):
                self.open_calls = 0

            def open_position(self, market, signal, entry_price):
                self.open_calls += 1
                raise AssertionError("open_position should not be called when reconcile finds a live position")

            def reconcile_position(self, market, local_position):
                return (
                    Position(
                        side=OutcomeSide.NO,
                        size=3.0,
                        entry_price=0.52,
                        edge_at_entry=0.1,
                        opened_at=now,
                    ),
                    True,
                    "reconstructed",
                )

        app.strategy = FakeStrategy()
        app.executor = FakeExecutor()

        app._tick()

        self.assertEqual(app.executor.open_calls, 0)
        self.assertIsNotNone(app.state.position)
        self.assertEqual(app.state.position.side, OutcomeSide.NO)
        self.assertAlmostEqual(app.state.position.size, 3.0)
        self.assertEqual(app._window_entry_count, 1)
        self.assertIn(OutcomeSide.NO, app._seen_entry_sides)

    def test_open_failed_clears_inflight_for_live_fak(self):
        app = TradingApplication.__new__(TradingApplication)
        app._order_in_flight = True
        app._inflight_since_ms = 123
        app._inflight_context = "open_open"

        class _Execution:
            mode = "live"
            order_type = "FAK"

        class _Config:
            execution = _Execution()

        app.config = _Config()

        app._clear_inflight_if_safe("open_failed")

        self.assertFalse(app._order_in_flight)
        self.assertEqual(app._inflight_context, "")

    def test_stale_live_fak_inflight_is_released(self):
        app = TradingApplication.__new__(TradingApplication)
        app._order_in_flight = True
        app._inflight_since_ms = int(datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        app._inflight_context = "open_open"
        app._inflight_timeout_ms = 3000

        class _Execution:
            mode = "live"
            order_type = "FAK"

        class _Config:
            execution = _Execution()

        app.config = _Config()

        released = app._maybe_release_stale_inflight(datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc))

        self.assertTrue(released)
        self.assertFalse(app._order_in_flight)


if __name__ == "__main__":
    unittest.main()
