from datetime import datetime
from pathlib import Path
from copy import deepcopy
import os
import json

from .models import OutcomeSide


class SizeBucket:
    def __init__(self, min_edge, size):
        self.min_edge = min_edge
        self.size = size


class MarketConfig:
    def __init__(
        self,
        market_slug="",
        condition_id="",
        slug_prefix="btc-updown-5m",
        yes_token_id="",
        no_token_id="",
        trade_side=OutcomeSide.YES,
        start_time_utc=None,
        end_time_utc=None,
        auto_roll_windows=True,
    ):
        self.market_slug = market_slug
        self.condition_id = condition_id
        self.slug_prefix = slug_prefix
        self.yes_token_id = yes_token_id
        self.no_token_id = no_token_id
        self.trade_side = trade_side
        self.start_time_utc = start_time_utc
        self.end_time_utc = end_time_utc
        self.auto_roll_windows = auto_roll_windows


class PriceFeedConfig:
    def __init__(self, symbol="btcusdt", source_topic="crypto_prices", provider="binance"):
        self.symbol = symbol
        self.source_topic = source_topic
        self.provider = provider


class StrategyConfig:
    def __init__(
        self,
        decision_window_start_seconds=45,
        decision_window_end_seconds=8,
        min_edge=0.04,
        max_spread=0.03,
        min_top_of_book_size=25.0,
        sigma_slow_lambda=0.97,
        jump_ratio_threshold=1.8,
        jump_sigma_multiplier=1.10,
        outlier_threshold=2.5,
        outlier_sigma_multiplier=1.15,
        drift_weight_m5=0.2,
        drift_weight_m15=0.1,
        fair_smoothing_start_seconds=20,
        fair_smoothing_alpha=0.35,
        book_fallback_max_age_seconds=3,
        size_buckets=None,
    ):
        self.decision_window_start_seconds = decision_window_start_seconds
        self.decision_window_end_seconds = decision_window_end_seconds
        self.min_edge = min_edge
        self.max_spread = max_spread
        self.min_top_of_book_size = min_top_of_book_size
        self.sigma_slow_lambda = sigma_slow_lambda
        self.jump_ratio_threshold = jump_ratio_threshold
        self.jump_sigma_multiplier = jump_sigma_multiplier
        self.outlier_threshold = outlier_threshold
        self.outlier_sigma_multiplier = outlier_sigma_multiplier
        self.drift_weight_m5 = drift_weight_m5
        self.drift_weight_m15 = drift_weight_m15
        self.fair_smoothing_start_seconds = fair_smoothing_start_seconds
        self.fair_smoothing_alpha = fair_smoothing_alpha
        self.book_fallback_max_age_seconds = book_fallback_max_age_seconds
        self.size_buckets = size_buckets or []


class ExecutionConfig:
    def __init__(self, mode="paper", order_type="fok", strategy_version=1, strategy_profile="main", strategy_type="fair_probability"):
        self.mode = mode
        self.order_type = order_type
        self.strategy_version = strategy_version
        self.strategy_profile = strategy_profile
        self.strategy_type = strategy_type


class WalletConfig:
    def __init__(self, private_key="", funder="", signature_type=0, chain_id=137):
        self.private_key = private_key
        self.funder = funder
        self.signature_type = signature_type
        self.chain_id = chain_id


class LoggingConfig:
    def __init__(self, level="INFO", active_only_last_seconds=60, window_close_path="window_close.jsonl", activity_path="activity.jsonl", market_state_path="market_state.jsonl", health_log_interval_seconds=15, stale_data_threshold_seconds=10, shutdown_grace_seconds=5, supervisor_restart_backoff_seconds=2):
        self.level = level
        self.active_only_last_seconds = active_only_last_seconds
        self.window_close_path = window_close_path
        self.activity_path = activity_path
        self.market_state_path = market_state_path
        self.health_log_interval_seconds = health_log_interval_seconds
        self.stale_data_threshold_seconds = stale_data_threshold_seconds
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self.supervisor_restart_backoff_seconds = supervisor_restart_backoff_seconds


