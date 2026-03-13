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

    def is_valid(self, max_spread=None, min_size=0.0) -> bool:
        if self.bid is None or self.ask is None:
            return False
        if self.bid <= 0.0 or self.ask <= 0.0 or self.bid >= self.ask:
            return False
        if self.bid <= 0.01 and self.ask >= 0.99:
            return False
        if max_spread is not None and self.spread is not None and self.spread > max_spread:
            return False
        if min_size > 0 and self.top_size < min_size:
            return False
        return True

    def effective_price(self, max_spread=None, min_size=0.0):
        if not self.is_valid(max_spread=max_spread, min_size=min_size):
            return None
        return self.midpoint

    def merged_with(self, fallback):
        if fallback is None:
            return self
        return BestBidAsk(
            asset_id=self.asset_id,
            bid=self.bid if self.bid is not None else fallback.bid,
            ask=self.ask if self.ask is not None else fallback.ask,
            bid_size=self.bid_size if self.bid_size > 0 else fallback.bid_size,
            ask_size=self.ask_size if self.ask_size > 0 else fallback.ask_size,
            timestamp_ms=self.timestamp_ms or fallback.timestamp_ms,
        )


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
        yes_price,
        no_price,
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
        self.yes_price = yes_price
        self.no_price = no_price
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


class RuntimeHealth:
    def __init__(self):
        self.price_updates = 0
        self.book_updates = 0
        self.price_reconnects = 0
        self.book_reconnects = 0
        self.last_price_at_ms = 0
        self.last_book_at_ms = 0
        self.last_health_log_at_ms = 0
        self.last_error = ""
        self.supervisor_restarts = 0
        self.shutdowns = 0


class WindowStats:
    def __init__(self):
        self.total_cost = 0.0
        self.total_fees = 0.0
        self.total_outlay = 0.0
        self.realized_pnl = 0.0
        self.worst_case_loss = 0.0
        self.open_order_count = 0
        self.fill_count = 0
        self.fill_count_by_side = {}
        self.fill_qty_by_side = {}
        self.fill_notional_by_side = {}
        self.action_counts = {}
        self.action_qty = {}
        self.strategy_type_counts = {}
        self.primary_strategy_type = None
        self.execution_status_counts = {}
        self.last_yes_ask = None
        self.last_yes_bid = None
        self.last_fair_yes = None
        self.open_position_side = None
        self.open_position_size = 0.0
        self.execution_events = 0

    def record_action(self, action, qty=0.0, strategy_type=None, execution_status=None):
        self.action_counts[action] = self.action_counts.get(action, 0) + 1
        if qty:
            self.action_qty[action] = self.action_qty.get(action, 0.0) + qty
        if strategy_type:
            self.strategy_type_counts[strategy_type] = self.strategy_type_counts.get(strategy_type, 0) + 1
            if self.primary_strategy_type is None:
                self.primary_strategy_type = strategy_type
        if execution_status:
            self.execution_status_counts[execution_status] = self.execution_status_counts.get(execution_status, 0) + 1

    def record_fill(self, side, qty, price, fee=0.0):
        direction = "Up" if side == OutcomeSide.YES else "Down"
        self.fill_count += 1
        self.fill_count_by_side[direction] = self.fill_count_by_side.get(direction, 0) + 1
        self.fill_qty_by_side[direction] = self.fill_qty_by_side.get(direction, 0.0) + qty
        self.fill_notional_by_side[direction] = self.fill_notional_by_side.get(direction, 0.0) + (qty * price)
        self.total_cost += qty * price
        self.total_fees += fee
        self.total_outlay = self.total_cost + self.total_fees
        self.worst_case_loss = self.total_outlay

    def mark_position(self, side, size):
        self.open_position_side = None if side is None else ("Up" if side == OutcomeSide.YES else "Down")
        self.open_position_size = size or 0.0

    def mark_execution_event(self):
        self.execution_events += 1

    def finalize(self, final_direction):
        prices = {"up": 0.99 if final_direction == "Up" else 0.01, "down": 0.99 if final_direction == "Down" else 0.01}
        pnl_if_up = self.fill_qty_by_side.get("Up", 0.0) - self.total_outlay
        pnl_if_down = self.fill_qty_by_side.get("Down", 0.0) - self.total_outlay
        self.realized_pnl = pnl_if_up if final_direction == "Up" else pnl_if_down
        coverage = 0.0
        total_qty = self.fill_qty_by_side.get("Up", 0.0) + self.fill_qty_by_side.get("Down", 0.0)
        if total_qty > 0:
            coverage = min(self.fill_qty_by_side.get("Up", 0.0), self.fill_qty_by_side.get("Down", 0.0)) / total_qty
        up_qty = self.fill_qty_by_side.get("Up", 0.0)
        down_qty = self.fill_qty_by_side.get("Down", 0.0)
        up_avg = 0.0 if up_qty == 0 else self.fill_notional_by_side.get("Up", 0.0) / up_qty
        down_avg = 0.0 if down_qty == 0 else self.fill_notional_by_side.get("Down", 0.0) / down_qty
        return {
            "prices": prices,
            "metrics": {
                "totalCost": self.total_cost,
                "totalFees": self.total_fees,
                "totalOutlay": self.total_outlay,
                "pnlIfUp": pnl_if_up,
                "pnlIfDown": pnl_if_down,
                "pnlMin": min(pnl_if_up, pnl_if_down),
                "pnlMax": max(pnl_if_up, pnl_if_down),
                "lockedPnl": self.realized_pnl,
                "coverage": coverage,
                "upAvg": up_avg,
                "downAvg": down_avg,
                "bias": 0,
                "balanceRatio": 0 if down_qty == 0 else up_qty / down_qty,
            },
            "realizedPnl": self.realized_pnl,
            "worstCaseLoss": self.worst_case_loss,
            "openOrderCount": self.open_order_count,
            "activity": {
                "actionCounts": self.action_counts,
                "actionQty": self.action_qty,
                "strategyTypeCounts": self.strategy_type_counts,
                "primaryStrategyType": self.primary_strategy_type,
                "executionStatusCounts": self.execution_status_counts,
                "executionEventCount": self.execution_events,
                "fillCount": self.fill_count,
                "fillCountBySide": self.fill_count_by_side,
                "fillQtyBySide": self.fill_qty_by_side,
                "fillNotionalBySide": self.fill_notional_by_side,
            },
            "position": {
                "openSide": self.open_position_side,
                "openSize": self.open_position_size,
            },
        }
