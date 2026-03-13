# Changelog

## v1.4.0 - 2026-03-13

- Added `activity.jsonl` execution ledger with per-action and per-fill events
- Extended window summaries with execution event counts and end-of-window open position state
- Improved `window_close` records by separating `inferredWinner` from `actualWinner`

## v1.3.0 - 2026-03-13

- Added `report` CLI command for archived `window_close` analysis
- Added offline summary metrics: total windows, traded windows, win rate, realized PnL, and max drawdown
- Added grouping by `strategyType` and UTC day for quick iteration comparisons

## v1.2.0 - 2026-03-13

- Automatic `window_close` JSONL archival at the end of each 5 minute market
- Added per-window execution stats, fill counts, notional tracking, and realized PnL summary
- Config support for `strategy_version`, `strategy_profile`, `strategy_type`, and `window_close_path`
- Default archive output path is `window_close.jsonl`

## v1.1.0 - 2026-03-13

- Automatic 5 minute market window discovery from `slug_prefix`
- Automatic market rollover when the current 5 minute window expires
- Continuous spot collection with active trading and status output only in the final configured seconds
- New `WINDOW ... phase=collecting` and `WINDOW ... phase=activated` lifecycle logs
- Config support for `slug_prefix`, `auto_roll_windows`, and `active_only_last_seconds`
- Unit test coverage for slug generation on 5 minute boundaries

## v1.0.0 - 2026-03-13

- Initial project scaffold for a Polymarket BTC 5 minute up/down bot
- Realtime BTC spot ingestion via Polymarket RTDS WebSocket
- Realtime Polymarket CLOB best bid/ask ingestion via market WebSocket
- Fair probability engine based on log price deviation, layered volatility, and momentum drift
- Paper trading execution flow with open, close, and flip signals
- Realtime `STATUS` logs during the final 60 seconds of a market window
- `STRATEGY` logs only when a trading action is triggered
- CLI commands for `inspect` and `run`
- Unit tests for the strategy core
