import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import logging

from .archive import JsonlWriter, WindowArchiveWriter
from .config import AppConfig
from .execution import build_executor
from .gamma import current_window_start, next_window_start, resolve_market, resolve_market_for_window
from .market_state import RollingState
from .models import BestBidAsk, OutcomeSide, RuntimeHealth, RuntimeState, SignalAction, WindowStats
from .strategy import StrategyEngine, default_size_buckets
from .validate import validate_config
from .ws import market_book_stream, price_stream


LOGGER = logging.getLogger(__name__)


class TradingApplication:
    def __init__(self, config):
        if not config.strategy.size_buckets:
            config.strategy.size_buckets = default_size_buckets()
        self.config = config
        self.market = self._resolve_initial_market()
        self.state = RuntimeState(market=self.market)
        self.health = RuntimeHealth()
        self.roll = RollingState(config.strategy)
        self.strategy = StrategyEngine(config.strategy)
        self.executor = build_executor(config.execution, config.wallet)
        self.archive = WindowArchiveWriter(config.logging.window_close_path)
        self.activity_archive = JsonlWriter(config.logging.activity_path)
        self.state_archive = JsonlWriter(config.logging.market_state_path)
        self._latest_book = None
        self._last_usable_book = None
        self._latest_trade_imbalance = 0.0
        self._queue = asyncio.Queue()
        self._last_status_second = None
        self._last_wait_log_second = None
        self._book_task = None
        self._latest_spot_price = None
        self._window_stats = WindowStats()
        self._running = True
        self._tasks = {}

    async def run(self):
        self._startup_check()
        self._start_background_tasks()
        try:
            while self._running:
                kind, payload = await self._queue.get()
                if kind == "price":
                    self._handle_price(payload)
                elif kind == "book":
                    if payload.asset_id == self._active_asset_id():
                        self._latest_book = payload
                        self._maybe_cache_book(payload)
                        self.health.book_updates += 1
                        self.health.last_book_at_ms = payload.timestamp_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
                elif kind == "price_stream_event":
                    self._handle_stream_event("price", payload)
                elif kind == "book_stream_event":
                    self._handle_stream_event("book", payload)
                elif kind == "task_failure":
                    await self._handle_task_failure(payload)
                elif kind == "shutdown":
                    await self._shutdown(payload.get("reason", "requested"))
                if self._latest_book is not None and self.roll.open_price is not None:
                    self._tick()
                else:
                    self._log_waiting()
        finally:
            await self._cancel_background_tasks()

    def _startup_check(self):
        validation = validate_config(self.config)
        if validation["errors"]:
            for item in validation["errors"]:
                LOGGER.error("STARTUP_CHECK error=%s", item)
            raise RuntimeError("configuration validation failed")
        for item in validation["warnings"]:
            LOGGER.warning("STARTUP_CHECK warning=%s", item)
        LOGGER.info(
            "STARTUP_CHECK mode=%s profile=%s market_ref=%s active_only_last_seconds=%s decision_window=%s-%s",
            self.config.execution.mode,
            self.config.execution.strategy_profile,
            self.config.market.market_slug or self.config.market.condition_id or self.config.market.slug_prefix,
            self.config.logging.active_only_last_seconds,
            self.config.strategy.decision_window_start_seconds,
            self.config.strategy.decision_window_end_seconds,
        )

    async def _consume_prices(self):
        await self._queue.put(("price_stream_event", {"event": "connect"}))
        try:
            async for tick in price_stream(
                self.config.price_feed.symbol,
                self.config.price_feed.source_topic,
                self.config.price_feed.provider,
            ):
                await self._queue.put(("price", tick))
        except Exception as exc:
            await self._queue.put(("price_stream_event", {"event": "error", "message": str(exc)}))
            await self._queue.put(("task_failure", {"task": "prices", "error": str(exc)}))

    async def _consume_books(self, asset_id):
        await self._queue.put(("book_stream_event", {"event": "connect", "asset_id": asset_id}))
        try:
            async for book in market_book_stream(asset_id):
                await self._queue.put(("book", book))
        except Exception as exc:
            await self._queue.put(("book_stream_event", {"event": "error", "message": str(exc), "asset_id": asset_id}))
            await self._queue.put(("task_failure", {"task": "books", "error": str(exc), "asset_id": asset_id}))

    def _handle_price(self, tick):
        from .models import PriceTick

        if not isinstance(tick, PriceTick):
            return
        self._latest_spot_price = tick.price
        self.health.price_updates += 1
        self.health.last_price_at_ms = tick.timestamp_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
        x_t = self.roll.update_price(tick.price)
        LOGGER.debug("spot tick symbol=%s price=%.2f x_t=%.6f", tick.symbol, tick.price, x_t)

    def _handle_stream_event(self, stream_name, payload):
        event = payload.get("event")
        if stream_name == "price":
            if event == "connect":
                self.health.price_reconnects += 1
            elif event == "error":
                self.health.last_error = "price:%s" % payload.get("message", "")
        elif stream_name == "book":
            if event == "connect":
                self.health.book_reconnects += 1
            elif event == "error":
                self.health.last_error = "book:%s" % payload.get("message", "")

    async def _health_loop(self):
        interval = max(5, int(self.config.logging.health_log_interval_seconds))
        while self._running:
            await asyncio.sleep(interval)
            self._log_health()

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
            book_yes=self._effective_book(now),
            tau_seconds=tau_seconds,
            trade_imbalance=self._latest_trade_imbalance,
            previous_fair_yes=None if self.state.last_snapshot is None else self.state.last_snapshot.fair_yes,
        )
        self.state.last_snapshot = snapshot
        current_book = self._effective_book(now)
        self._window_stats.last_yes_ask = current_book.ask
        self._window_stats.last_yes_bid = current_book.bid
        self._window_stats.last_fair_yes = snapshot.fair_yes
        self._log_status(now, snapshot)
        signal = self.strategy.evaluate(snapshot, current_book, self.state.position)
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
            ask_price = current_book.ask if signal.side == OutcomeSide.YES else max(0.001, 1.0 - (current_book.bid or 0.0))
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
        effective_book = self._effective_book(now)
        yes_bid = effective_book.bid
        yes_ask = effective_book.ask
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
        self.state_archive.write(
            {
                "recordType": "state",
                "strategyVersion": self.config.execution.strategy_version,
                "strategyProfile": self.config.execution.strategy_profile,
                "strategyType": self.config.execution.strategy_type,
                "marketSlug": self.market.slug,
                "eventAtMs": int(now.timestamp() * 1000),
                "timeToExpirySec": int(snapshot.tau_seconds),
                "spot": self.roll.last_price,
                "x_t": snapshot.x_t,
                "yesBid": yes_bid,
                "yesAsk": yes_ask,
                "noBid": no_bid,
                "noAsk": no_ask,
                "fairYes": snapshot.fair_yes,
                "fairNo": snapshot.fair_no,
                "edgeYes": snapshot.edge_yes,
                "edgeNo": snapshot.edge_no,
                "position": position,
            }
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
            effective_book = self._effective_book(now)
            yes_bid = effective_book.bid
            yes_ask = effective_book.ask
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
                "WINDOW window=%s phase=collecting tau=%ss active_in=%ss spot=%s x_t=%s yes_bid=%s yes_ask=%s no_bid=%s no_ask=%s pos=%s",
                self.market.slug or self.market.condition_id,
                int(tau_seconds),
                int(tau_seconds - self.config.logging.active_only_last_seconds),
                _fmt(self._latest_spot_price),
                _fmt(self.roll.latest_x()),
                _fmt(yes_bid),
                _fmt(yes_ask),
                _fmt(no_bid),
                _fmt(no_ask),
                position,
            )
            self._log_health(now)

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
        self._last_usable_book = None
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
        LOGGER.info(
            "MARKET window=%s question=%s trade_side=%s active_asset_id=%s yes_token_id=%s no_token_id=%s",
            self.market.slug or self.market.condition_id,
            self.market.question,
            self.config.market.trade_side.value,
            self._active_asset_id(),
            self.market.yes_token_id,
            self.market.no_token_id,
        )
        self.state_archive.write(
            {
                "recordType": "window",
                "phase": "activated",
                "strategyVersion": self.config.execution.strategy_version,
                "strategyProfile": self.config.execution.strategy_profile,
                "strategyType": self.config.execution.strategy_type,
                "marketSlug": self.market.slug,
                "eventAtMs": int(datetime.now(timezone.utc).timestamp() * 1000),
                "startTime": self.market.start_time.isoformat() if self.market.start_time else None,
                "endTime": self.market.end_time.isoformat() if self.market.end_time else None,
            }
        )
        self._log_health()

    def _roll_to_next_window(self, now):
        if not self.config.market.auto_roll_windows:
            self._archive_window(now)
            awaitable = self._queue.put(("shutdown", {"reason": "market_window_over"}))
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(awaitable)
            except RuntimeError:
                pass
            return
        self._archive_window(now)
        next_start = next_window_start(now)
        next_market = resolve_market_for_window(self.config.market, next_start)
        self._ensure_market_times(next_market)
        if self._book_task is not None:
            self._book_task.cancel()
        self._reset_for_market(next_market)
        self._book_task = asyncio.create_task(self._consume_books(self._active_asset_id()))
        self._tasks["books"] = self._book_task

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
        self.state_archive.write(
            {
                "recordType": "window",
                "phase": "closed",
                "strategyVersion": self.config.execution.strategy_version,
                "strategyProfile": self.config.execution.strategy_profile,
                "strategyType": self.config.execution.strategy_type,
                "marketSlug": self.market.slug,
                "eventAtMs": int(now.timestamp() * 1000),
                "startTime": self.market.start_time.isoformat() if self.market.start_time else None,
                "endTime": self.market.end_time.isoformat() if self.market.end_time else None,
                "finalDirection": final_direction,
                "realizedPnl": record["realizedPnl"],
            }
        )
        LOGGER.info(
            "WINDOW window=%s phase=closed winner=%s realized_pnl=%.4f fills=%s archive=%s",
            self.market.slug or self.market.condition_id,
            final_direction,
            record["realizedPnl"],
            summary["activity"]["fillCount"],
            self.config.logging.window_close_path,
        )

    def _write_activity_event(self, now, event_type, action, side, size, price, reason, snapshot):
        effective_book = self._effective_book(now)
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
            "yesBid": effective_book.bid if effective_book is not None else None,
            "yesAsk": effective_book.ask if effective_book is not None else None,
        }
        self.activity_archive.write(record)

    def _infer_final_direction(self):
        if self.roll.last_price is not None and self.roll.open_price is not None:
            return "Up" if self.roll.last_price > self.roll.open_price else "Down"
        if self._window_stats.last_fair_yes is not None:
            return "Up" if self._window_stats.last_fair_yes >= 0.5 else "Down"
        return "Down"

    def _log_health(self, now=None):
        now = now or datetime.now(timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        interval_ms = max(1000, int(self.config.logging.health_log_interval_seconds * 1000))
        if now_ms - self.health.last_health_log_at_ms < interval_ms:
            return
        self.health.last_health_log_at_ms = now_ms
        price_lag_ms = None if self.health.last_price_at_ms == 0 else max(0, now_ms - self.health.last_price_at_ms)
        book_lag_ms = None if self.health.last_book_at_ms == 0 else max(0, now_ms - self.health.last_book_at_ms)
        stale_threshold_ms = int(self.config.logging.stale_data_threshold_seconds * 1000)
        status = "ok"
        if (price_lag_ms is not None and price_lag_ms > stale_threshold_ms) or (book_lag_ms is not None and book_lag_ms > stale_threshold_ms):
            status = "stale"
        LOGGER.info(
            "HEALTH status=%s window=%s price_updates=%s book_updates=%s price_reconnects=%s book_reconnects=%s price_lag_ms=%s book_lag_ms=%s last_error=%s",
            status,
            self.market.slug or self.market.condition_id,
            self.health.price_updates,
            self.health.book_updates,
            self.health.price_reconnects,
            self.health.book_reconnects,
            _fmt_int(price_lag_ms),
            _fmt_int(book_lag_ms),
            self.health.last_error or "none",
        )

    def _maybe_cache_book(self, book):
        if book is None:
            return
        if book.bid is None and book.ask is None and book.bid_size <= 0 and book.ask_size <= 0:
            return
        if self._last_usable_book is None:
            self._last_usable_book = book
            return
        self._last_usable_book = book.merged_with(self._last_usable_book)

    def _effective_book(self, now):
        if self._latest_book is None and self._last_usable_book is None:
            return BestBidAsk(asset_id=self._active_asset_id(), bid=None, ask=None)
        current = self._latest_book or self._last_usable_book
        if self._last_usable_book is None:
            return current
        max_age_ms = int(self.config.strategy.book_fallback_max_age_seconds * 1000)
        reference_ms = int(now.timestamp() * 1000)
        if self._last_usable_book.timestamp_ms and (reference_ms - self._last_usable_book.timestamp_ms) > max_age_ms:
            return current
        return current.merged_with(self._last_usable_book)

    def _start_background_tasks(self):
        self._tasks["prices"] = asyncio.create_task(self._consume_prices())
        self._book_task = asyncio.create_task(self._consume_books(self._active_asset_id()))
        self._tasks["books"] = self._book_task
        self._tasks["health"] = asyncio.create_task(self._health_loop())

    async def _handle_task_failure(self, payload):
        task_name = payload.get("task", "unknown")
        error = payload.get("error", "unknown")
        LOGGER.error("SUPERVISOR task=%s action=restart error=%s", task_name, error)
        self.health.last_error = "%s:%s" % (task_name, error)
        self.health.supervisor_restarts += 1
        await asyncio.sleep(max(1, int(self.config.logging.supervisor_restart_backoff_seconds)))
        if not self._running:
            return
        if task_name == "prices":
            self._tasks["prices"] = asyncio.create_task(self._consume_prices())
        elif task_name == "books":
            self._tasks["books"] = asyncio.create_task(self._consume_books(self._active_asset_id()))
            self._book_task = self._tasks["books"]

    async def _shutdown(self, reason):
        if not self._running:
            return
        self._running = False
        self.health.shutdowns += 1
        now = datetime.now(timezone.utc)
        self.state_archive.write(
            {
                "recordType": "shutdown",
                "eventAtMs": int(now.timestamp() * 1000),
                "marketSlug": self.market.slug,
                "reason": reason,
                "strategyVersion": self.config.execution.strategy_version,
                "strategyProfile": self.config.execution.strategy_profile,
            }
        )
        LOGGER.info("SHUTDOWN reason=%s grace_seconds=%s", reason, self.config.logging.shutdown_grace_seconds)

    async def _cancel_background_tasks(self):
        for name, task in list(self._tasks.items()):
            task.cancel()
        for name, task in list(self._tasks.items()):
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=self.config.logging.shutdown_grace_seconds)



def _fmt(value):
    if value is None:
        return "None"
    return f"{value:.4f}"


def _fmt_int(value):
    if value is None:
        return "None"
    return str(int(value))
