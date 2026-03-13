# Changelog

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
