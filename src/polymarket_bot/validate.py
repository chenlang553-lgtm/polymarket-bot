from __future__ import print_function


def validate_config(config):
    errors = []
    warnings = []

    if config.strategy.decision_window_start_seconds <= config.strategy.decision_window_end_seconds:
        errors.append("strategy.decision_window_start_seconds must be greater than strategy.decision_window_end_seconds")
    if config.logging.active_only_last_seconds < config.strategy.decision_window_start_seconds:
        warnings.append("logging.active_only_last_seconds is smaller than the decision start window")
    if config.strategy.min_edge <= 0 or config.strategy.min_edge >= 1:
        errors.append("strategy.min_edge must be between 0 and 1")
    if config.strategy.max_spread <= 0 or config.strategy.max_spread >= 1:
        errors.append("strategy.max_spread must be between 0 and 1")
    if config.strategy.min_top_of_book_size < 0:
        errors.append("strategy.min_top_of_book_size must be non-negative")
    if config.execution.mode not in ("paper", "live"):
        errors.append("execution.mode must be 'paper' or 'live'")
    if config.execution.mode == "live" and not config.wallet.private_key:
        errors.append("wallet.private_key is required for live mode")
    if config.market.market_slug and config.market.condition_id:
        warnings.append("both market.market_slug and market.condition_id are set; market_slug will win")
    if not config.market.market_slug and not config.market.condition_id and not config.market.slug_prefix:
        errors.append("either market_slug, condition_id, or slug_prefix must be configured")
    if not config.logging.window_close_path:
        errors.append("logging.window_close_path is required")
    if not config.logging.activity_path:
        errors.append("logging.activity_path is required")
    if not config.logging.market_state_path:
        errors.append("logging.market_state_path is required")
    if config.logging.health_log_interval_seconds <= 0:
        errors.append("logging.health_log_interval_seconds must be positive")
    if config.logging.stale_data_threshold_seconds <= 0:
        errors.append("logging.stale_data_threshold_seconds must be positive")
    if config.logging.shutdown_grace_seconds <= 0:
        errors.append("logging.shutdown_grace_seconds must be positive")
    if config.logging.supervisor_restart_backoff_seconds <= 0:
        errors.append("logging.supervisor_restart_backoff_seconds must be positive")
    if not config.price_feed.symbol:
        errors.append("price_feed.symbol is required")

    return {"errors": errors, "warnings": warnings}


def render_validation(result):
    lines = []
    if result["errors"]:
        lines.append("VALIDATION FAILED")
        for item in result["errors"]:
            lines.append("ERROR: %s" % item)
    else:
        lines.append("VALIDATION OK")
    for item in result["warnings"]:
        lines.append("WARN: %s" % item)
    return "\n".join(lines)
