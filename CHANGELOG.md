# Changelog

## v1.15.0 - 2026-03-14

- Fixed 5-minute window rollover to use the current market end time instead of skipping ahead one extra window

## v1.14.0 - 2026-03-13

- Enriched `collecting` phase logs with up/down prices, `x_t`, and current position

## v1.13.0 - 2026-03-13

- Added explicit market metadata logs on window activation for token-side debugging

## v1.12.0 - 2026-03-13

- Added `--iteration` runtime option to route logs and JSONL data into per-iteration directories
- Added `logs/<iteration>/service.log` and `data/<iteration>/` layout for cleaner experiment management
- Updated `.gitignore` to exclude generated runtime logs and datasets

## v1.11.0 - 2026-03-13

- Added final-seconds EMA smoothing for `fair_yes` to reduce single-tick probability whipsaws
- Added short-lived best bid/ask fallback caching to reduce missing-edge gaps during sparse book updates
- Added tests for fair-value smoothing and order book fallback merging

## v1.10.0 - 2026-03-13

- Fixed Gamma API access by adding explicit request headers
- Fixed Gamma market parsing for string-encoded `clobTokenIds` and `outcomes`
- Switched default BTC spot feed to Binance websocket for reliable live startup
- Updated market websocket parsing to support list-form batch messages

## v1.9.0 - 2026-03-13

- Added supervisor-style restarts for failed background stream tasks
- Added explicit `shutdown` archive events and structured shutdown logging
- Added config for shutdown grace period and restart backoff

## v1.8.0 - 2026-03-13

- Added periodic `HEALTH` logs with update counts, reconnect counts, and stale-data lag metrics
- Added runtime tracking for latest price/book timestamps and last stream error
- Improved operational visibility during long-running sessions and reconnect cycles

## v1.7.0 - 2026-03-13

- Added `validate` CLI command for startup-time configuration checks
- Added runtime startup self-check logging before the bot begins processing streams
- Added validation coverage for window timing, execution mode, and archive path sanity

## v1.6.0 - 2026-03-13

- Added `market_state.jsonl` recording for active-window state snapshots and window lifecycle events
- Added `replay` CLI command to inspect archived state and activity flow offline
- Improved production debugging by separating state replay data from window summaries and execution events

## v1.5.0 - 2026-03-13

- Added named config `profiles` for controlled experiment overrides
- Added CLI `--profile` selection for `inspect`, `run`, and `report`
- Extended reports with `strategyProfile` summaries for version-to-version comparison

## v1.18.0 - 2026-03-14

- Fixed execution pricing so orders no longer fall back to `bestBid` when buying an outcome
- Separated snapshot market price from execution price to avoid fake fills like `0.005`
- Execution now uses `bestAsk -> lastTradePrice -> null`

## v1.17.0 - 2026-03-14

- Changed `yes_price` and `no_price` to use the priority `midpoint -> lastTradePrice -> bestBid -> bestAsk -> null`
- Added `last_trade_price` handling for Polymarket `price_change` and related market websocket messages
- Updated strategy edge calculations to use the same priority-based market price instead of requiring a full valid midpoint

## v1.16.0 - 2026-03-14

- Switched market pricing to real token prices only by subscribing to both `yes_token_id` and `no_token_id`
- Replaced logged and archived bid/ask fields with single `yesPrice` and `noPrice` effective prices
- Updated strategy edge calculations to use validated midpoint prices instead of synthetic complement quotes
- Filtered out placeholder `0.01/0.99` books from status, activity, and replay records

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
