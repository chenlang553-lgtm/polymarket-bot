import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import logging

from .archive import JsonlWriter, WindowArchiveWriter
from .config import AppConfig
from .execution import build_executor
from .gamma import current_window_start, next_window_start, resolve_market, resolve_market_for_window
from .market_state import RollingState
from .models import BestBidAsk, OutcomeSide, RuntimeState, SignalAction, WindowStats
from .strategy import StrategyEngine, default_size_buckets
from .ws import market_book_stream, rtds_price_stream


LOGGER = logging.getLogger(__name__)


class TradingApplication:
    def __init__(self, config):
        if not config.strategy.size_buckets:
            config.strategy.size_buckets = default_size_buckets()
        self.config = config
        self.market = self._resolve_initial_market()
        self.state = RuntimeState(market=self.market)
        self.roll = RollingState(config.strategy)
        self.strategy = StrategyEngine(config.strategy)
        self.executor = build_executor(config.execution, config.wallet)
        self.archive = WindowArchiveWriter(config.logging.window_close_path)
        self.activity_archive = JsonlWriter(config.logging.activity_path)
        self._latest_book = None
        self._latest_trade_imbalance = 0.0
        self._queue = asyncio.Queue()
        self._last_status_second = None
        self._last_wait_log_second = None
        self._book_task = None
        self._latest_spot_price = None
        self._window_stats = WindowStats()

    async def run(self):
        producers = [
            asyncio.create_task(self._consume_prices()),
        ]
        self._book_task = asyncio.create_task(self._consume_books(self._active_asset_id()))
        producers.append(self._book_task)
        try:
            while True:
                kind, payload = await self._queue.get()
                if kind == "price":
                    self._handle_price(payload)
                elif kind == "book":
                    if payload.asset_id == self._active_asset_id():
                        self._latest_book = payload
                if self._latest_book is not None and self.roll.open_price is not None:
                    self._tick()
                else:
                    self._log_waiting()
        finally:
            for task in producers:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def _consume_prices(self):
        async for tick in rtds_price_stream(self.config.price_feed.symbol, self.config.price_feed.source_topic):
            await self._queue.put(("price", tick))

    async def _consume_books(self, asset_id):
        async for book in market_book_stream(asset_id):
            await self._queue.put(("book", book))

    def _handle_price(self, tick):
        from .models import PriceTick

        if not isinstance(tick, PriceTick):
            return
        self._latest_spot_price = tick.price
        x_t = self.roll.update_price(tick.price)
        LOGGER.debug("spot tick symbol=%s price=%.2f x_t=%.6f", tick.symbol, tick.price, x_t)

    def _tick(self):
        if self.market.start_time is None or self.market.end_time is None:
            raise RuntimeError("market end_time is required for live strategy timing")
        now = datetime.now(timezone.utc)
        if now < self.market.start_time:
            self._log_waiting(now)
            return
        tau_seconds = (self.market.end_time - now).total_seconds()
        if tau_seconds <= 0:
            self._roll_to_next_window(now)
            return

        if tau_seconds > self.config.logging.active_only_last_seconds:
            self._log_waiting(now)
            return

        snapshot = self.strategy.compute_snapshot(
            state=self.roll,
            book_yes=self._latest_book,
            tau_seconds=tau_seconds,
            trade_imbalance=self._latest_trade_imbalance,
        )
        self.state.last_snapshot = snapshot
        self._window_stats.last_yes_ask = self._latest_book.ask
        self._window_stats.last_yes_bid = self._latest_book.bid
        self._window_stats.last_fair_yes = snapshot.fair_yes
        self._log_status(now, snapshot)
        signal = self.strategy.evaluate(snapshot, self._latest_book, self.state.position)
        if signal.action == SignalAction.HOLD:
            return

        if signal.action in {SignalAction.CLOSE, SignalAction.FLIP} and self.state.position is not None:
            self._window_stats.record_action(signal.action.value.upper(), self.state.position.size, self.config.execution.strategy_type, "filled")
            self._window_stats.mark_execution_event()
            self._write_activity_event(
                now=now,
                event_type="execution",
                action=signal.action.value,
                side=self.state.position.side,
                size=self.state.position.size,
                price=self.state.position.entry_price,
                reason=signal.reason,
                snapshot=snapshot,
            )
            LOGGER.info(
                "STRATEGY action=%s side=%s size=%.4f reason=%s tau=%.1f fair_yes=%.3f fair_no=%.3f edge_yes=%s edge_no=%s",
                signal.action.value,
                self.state.position.side.value,
                self.state.position.size,
                signal.reason,
                tau_seconds,
                snapshot.fair_yes,
                snapshot.fair_no,
                _fmt(snapshot.edge_yes),
                _fmt(snapshot.edge_no),
            )
            self.executor.close_position(self.market, self.state.position)
            self.state.position = None
            self._window_stats.mark_position(None, 0.0)

        if signal.action in {SignalAction.OPEN, SignalAction.FLIP}:
            ask_price = self._latest_book.ask if signal.side == OutcomeSide.YES else max(0.001, 1.0 - (self._latest_book.bid or 0.0))
            self._window_stats.record_action(signal.action.value.upper(), signal.size, self.config.execution.strategy_type, "filled")
            self._window_stats.record_fill(signal.side, signal.size, ask_price)
            self._window_stats.mark_execution_event()
            self._write_activity_event(
                now=now,
                event_type="fill",
                action=signal.action.value,
                side=signal.side,
                size=signal.size,
                price=ask_price,
                reason=signal.reason,
                snapshot=snapshot,
            )
            LOGGER.info(
                "STRATEGY action=%s side=%s size=%.4f reason=%s tau=%.1f ask=%.4f fair_yes=%.3f fair_no=%.3f edge_yes=%s edge_no=%s",
                signal.action.value,
                signal.side.value,
                signal.size,
                signal.reason,
                tau_seconds,
                ask_price,
                snapshot.fair_yes,
                snapshot.fair_no,
                _fmt(snapshot.edge_yes),
                _fmt(snapshot.edge_no),
            )
            self.state.position = self.executor.open_position(self.market, signal, ask_price)
            self._window_stats.mark_position(signal.side, signal.size)

    def _log_status(self, now, snapshot):
        current_second = int(now.timestamp())
        if self._last_status_second == current_second:
            return
        self._last_status_second = current_second

        yes_bid = self._latest_book.bid
        yes_ask = self._latest_book.ask
        no_bid = None if yes_ask is None else max(0.0, 1.0 - yes_ask)
        no_ask = None if yes_bid is None else max(0.0, 1.0 - yes_bid)
        position = "flat"
        if self.state.position is not None:
            position = "%s@%.4f x %.4f" % (
                self.state.position.side.value,
                self.state.position.entry_price,
                self.state.position.size,
            )

        LOGGER.info(
            "STATUS window=%s tau=%ss spot=%.2f x_t=%.6f yes_bid=%s yes_ask=%s no_bid=%s no_ask=%s fair_yes=%.3f fair_no=%.3f edge_yes=%s edge_no=%s pos=%s",
            self.market.slug or self.market.condition_id,
            int(snapshot.tau_seconds),
            self.roll.last_price,
            snapshot.x_t,
            _fmt(yes_bid),
            _fmt(yes_ask),
            _fmt(no_bid),
            _fmt(no_ask),
            snapshot.fair_yes,
            snapshot.fair_no,
            _fmt(snapshot.edge_yes),
            _fmt(snapshot.edge_no),
            position,
        )

    def _log_waiting(self, now=None):
        now = now or datetime.now(timezone.utc)
        current_second = int(now.timestamp())
        if self._last_wait_log_second == current_second:
            return
        self._last_wait_log_second = current_second

        if self.market.start_time is None or self.market.end_time is None:
            return

        tau_seconds = (self.market.end_time - now).total_seconds()
        if tau_seconds <= 0:
            return
        if tau_seconds > self.config.logging.active_only_last_seconds:
            LOGGER.info(
                "WINDOW window=%s phase=collecting tau=%ss active_in=%ss spot=%s",
                self.market.slug or self.market.condition_id,
                int(tau_seconds),
                int(tau_seconds - self.config.logging.active_only_last_seconds),
                _fmt(self._latest_spot_price),
            )

    def _resolve_initial_market(self):
        if self.config.market.market_slug or self.config.market.condition_id or (self.config.market.yes_token_id and self.config.market.no_token_id):
            market = resolve_market(self.config.market)
        else:
            market = resolve_market_for_window(self.config.market, current_window_start())
        self._ensure_market_times(market)
        return market

    def _ensure_market_times(self, market):
        if market.start_time is None and market.slug:
            market.start_time = current_window_start()
        if market.end_time is None and market.start_time is not None:
            from datetime import timedelta
            market.end_time = market.start_time + timedelta(seconds=market.window_size_seconds)

    def _active_asset_id(self):
        return self.market.yes_token_id if self.config.market.trade_side == OutcomeSide.YES else self.market.no_token_id

    def _reset_for_market(self, market):
        self._window_stats = WindowStats()
        self.market = market
        self.state.market = market
        self.state.last_snapshot = None
        self.state.position = None
        self.roll = RollingState(self.config.strategy)
        self._latest_book = None
        self._last_status_second = None
        self._last_wait_log_second = None
        if self._latest_spot_price is not None:
            self.roll.update_price(self._latest_spot_price)
        LOGGER.info(
            "WINDOW window=%s phase=activated start=%s end=%s trade_side=%s",
            self.market.slug or self.market.condition_id,
            self.market.start_time.isoformat() if self.market.start_time else "unknown",
            self.market.end_time.isoformat() if self.market.end_time else "unknown",
            self.config.market.trade_side.value,
        )

    def _roll_to_next_window(self, now):
        if not self.config.market.auto_roll_windows:
            self._archive_window(now)
            LOGGER.info("market window is over")
            raise SystemExit(0)
        self._archive_window(now)
        next_start = next_window_start(now)
        next_market = resolve_market_for_window(self.config.market, next_start)
        self._ensure_market_times(next_market)
        if self._book_task is not None:
            self._book_task.cancel()
        self._reset_for_market(next_market)
        self._book_task = asyncio.create_task(self._consume_books(self._active_asset_id()))

    def _archive_window(self, now):
        final_direction = self._infer_final_direction()
        summary = self._window_stats.finalize(final_direction)
        inferred_winner = "Up" if (self._window_stats.last_fair_yes or 0.0) >= 0.5 else "Down"
        record = {
            "recordType": "window_close",
            "strategyVersion": self.config.execution.strategy_version,
            "strategyProfile": self.config.execution.strategy_profile,
            "strategyType": self.config.execution.strategy_type if self._window_stats.fill_count else None,
            "marketSlug": self.market.slug,
            "title": self.market.question,
            "closedAtMs": int(now.timestamp() * 1000),
            "timeToExpirySec": 0,
            "finalDirection": final_direction,
            "inferredWinner": inferred_winner,
            "actualWinner": final_direction,
            "resolutionSource": "inferred-final-price",
        }
        record.update(summary)
        self.archive.write(record)
        LOGGER.info(
            "WINDOW window=%s phase=closed winner=%s realized_pnl=%.4f fills=%s archive=%s",
            self.market.slug or self.market.condition_id,
            final_direction,
            record["realizedPnl"],
            summary["activity"]["fillCount"],
            self.config.logging.window_close_path,
        )

    def _write_activity_event(self, now, event_type, action, side, size, price, reason, snapshot):
        record = {
            "recordType": "activity",
            "eventType": event_type,
            "strategyVersion": self.config.execution.strategy_version,
            "strategyProfile": self.config.execution.strategy_profile,
            "strategyType": self.config.execution.strategy_type,
            "marketSlug": self.market.slug,
            "eventAtMs": int(now.timestamp() * 1000),
            "timeToExpirySec": int(snapshot.tau_seconds),
            "action": action,
            "side": None if side is None else ("Up" if side == OutcomeSide.YES else "Down"),
            "size": size,
            "price": price,
            "reason": reason,
            "spot": self.roll.last_price,
            "fairYes": snapshot.fair_yes,
            "fairNo": snapshot.fair_no,
            "edgeYes": snapshot.edge_yes,
            "edgeNo": snapshot.edge_no,
            "yesBid": self._latest_book.bid if self._latest_book is not None else None,
            "yesAsk": self._latest_book.ask if self._latest_book is not None else None,
        }
        self.activity_archive.write(record)

    def _infer_final_direction(self):
        if self.roll.last_price is not None and self.roll.open_price is not None:
            return "Up" if self.roll.last_price > self.roll.open_price else "Down"
        if self._window_stats.last_fair_yes is not None:
            return "Up" if self._window_stats.last_fair_yes >= 0.5 else "Down"
        return "Down"



def _fmt(value):
    if value is None:
        return "None"
    return f"{value:.4f}"
