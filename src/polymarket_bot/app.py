import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import logging
from math import isnan

from .config import AppConfig
from .execution import build_executor
from .gamma import resolve_market
from .market_state import RollingState
from .models import BestBidAsk, OutcomeSide, RuntimeState, SignalAction
from .strategy import StrategyEngine, default_size_buckets
from .ws import market_book_stream, rtds_price_stream


LOGGER = logging.getLogger(__name__)


class TradingApplication:
    def __init__(self, config):
        if not config.strategy.size_buckets:
            config.strategy.size_buckets = default_size_buckets()
        self.config = config
        self.market = resolve_market(config.market)
        self.state = RuntimeState(market=self.market)
        self.roll = RollingState(config.strategy)
        self.strategy = StrategyEngine(config.strategy)
        self.executor = build_executor(config.execution, config.wallet)
        self._latest_book = None
        self._latest_trade_imbalance = 0.0
        self._queue = asyncio.Queue()
        self._last_status_second = None

    async def run(self):
        side_asset = self.market.yes_token_id if self.config.market.trade_side == OutcomeSide.YES else self.market.no_token_id
        producers = [
            asyncio.create_task(self._consume_prices()),
            asyncio.create_task(self._consume_books(side_asset)),
        ]
        try:
            while True:
                kind, payload = await self._queue.get()
                if kind == "price":
                    self._handle_price(payload)
                elif kind == "book":
                    self._latest_book = payload
                if self._latest_book is not None and self.roll.open_price is not None:
                    self._tick()
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
        x_t = self.roll.update_price(tick.price)
        LOGGER.debug("spot tick symbol=%s price=%.2f x_t=%.6f", tick.symbol, tick.price, x_t)

    def _tick(self):
        if self.market.end_time is None:
            raise RuntimeError("market end_time is required for live strategy timing")
        now = datetime.now(timezone.utc)
        tau_seconds = (self.market.end_time - now).total_seconds()
        if tau_seconds <= 0:
            LOGGER.info("market window is over")
            raise SystemExit(0)

        snapshot = self.strategy.compute_snapshot(
            state=self.roll,
            book_yes=self._latest_book,
            tau_seconds=tau_seconds,
            trade_imbalance=self._latest_trade_imbalance,
        )
        self.state.last_snapshot = snapshot
        self._log_status(now, snapshot)
        signal = self.strategy.evaluate(snapshot, self._latest_book, self.state.position)
        if signal.action == SignalAction.HOLD:
            return

        if signal.action in {SignalAction.CLOSE, SignalAction.FLIP} and self.state.position is not None:
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

        if signal.action in {SignalAction.OPEN, SignalAction.FLIP}:
            ask_price = self._latest_book.ask if signal.side == OutcomeSide.YES else max(0.001, 1.0 - (self._latest_book.bid or 0.0))
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

    def _log_status(self, now, snapshot):
        if snapshot.tau_seconds > 60:
            return
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


def _fmt(value):
    if value is None:
        return "None"
    return f"{value:.4f}"
