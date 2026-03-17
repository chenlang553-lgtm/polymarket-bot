# Changelog

## v1.25.20 - 2026-03-17

- Added a live startup guard that skips new opens/flips for the current window after a restart
- Trading resumes automatically on the next market window after rollover
- Added regression coverage so startup-window blocking does not affect later windows

## v1.25.19 - 2026-03-17

- Added live market-order price-buffer laddering so failed FAK opens retry with progressively more aggressive buffers
- Added `execution.market_order_price_buffer_step` and `execution.market_order_price_buffer_max`
- Reset per-side live open failure state on successful fills and on market rollover
- Added validation and regression coverage for the live buffer ladder

## v1.25.13 - 2026-03-17

- Updated `scripts/run_monitor.sh` to stop all older `monitor_iteration.py` processes and clear stale monitor pid files before starting a new monitor

## v1.25.12 - 2026-03-17

- Reworked the WeCom monitor to report full-day summaries for yesterday and today instead of incremental 5-minute deltas
- Added day-level metrics for PnL, fill count, traded-window count, largest winning window, and daily max drawdown
- Added best-effort live account reporting for current USDC balance/allowance and the active window's conditional token balances

## v1.25.11 - 2026-03-17

- Raised the default live `execution.market_order_price_buffer` from `0.001` to `0.05` to make FAK market orders substantially more aggressive
- Updated both example and local runtime configs to use the higher live order-price buffer

## v1.25.10 - 2026-03-17

- Added a live-only `execution.market_order_price_buffer` setting for more aggressive FAK worst-price limits
- Live entry pricing now applies the configured buffer and rounds up to tick size before submission
- Added validation and regression tests for the live market-order price buffer

## v1.25.1 - 2026-03-16

- Added environment-variable fallback for live wallet settings so the main bot can use the same `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER`, `POLYMARKET_SIGNATURE_TYPE`, and `POLYMARKET_CHAIN_ID` flow as `trade.py`
- Added `scripts/run_live.sh` to generate a live runtime config, stop the previous live process, and start a new live bot run from a single version argument

## v1.25.3 - 2026-03-16

- Added `scripts/wecom_send.py`, a Python WeCom text sender with cached access tokens
- Added `scripts/monitor_iteration.py` to summarize the latest 5-minute order/window activity and push it to WeCom on a fixed interval
- Added `scripts/run_monitor.sh` to stop an older monitor process and start a fresh background monitor for a given iteration

## v1.25.4 - 2026-03-16

- Moved WeCom credentials, recipients, and monitor interval into a dedicated `monitor_config.json`
- Added `monitor_config.example.json` and updated the monitor scripts to read from the standalone monitor config by default

## v1.25.0 - 2026-03-16

- Integrated the bot's live executor with the same authenticated market-order flow proven in `trade.py`, including automatic API-credential derivation fallback on invalid L2 creds
- Switched default runtime sizing to fixed `1u` notional per order and converted execution to compute actual share quantity from the current execution price
- Changed default live order type to `FAK`, updated strategy default size bands to a uniform logical size of `1.0`, and added validation for `execution.fixed_order_notional`

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

## v1.21.0 - 2026-03-14

- Fixed `LiveExecutor.open_position()` so submission failures now always populate `last_report`
- Removed duplicate live open submission path that could raise before error reporting was recorded

## v1.20.0 - 2026-03-14

- Rebuilt Polymarket outcome pricing to match the provided JS market-data logic
- Market websocket now consumes only `book` events and computes `bestBid`/`bestAsk` from the full level set
- Snapshot `yes_price` / `no_price` now come directly from the current book state via `midpoint -> lastTradePrice -> bestBid -> bestAsk -> null`
- Execution pricing now uses `max(signalPrice, bestAsk)` when `bestAsk` exists

## v1.19.0 - 2026-03-14

- Added per-outcome cached market prices so `yes_price` and `no_price` no longer flap to `None` on brief websocket gaps
- Strategy snapshots now prefer the current market price and fall back to the most recent valid price within the configured book fallback age

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

## v1.24.0 - 2026-03-16

