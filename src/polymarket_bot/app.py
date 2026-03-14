import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import logging

from .archive import JsonlWriter, WindowArchiveWriter
from .config import AppConfig
from .execution import build_executor
from .gamma import current_window_start, resolve_market, resolve_market_for_window
from .market_state import RollingState
from .models import BestBidAsk, OutcomeSide, RuntimeHealth, RuntimeState, SignalAction, TradeSignal, WindowStats
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
        self._latest_books = {}
        self._last_usable_books = {}
        self._latest_trade_imbalance = 0.0
        self._queue = asyncio.Queue()
        self._last_status_second = None
        self._last_wait_log_second = None
        self._latest_spot_price = None
        self._window_stats = WindowStats()
        self._window_entry_count = 0
        self._window_flip_count = 0
        self._seen_entry_sides = set()
        self._running = True
        self._tasks = {}
        self._order_in_flight = False
        self._inflight_since_ms = 0
        self._inflight_context = ""

    async def run(self):
        self._startup_check()
        self._start_background_tasks()
        try:
            while self._running:
                kind, payload = await self._queue.get()
                if kind == "price":
                    self._handle_price(payload)
                elif kind == "book":
                    if payload.asset_id in self._market_asset_ids():
                        self._latest_books[payload.asset_id] = payload
                        self._maybe_cache_book(payload.asset_id, payload)
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
                if self._has_any_book() and self.roll.open_price is not None:
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
            await self._queue.put(("task_failure", {"task": "book:%s" % asset_id, "error": str(exc), "asset_id": asset_id}))

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

    def _execution_report(self):
        report = getattr(self.executor, "last_report", None)
        if isinstance(report, dict):
            return report
        return {
            "success": True,
            "status": "filled",
            "requested_size": 0.0,
            "filled_size": 0.0,
            "avg_price": None,
            "error": "",
            "raw": None,
        }

    def _mark_inflight(self, now, context):
        self._order_in_flight = True
        self._inflight_since_ms = int(now.timestamp() * 1000)
        self._inflight_context = context

    def _clear_inflight(self):
        self._order_in_flight = False
        self._inflight_since_ms = 0
        self._inflight_context = ""

    def _reconcile_inflight(self):
        reconcile = getattr(self.executor, "reconcile_position", None)
        if not callable(reconcile):
            return False
        position, resolved, reason = reconcile(self.market, self.state.position)
        if resolved:
            self.state.position = position
            self._window_stats.mark_position(None if position is None else position.side, 0.0 if position is None else position.size)
            self._clear_inflight()
            LOGGER.info("EXECUTION reconcile_resolved reason=%s pos=%s", reason, "flat" if position is None else position.side.value)
            return True
        LOGGER.warning("EXECUTION reconcile_pending reason=%s context=%s", reason, self._inflight_context)
        return False

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

        if self._order_in_flight and not self._reconcile_inflight():
            LOGGER.warning("EXECUTION blocked_inflight context=%s", self._inflight_context)
            return

        yes_book = self._effective_book(self.market.yes_token_id, now)
        no_book = self._effective_book(self.market.no_token_id, now)
        snapshot = self.strategy.compute_snapshot(
            state=self.roll,
            book_yes=yes_book,
            book_no=no_book,
            tau_seconds=tau_seconds,
            trade_imbalance=self._latest_trade_imbalance,
            previous_fair_yes=None if self.state.last_snapshot is None else self.state.last_snapshot.fair_yes,
        )
        self.state.last_snapshot = snapshot
        self._window_stats.last_yes_ask = snapshot.yes_price
        self._window_stats.last_yes_bid = yes_book.bid
        self._window_stats.last_fair_yes = snapshot.fair_yes
        self._log_status(now, snapshot)
        signal = self.strategy.evaluate(snapshot, yes_book, no_book, self.state.position)
        signal = self._apply_signal_risk_controls(signal)
        if signal.action == SignalAction.HOLD:
            return

        if signal.action in {SignalAction.CLOSE, SignalAction.FLIP} and self.state.position is not None:
            self._window_stats.record_action(signal.action.value.upper(), self.state.position.size, self.config.execution.strategy_type, "attempt")
            self._mark_inflight(now, "close_%s" % signal.action.value)
            try:
                self.executor.close_position(self.market, self.state.position)
            except Exception as exc:
                LOGGER.error("EXECUTION close_failed action=%s side=%s size=%.4f error=%s", signal.action.value, self.state.position.side.value, self.state.position.size, exc)
                return

            report = self._execution_report()
            requested = max(0.0, float(report.get("requested_size", self.state.position.size)))
            filled = max(0.0, float(report.get("filled_size", 0.0)))
            if not report.get("success", False):
                LOGGER.error("EXECUTION close_unsuccessful status=%s error=%s", report.get("status"), report.get("error"))
                return

            if filled >= requested - 1e-9:
                self._window_stats.record_action(signal.action.value.upper(), requested, self.config.execution.strategy_type, "filled")
                self._window_stats.mark_execution_event()
                if signal.action == SignalAction.FLIP:
                    self._window_flip_count = getattr(self, "_window_flip_count", 0) + 1
                self._write_activity_event(
                    now=now,
                    event_type="execution",
                    action=signal.action.value,
                    side=self.state.position.side,
                    size=requested,
                    price=self.state.position.entry_price,
                    reason=signal.reason,
                    snapshot=snapshot,
                )
                self.state.position = None
                self._window_stats.mark_position(None, 0.0)
                self._clear_inflight()
            elif filled > 0:
                self.state.position.size = max(0.0, self.state.position.size - filled)
                self._window_stats.mark_position(self.state.position.side, self.state.position.size)
                LOGGER.warning("EXECUTION partial_close requested=%.4f filled=%.4f remaining=%.4f", requested, filled, self.state.position.size)
                return
            else:
                LOGGER.warning("EXECUTION close_submitted_unfilled status=%s", report.get("status"))
                return

            # Safer flip handling: do not reopen in same tick after close.
            if signal.action == SignalAction.FLIP:
                LOGGER.info("EXECUTION flip_deferred next_tick=true")
                return

        if signal.action in {SignalAction.OPEN, SignalAction.FLIP}:
            selected_book = yes_book if signal.side == OutcomeSide.YES else no_book
            signal_price = snapshot.yes_price if signal.side == OutcomeSide.YES else snapshot.no_price
            entry_price = selected_book.execution_price_for(signal_price)
            if entry_price is None:
                LOGGER.info(
                    "STRATEGY action=%s side=%s size=%.4f reason=missing_execution_price tau=%.1f fair_yes=%.3f fair_no=%.3f edge_yes=%s edge_no=%s",
                    signal.action.value,
                    signal.side.value,
                    signal.size,
                    tau_seconds,
                    snapshot.fair_yes,
                    snapshot.fair_no,
                    _fmt(snapshot.edge_yes),
                    _fmt(snapshot.edge_no),
                )
                return

            self._window_stats.record_action(signal.action.value.upper(), signal.size, self.config.execution.strategy_type, "attempt")
            self._mark_inflight(now, "open_%s" % signal.action.value)
            try:
                candidate_position = self.executor.open_position(self.market, signal, entry_price)
            except Exception as exc:
                LOGGER.error("EXECUTION open_failed action=%s side=%s size=%.4f error=%s", signal.action.value, signal.side.value, signal.size, exc)
                return

            report = self._execution_report()
            requested = max(0.0, float(report.get("requested_size", signal.size)))
            filled = max(0.0, float(report.get("filled_size", 0.0)))
            if not report.get("success", False) or filled <= 0.0:
                LOGGER.warning("EXECUTION open_unconfirmed status=%s error=%s", report.get("status"), report.get("error"))
                return

            if filled < requested:
                candidate_position.size = filled
                LOGGER.warning("EXECUTION partial_open requested=%.4f filled=%.4f", requested, filled)
            self.state.position = candidate_position
            self._window_entry_count = getattr(self, "_window_entry_count", 0) + 1
            if not hasattr(self, "_seen_entry_sides"):
                self._seen_entry_sides = set()
            self._seen_entry_sides.add(signal.side)
            self._window_stats.record_action(signal.action.value.upper(), filled, self.config.execution.strategy_type, "filled")
            self._window_stats.record_fill(signal.side, filled, entry_price)
            self._window_stats.mark_execution_event()
            self._write_activity_event(
                now=now,
                event_type="fill",
                action=signal.action.value,
                side=signal.side,
                size=filled,
                price=entry_price,
                reason=signal.reason,
                snapshot=snapshot,
            )
            self._window_stats.mark_position(signal.side, candidate_position.size)
            if filled >= requested - 1e-9:
                self._clear_inflight()
            LOGGER.info(
                "STRATEGY action=%s side=%s requested=%.4f filled=%.4f reason=%s tau=%.1f price=%.4f fair_yes=%.3f fair_no=%.3f edge_yes=%s edge_no=%s",
                signal.action.value,
                signal.side.value,
                requested,
                filled,
                signal.reason,
                tau_seconds,
                entry_price,
                snapshot.fair_yes,
                snapshot.fair_no,
                _fmt(snapshot.edge_yes),
                _fmt(snapshot.edge_no),
            )

    def _apply_signal_risk_controls(self, signal):
        if signal.action == SignalAction.HOLD:
            return signal

        snapshot = signal.snapshot
        if (
            getattr(self.config.strategy, "require_both_prices", False)
            and snapshot is not None
            and (snapshot.yes_price is None or snapshot.no_price is None)
        ):
            return TradeSignal(SignalAction.HOLD, reason="incomplete_market_prices", snapshot=snapshot)

        if signal.action == SignalAction.OPEN:
            if getattr(self, "_window_entry_count", 0) >= getattr(self.config.strategy, "max_entries_per_window", 999999):
                return TradeSignal(SignalAction.HOLD, reason="entry_limit_reached", snapshot=snapshot)
            if (not getattr(self.config.strategy, "allow_same_side_reentry", True)) and signal.side in getattr(self, "_seen_entry_sides", set()):
                return TradeSignal(SignalAction.HOLD, reason="same_side_reentry_blocked", snapshot=snapshot)

        if signal.action == SignalAction.FLIP:
            if getattr(self, "_window_flip_count", 0) >= getattr(self.config.strategy, "max_flips_per_window", 999999):
                return TradeSignal(SignalAction.HOLD, reason="flip_limit_reached", snapshot=snapshot)

        return signal

    def _log_status(self, now, snapshot):
        current_second = int(now.timestamp())
        if self._last_status_second == current_second:
            return
        self._last_status_second = current_second

        position = "flat"
        if self.state.position is not None:
            position = "%s@%.4f x %.4f" % (
                self.state.position.side.value,
                self.state.position.entry_price,
                self.state.position.size,
            )

        LOGGER.info(
            "STATUS window=%s tau=%ss spot=%.2f x_t=%.6f yes_price=%s no_price=%s fair_yes=%.3f fair_no=%.3f edge_yes=%s edge_no=%s pos=%s",
            self.market.slug or self.market.condition_id,
            int(snapshot.tau_seconds),
            self.roll.last_price,
            snapshot.x_t,
            _fmt(snapshot.yes_price),
            _fmt(snapshot.no_price),
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
                "yesPrice": snapshot.yes_price,
                "noPrice": snapshot.no_price,
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
            yes_book = self._effective_book(self.market.yes_token_id, now)
            no_book = self._effective_book(self.market.no_token_id, now)
            position = "flat"
            if self.state.position is not None:
                position = "%s@%.4f x %.4f" % (
                    self.state.position.side.value,
                    self.state.position.entry_price,
                    self.state.position.size,
                )
            LOGGER.info(
                "WINDOW window=%s phase=collecting tau=%ss active_in=%ss spot=%s x_t=%s yes_price=%s no_price=%s pos=%s",
                self.market.slug or self.market.condition_id,
                int(tau_seconds),
                int(tau_seconds - self.config.logging.active_only_last_seconds),
                _fmt(self._latest_spot_price),
                _fmt(self.roll.latest_x()),
                _fmt(yes_book.market_price()),
                _fmt(no_book.market_price()),
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
        self._window_entry_count = 0
        self._window_flip_count = 0
        self._seen_entry_sides = set()
        self.market = market
        self.state.market = market
        self.state.last_snapshot = None
        self.state.position = None
        self.roll = RollingState(self.config.strategy)
        self._latest_books = {}
        self._last_usable_books = {}
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
        next_start = self.market.end_time or current_window_start(now)
        next_market = resolve_market_for_window(self.config.market, next_start)
        self._ensure_market_times(next_market)
        self._cancel_book_tasks()
        self._reset_for_market(next_market)
        self._start_book_tasks()

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
            "yesPrice": snapshot.yes_price,
            "noPrice": snapshot.no_price,
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

    def _maybe_cache_book(self, asset_id, book):
        if book is None:
            return
        if (
            book.bid is None
            and book.ask is None
            and book.bid_size <= 0
            and book.ask_size <= 0
            and book.last_trade_price is None
        ):
            return
        self._last_usable_books[asset_id] = book

    def _effective_book(self, asset_id, now):
        latest_book = self._latest_books.get(asset_id)
        fallback_book = self._last_usable_books.get(asset_id)
        if latest_book is None and fallback_book is None:
            return BestBidAsk(asset_id=asset_id, bid=None, ask=None)

        reference_ms = int(now.timestamp() * 1000)
        max_age_ms = int(self.config.strategy.book_fallback_max_age_seconds * 1000)

        if latest_book is not None:
            if latest_book.timestamp_ms:
                if (reference_ms - latest_book.timestamp_ms) <= max_age_ms:
                    return latest_book
            else:
                return latest_book

        if fallback_book is not None:
            if fallback_book.timestamp_ms:
                if (reference_ms - fallback_book.timestamp_ms) <= max_age_ms:
                    return fallback_book
            else:
                return fallback_book

        return BestBidAsk(asset_id=asset_id, bid=None, ask=None)

    def _start_background_tasks(self):
        self._tasks["prices"] = asyncio.create_task(self._consume_prices())
        self._start_book_tasks()
        self._tasks["health"] = asyncio.create_task(self._health_loop())

    def _start_book_tasks(self):
        for asset_id in self._market_asset_ids():
            task_name = "book:%s" % asset_id
            self._tasks[task_name] = asyncio.create_task(self._consume_books(asset_id))

    def _cancel_book_tasks(self):
        for task_name in list(self._tasks.keys()):
            if task_name.startswith("book:"):
                self._tasks[task_name].cancel()
                del self._tasks[task_name]

    def _market_asset_ids(self):
        return [self.market.yes_token_id, self.market.no_token_id]

    def _has_any_book(self):
        return any(
            asset_id in self._latest_books or asset_id in self._last_usable_books
            for asset_id in self._market_asset_ids()
        )

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
        elif task_name.startswith("book:"):
            asset_id = payload.get("asset_id")
            if asset_id:
                self._tasks[task_name] = asyncio.create_task(self._consume_books(asset_id))

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
