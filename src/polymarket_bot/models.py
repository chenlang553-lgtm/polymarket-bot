from datetime import datetime
from enum import Enum
from typing import List, Optional


class OutcomeSide(str, Enum):
    YES = "yes"
    NO = "no"


class SignalAction(str, Enum):
    HOLD = "hold"
    OPEN = "open"
    CLOSE = "close"
    FLIP = "flip"


class OrderBookLevel:
    def __init__(self, price, size):
        self.price = price
        self.size = size


class BestBidAsk:
    def __init__(self, asset_id, bid, ask, bid_size=0.0, ask_size=0.0, timestamp_ms=0):
        self.asset_id = asset_id
        self.bid = bid
        self.ask = ask
        self.bid_size = bid_size
        self.ask_size = ask_size
        self.timestamp_ms = timestamp_ms

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def midpoint(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2.0

    @property
    def top_size(self) -> float:
        return min(self.bid_size, self.ask_size)

    @property
    def obi(self) -> float:
        total = self.bid_size + self.ask_size
        if total <= 0:
            return 0.0
        return (self.bid_size - self.ask_size) / total


class PriceTick:
    def __init__(self, symbol, price, timestamp_ms):
        self.symbol = symbol
        self.price = price
        self.timestamp_ms = timestamp_ms


class MarketDefinition:
    def __init__(
        self,
        question,
        slug,
        condition_id,
        yes_token_id,
        no_token_id,
        start_time=None,
        end_time=None,
        neg_risk=False,
        tick_size=0.01,
        window_size_seconds=300,
    ):
        self.question = question
        self.slug = slug
        self.condition_id = condition_id
        self.yes_token_id = yes_token_id
        self.no_token_id = no_token_id
        self.start_time = start_time
        self.end_time = end_time
        self.neg_risk = neg_risk
        self.tick_size = tick_size
        self.window_size_seconds = window_size_seconds


class Position:
    def __init__(self, side, size, entry_price, edge_at_entry, opened_at):
        self.side = side
        self.size = size
        self.entry_price = entry_price
        self.edge_at_entry = edge_at_entry
        self.opened_at = opened_at


class StrategySnapshot:
    def __init__(
        self,
        fair_yes,
        fair_no,
        edge_yes,
        edge_no,
        sigma_10,
        sigma_30,
        sigma_slow,
        sigma_eff,
        momentum_5,
        momentum_15,
        drift,
        x_t,
        tau_seconds,
        jump_adjusted,
        outlier_adjusted,
        obi=0.0,
        trade_imbalance=0.0,
    ):
        self.fair_yes = fair_yes
        self.fair_no = fair_no
        self.edge_yes = edge_yes
        self.edge_no = edge_no
        self.sigma_10 = sigma_10
        self.sigma_30 = sigma_30
        self.sigma_slow = sigma_slow
        self.sigma_eff = sigma_eff
        self.momentum_5 = momentum_5
        self.momentum_15 = momentum_15
        self.drift = drift
        self.x_t = x_t
        self.tau_seconds = tau_seconds
        self.jump_adjusted = jump_adjusted
        self.outlier_adjusted = outlier_adjusted
        self.obi = obi
        self.trade_imbalance = trade_imbalance


class TradeSignal:
    def __init__(self, action, side=None, size=0.0, reason="", snapshot=None):
        self.action = action
        self.side = side
        self.size = size
        self.reason = reason
        self.snapshot = snapshot


class RuntimeState:
    def __init__(self, market, book=None, position=None, last_snapshot=None, recent_trade_signs=None):
        self.market = market
        self.book = book
        self.position = position
        self.last_snapshot = last_snapshot
        self.recent_trade_signs = recent_trade_signs or []
