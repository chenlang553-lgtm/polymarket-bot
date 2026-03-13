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

It will fetch token IDs from the Gamma API and subscribe to the chosen outcome token on the CLOB market channel.

## Run

Inspect market metadata:

```bash
polymarket-bot inspect --config config.json
```

Run in paper mode:

```bash
polymarket-bot run --config config.json
```

## Notes

- The strategy assumes the market is a BTC 5 minute up/down contract where settlement is `BTC(T) > BTC(0)`.
- Spot input comes from RTDS `crypto_prices` with `btcusdt` by default.
- Order book input comes from the public market WebSocket `wss://ws-subscriptions-clob.polymarket.com/ws/market`.
- Live execution uses the official Python CLOB client if installed.