class AppConfig:
    def __init__(self, market, price_feed, strategy, execution, wallet, logging):
        self.market = market
        self.price_feed = price_feed
        self.strategy = strategy
        self.execution = execution
        self.wallet = wallet
        self.logging = logging


def apply_iteration_paths(config, iteration, project_root):
    if not iteration:
        return config
    logs_dir = os.path.join(project_root, "logs", iteration)
    data_dir = os.path.join(project_root, "data", iteration)
    os.makedirs(logs_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)
    config.logging.window_close_path = os.path.join(data_dir, "window_close.jsonl")
    config.logging.activity_path = os.path.join(data_dir, "activity.jsonl")
    config.logging.market_state_path = os.path.join(data_dir, "market_state.jsonl")
    return {
        "logs_dir": logs_dir,
        "data_dir": data_dir,
        "service_log_path": os.path.join(logs_dir, "service.log"),
    }


def _parse_datetime(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _deep_merge(base, override):
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path, profile=None):
    raw = json.loads(Path(path).read_text())
    if profile:
        profiles = raw.get("profiles", {})
        if profile not in profiles:
            raise ValueError("unknown profile: %s" % profile)
        raw = _deep_merge(raw, profiles[profile])
        raw.setdefault("execution", {})
        raw["execution"]["strategy_profile"] = profile
    market = raw.get("market", {})
    strategy = raw.get("strategy", {})
    return AppConfig(
        market=MarketConfig(
            market_slug=market.get("market_slug", ""),
            condition_id=market.get("condition_id", ""),
            slug_prefix=market.get("slug_prefix", "btc-updown-5m"),
            yes_token_id=market.get("yes_token_id", ""),
            no_token_id=market.get("no_token_id", ""),
            trade_side=OutcomeSide(market.get("trade_side", "yes")),
            start_time_utc=_parse_datetime(market.get("start_time_utc", "")),
            end_time_utc=_parse_datetime(market.get("end_time_utc", "")),
            auto_roll_windows=market.get("auto_roll_windows", True),
        ),
        price_feed=PriceFeedConfig(**raw.get("price_feed", {})),
        strategy=StrategyConfig(
            decision_window_start_seconds=strategy.get("decision_window_start_seconds", 45),
            decision_window_end_seconds=strategy.get("decision_window_end_seconds", 8),
            min_edge=strategy.get("min_edge", 0.04),
            max_spread=strategy.get("max_spread", 0.03),
            min_top_of_book_size=strategy.get("min_top_of_book_size", 25.0),
            sigma_slow_lambda=strategy.get("sigma_slow_lambda", 0.97),
            jump_ratio_threshold=strategy.get("jump_ratio_threshold", 1.8),
            jump_sigma_multiplier=strategy.get("jump_sigma_multiplier", 1.10),
            outlier_threshold=strategy.get("outlier_threshold", 2.5),
            outlier_sigma_multiplier=strategy.get("outlier_sigma_multiplier", 1.15),
            drift_weight_m5=strategy.get("drift_weight_m5", 0.2),
            drift_weight_m15=strategy.get("drift_weight_m15", 0.1),
            fair_smoothing_start_seconds=strategy.get("fair_smoothing_start_seconds", 20),
            fair_smoothing_alpha=strategy.get("fair_smoothing_alpha", 0.35),
            book_fallback_max_age_seconds=strategy.get("book_fallback_max_age_seconds", 3),
            size_buckets=[
                SizeBucket(min_edge=item["min_edge"], size=item["size"])
                for item in strategy.get("size_buckets", [])
            ],
        ),
        execution=ExecutionConfig(**raw.get("execution", {})),
        wallet=WalletConfig(**raw.get("wallet", {})),
        logging=LoggingConfig(**raw.get("logging", {})),
    )