- Switched position sizing from edge buckets to explicit price bands: buy `500` shares below `0.10`, `100` shares from `0.10` to `0.50`, and `60` shares above `0.50`
- Added configurable `price_size_rules` to both the example config and active `config.json`
- Kept the rest of the v1.23.0 entry filters intact so the sizing change is isolated and testable

## v1.23.0 - 2026-03-15

- Added a volatility floor to the fair-probability engine so tiny BTC moves do not saturate the model into near-certain `Up` or `Down`
- Added a no-trade zone around the opening price and tightened the main profile to trade later, with higher edge requirements and tighter spreads
- Reduced single-window aggressiveness by limiting the main profile to one entry and zero flips per window
- Updated the active `config.json` baseline so the running `main` profile actually uses the tighter parameters

## v1.22.2 - 2026-03-14

- Updated `scripts/run_iteration.sh` to stop any existing bot process using the same `config + profile` before starting a new iteration
- Documented the restart behavior in the README so iteration switches do not leave multiple bot processes running

## v1.22.1 - 2026-03-14

- Added `scripts/run_iteration.sh` to start a detached iteration run with versioned `logs/<iteration>` and `data/<iteration>` paths
- Documented the helper script in the README so new experiment runs do not require hand-written `nohup` commands

## v1.22.0 - 2026-03-14

- Tightened the default trading profile with a narrower decision window, higher edge threshold, tighter spread limit, and more conservative size buckets
- Capped fair probabilities away from `0.001/0.999` and switched signal prices back to book-derived market prices instead of executable asks
- Added single-window risk controls: require both outcome prices, block same-side reentry, cap entries per window, and cap flips per window
- Relaxed edge-decay exits so the bot no longer closes and immediately reopens while the held side still has positive edge

# Changelog

## v1.25.7 - 2026-03-16

- Made live FAK execution behave closer to paper by clearing stuck `inflight` state on rejected, partial, and timed-out orders
- Added a stale `inflight` timeout so live trading does not block an entire window after a failed open
- Improved book caching so partial market book updates merge with the previous usable snapshot instead of wiping the opposite side
- Added regression tests for partial book merging and live inflight recovery

## v1.25.8 - 2026-03-16

- Switched live market book ingestion from two separate outcome WebSockets to one combined dual-outcome stream
- Increased market WebSocket keepalive tolerance to reduce ping-timeout disconnects
- Fixed `run_live.sh` so live archives always write to `data/<iteration>/` instead of the project root

## v1.25.9 - 2026-03-16

- Aligned the market WebSocket heartbeat with the official docs by sending text `PING` every 10 seconds
- Added support for `best_bid_ask` and `price_change` market-channel events to preserve prices when full `book` updates are sparse
- Added regression tests for `best_bid_ask` and `price_change` parsing

## v1.25.18 - 2026-03-17

- Added a local dashboard page for browsing iteration summaries, recent fills, recent window closes, and latest state by version
- Added `scripts/run_dashboard.sh` to restart the dashboard cleanly and serve it on a configurable port

## v1.25.17 - 2026-03-17

- Lowered the live `execution.market_order_price_buffer` from `0.05` to `0.01` to reduce worst-price slippage while keeping FAK retry behavior

## v1.25.16 - 2026-03-17

- Switched monitor holdings from current-window token balances to official full-account positions via `data-api.polymarket.com/positions`
- Added official total position value via `data-api.polymarket.com/value`
- Monitor now reports `USDC balance + holdings value = total equity` and includes a compact top-holdings summary

## v1.25.15 - 2026-03-17

- Updated monitor summaries to report `USDC balance + position market value = total equity`
- Kept `Up/Down` conditional token quantities as separate position-quantity fields

## v1.25.14 - 2026-03-17

- Fixed monitor balance lookup by retrying with freshly derived Polymarket API creds when stored L2 creds return `401 Invalid api key`
- Corrected monitor balance parsing for collateral and conditional token balances
- Improved monitor output to show `allowance=set/none` instead of a bogus numeric value
- Added monitor balance error logging so failures no longer silently degrade to `-`

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
