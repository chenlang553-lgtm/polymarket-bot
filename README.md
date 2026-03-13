# Polymarket Bot

Realtime trading bot for Polymarket BTC 5 minute binary markets.

The project implements the strategy described in `/root/polymarket.md` as a production-style Python service:

- Polymarket RTDS WebSocket for BTC spot price updates
- Polymarket CLOB market WebSocket for order book updates
- Gamma API bootstrap for market metadata and token IDs
- Strategy engine for fair probability, edge, sizing, and exit logic
- Paper execution by default, optional live execution through `py-clob-client`

## Install

```bash
cd /root/polymarket_bot
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Optional live trading support:

```bash
pip install -e .[trading]
```

## Configure

Copy [config.example.json](/root/polymarket_bot/config.example.json) to `config.json` and fill in the market selection and wallet fields you need.

The bot can bootstrap a market either from:

- `market_slug`
- `condition_id`
- `slug_prefix` for automatic current 5 minute window discovery

It will fetch token IDs from the Gamma API and subscribe to the chosen outcome token on the CLOB market channel.
If `market_slug` and `condition_id` are empty, the bot derives the current market slug as `<slug_prefix>-<window_start_epoch>` and rolls to the next 5 minute window automatically.

## Run

Inspect market metadata:

```bash
polymarket-bot inspect --config config.json
```

Run in paper mode:

```bash
polymarket-bot run --config config.json
```

Generate a rollup report from archived `window_close` records:

```bash
polymarket-bot report --config config.json
```

Replay archived market-state snapshots:

```bash
polymarket-bot replay --config config.json --limit 100
```

Validate configuration before a live run:

```bash
polymarket-bot validate --config config.json --profile main
```

Run a named experiment profile from the same config:

```bash
polymarket-bot run --config config.json --profile tight_edge
```

## Runtime behavior

- The bot listens to BTC spot continuously to build window state.
- It prints `WINDOW ... phase=collecting` before the active trading phase.
- It prints `STATUS ...` once per second only during the final `active_only_last_seconds` of the current market.
- It prints `STRATEGY ...` only when an open, close, or flip action is triggered.
- It appends one `window_close` JSON record per completed 5 minute market to `window_close.jsonl` by default.
- It appends per-action execution events to `activity.jsonl` by default for later debugging and replay.
- It appends per-second active-window state snapshots and window lifecycle markers to `market_state.jsonl`.
- `report` summarizes archived windows by overall performance, strategy type, and UTC day.
- You can define `profiles` in the config to override strategy, execution, logging, or market fields for controlled experiments.
- `validate` checks configuration consistency before the bot starts.
- It emits periodic `HEALTH` logs with stream update counts, reconnect counts, and stale-data lag metrics.
- It handles task failures with supervisor-style restarts and records explicit `SHUTDOWN` events on exit.

## Notes

- The strategy assumes the market is a BTC 5 minute up/down contract where settlement is `BTC(T) > BTC(0)`.
- Spot input comes from RTDS `crypto_prices` with `btcusdt` by default.
- Order book input comes from the public market WebSocket `wss://ws-subscriptions-clob.polymarket.com/ws/market`.
- Live execution uses the official Python CLOB client if installed.
