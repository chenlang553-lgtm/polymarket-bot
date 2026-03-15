from math import sqrt

from .config import PriceSizeRule, SizeBucket
from .market_state import RollingState
from .math_utils import clamp, logistic, logit, normal_cdf
from .models import BestBidAsk, OutcomeSide, Position, SignalAction, StrategySnapshot, TradeSignal


def _weighted_sigma(tau_seconds, sigma_10, sigma_30, sigma_slow):
    if 40 < tau_seconds <= 60:
        return 0.2 * sigma_10 + 0.4 * sigma_30 + 0.4 * sigma_slow
    if 15 < tau_seconds <= 40:
        return 0.4 * sigma_10 + 0.35 * sigma_30 + 0.25 * sigma_slow
    if 5 < tau_seconds <= 15:
        return 0.65 * sigma_10 + 0.25 * sigma_30 + 0.10 * sigma_slow
    return 0.5 * sigma_10 + 0.3 * sigma_30 + 0.2 * sigma_slow


class StrategyEngine(object):
    def __init__(self, config):
        self.config = config
        if not getattr(self.config, "price_size_rules", None):
            self.config.price_size_rules = default_price_size_rules()

    def compute_snapshot(
        self,
        state,
        book_yes,
        book_no,
        tau_seconds,
        trade_imbalance=0.0,
        previous_fair_yes=None,
        yes_price=None,
        no_price=None,
    ):
        sigma_10 = state.sigma_10()
        sigma_30 = state.sigma_30()
        sigma_slow = state.sigma_slow()
        sigma_eff = _weighted_sigma(tau_seconds, sigma_10, sigma_30, sigma_slow)
        jump_adjusted = False
        outlier_adjusted = False

        if sigma_30 > 0 and (sigma_10 / sigma_30) > self.config.jump_ratio_threshold:
            sigma_eff *= self.config.jump_sigma_multiplier
            jump_adjusted = True

        if sigma_10 > 0 and state.max_recent_abs_return(10) > (self.config.outlier_threshold * sigma_10):
            sigma_eff *= self.config.outlier_sigma_multiplier
            outlier_adjusted = True

        x_t = state.latest_x()
        momentum_5 = state.momentum(5)
        momentum_15 = state.momentum(15)
        drift = (
            self.config.drift_weight_m5 * momentum_5 * tau_seconds
            + self.config.drift_weight_m15 * momentum_15 * tau_seconds
        )

        sigma_eff = max(self.config.sigma_floor, sigma_eff)
        denom = max(1e-6, sigma_eff * sqrt(max(1.0, tau_seconds)))
        z_score = (x_t + drift) / denom
        fair_yes = normal_cdf(z_score)

        # Optional microstructure layer from the document.
        fair_yes = logistic(logit(fair_yes) + 0.08 * book_yes.obi + 0.05 * trade_imbalance)
        fair_yes = self._smooth_fair_value(fair_yes, previous_fair_yes, tau_seconds)
        fair_cap = clamp(self.config.fair_value_cap, 0.50, 0.999)
        fair_yes = clamp(fair_yes, 1.0 - fair_cap, fair_cap)
        fair_no = 1.0 - fair_yes

        yes_price = book_yes.market_price() if yes_price is None else yes_price
        no_price = book_no.market_price() if no_price is None else no_price
        edge_yes = None if yes_price is None else fair_yes - yes_price
        edge_no = None if no_price is None else fair_no - no_price

        return StrategySnapshot(
            fair_yes=fair_yes,
            fair_no=fair_no,
            yes_price=yes_price,
            no_price=no_price,
            edge_yes=edge_yes,
            edge_no=edge_no,
            sigma_10=sigma_10,
            sigma_30=sigma_30,
            sigma_slow=sigma_slow,
            sigma_eff=sigma_eff,
            momentum_5=momentum_5,
            momentum_15=momentum_15,
            drift=drift,
            x_t=x_t,
            tau_seconds=tau_seconds,
            jump_adjusted=jump_adjusted,
            outlier_adjusted=outlier_adjusted,
            obi=book_yes.obi,
            trade_imbalance=trade_imbalance,
        )

    def _smooth_fair_value(self, fair_yes, previous_fair_yes, tau_seconds):
        if previous_fair_yes is None:
            return fair_yes
        if tau_seconds > self.config.fair_smoothing_start_seconds:
            return fair_yes
        alpha = clamp(self.config.fair_smoothing_alpha, 0.05, 1.0)
        return alpha * fair_yes + (1.0 - alpha) * previous_fair_yes

    def evaluate(self, snapshot, book_yes, book_no, position):
        if not (
            self.config.decision_window_end_seconds
            <= snapshot.tau_seconds
            <= self.config.decision_window_start_seconds
        ):
            return TradeSignal(SignalAction.HOLD, reason="outside_decision_window", snapshot=snapshot)

        if self.config.require_both_prices and (snapshot.yes_price is None or snapshot.no_price is None):
            return TradeSignal(SignalAction.HOLD, reason="incomplete_market_prices", snapshot=snapshot)

        best_side, best_edge = self._best_side(snapshot)

        if position is not None:
            current_edge = snapshot.edge_yes if position.side == OutcomeSide.YES else snapshot.edge_no
            if current_edge is None:
                return TradeSignal(SignalAction.HOLD, reason="missing_current_edge", snapshot=snapshot)
            if best_side is not None and position.side != best_side and best_edge >= self.config.min_edge:
                selected_book = book_yes if best_side == OutcomeSide.YES else book_no
                selected_price = snapshot.yes_price if best_side == OutcomeSide.YES else snapshot.no_price
                if selected_price is None:
                    return TradeSignal(SignalAction.HOLD, reason="missing_market_price", snapshot=snapshot)
                if selected_book.spread is not None and selected_book.spread > self.config.max_spread:
                    return TradeSignal(SignalAction.HOLD, reason="spread_too_wide", snapshot=snapshot)
                if selected_book.bid_size > 0 and selected_book.ask_size > 0 and selected_book.top_size < self.config.min_top_of_book_size:
                    return TradeSignal(SignalAction.HOLD, reason="top_of_book_too_thin", snapshot=snapshot)
                return TradeSignal(
                    action=SignalAction.FLIP,
                    side=best_side,
                    size=self._size_for_price(selected_price),
                    reason="flip_signal",
                    snapshot=snapshot,
                )
            if snapshot.tau_seconds > 15 and current_edge <= self.config.edge_decay_close_threshold:
                return TradeSignal(
                    action=SignalAction.CLOSE,
                    side=position.side,
                    size=position.size,
                    reason="edge_decayed",
                    snapshot=snapshot,
                )
            return TradeSignal(SignalAction.HOLD, reason="position_unchanged", snapshot=snapshot)

        if best_side is None or best_edge < self.config.min_edge:
            return TradeSignal(SignalAction.HOLD, reason="edge_too_small", snapshot=snapshot)
        if abs(snapshot.x_t) < self.config.min_abs_x:
            return TradeSignal(SignalAction.HOLD, reason="inside_no_trade_zone", snapshot=snapshot)

        selected_book = book_yes if best_side == OutcomeSide.YES else book_no
        selected_price = snapshot.yes_price if best_side == OutcomeSide.YES else snapshot.no_price
        if selected_price is None:
            return TradeSignal(SignalAction.HOLD, reason="missing_market_price", snapshot=snapshot)
        if selected_book.spread is not None and selected_book.spread > self.config.max_spread:
            return TradeSignal(SignalAction.HOLD, reason="spread_too_wide", snapshot=snapshot)
        if selected_book.bid_size > 0 and selected_book.ask_size > 0 and selected_book.top_size < self.config.min_top_of_book_size:
            return TradeSignal(SignalAction.HOLD, reason="top_of_book_too_thin", snapshot=snapshot)

        return TradeSignal(
            action=SignalAction.OPEN,
            side=best_side,
            size=self._size_for_price(selected_price),
            reason="open_edge_signal",
            snapshot=snapshot,
        )

    def _best_side(self, snapshot):
        candidates = []
        if snapshot.edge_yes is not None:
            candidates.append((OutcomeSide.YES, snapshot.edge_yes))
        if snapshot.edge_no is not None:
            candidates.append((OutcomeSide.NO, snapshot.edge_no))
        if not candidates:
            return None, 0.0
        return max(candidates, key=lambda item: item[1])

    def _size_for_price(self, price):
        ordered = sorted(self.config.price_size_rules, key=lambda item: item.max_price)
        if ordered:
            for rule in ordered:
                if price <= rule.max_price:
                    return rule.size
            return ordered[-1].size
        ordered = sorted(self.config.size_buckets, key=lambda item: item.min_edge)
        return ordered[0].size if ordered else 0.0


def default_size_buckets():
    return [
        SizeBucket(min_edge=0.08, size=0.5),
        SizeBucket(min_edge=0.12, size=1.0),
        SizeBucket(min_edge=0.18, size=1.5),
    ]


def default_price_size_rules():
    return [
        PriceSizeRule(max_price=0.10, size=500.0),
        PriceSizeRule(max_price=0.50, size=100.0),
        PriceSizeRule(max_price=1.00, size=60.0),
    ]
