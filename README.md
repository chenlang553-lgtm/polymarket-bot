@@ -30,50 +30,66 @@ pip install -e .[trading]
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

Standalone order-flow verification (official market-order shape + FAK):

```bash
python trade.py --simulate --token-id demo_yes_token --side buy --size 30 --price 0.85 --order-type FAK --json
```

Live request (official quickstart style create_and_post_market_order):

```bash
python trade.py --live --token-id <token_id> --side buy --size 1 --price 0.85 --order-type FAK \
  --tick-size 0.01 --neg-risk false \
  --api-key <api_key> --api-secret <api_secret> --api-passphrase <api_passphrase>
```

`trade.py` defaults to simulation mode for safe local verification. In `--live` mode, it authenticates with `api_key/api_secret/api_passphrase` (L2 creds), then calls `client.create_and_post_market_order(..., options={"tick_size": "0.01", "neg_risk": false}, order_type=OrderType.FAK)`.

Start a detached iteration run with a versioned log/data directory:

```bash
./scripts/run_iteration.sh v1.22.0
```

The helper stops any existing bot process using the same `config + profile` before starting the new iteration.

With a custom profile or config path:

```bash
./scripts/run_iteration.sh v1.22.0 tight_edge /root/polymarket_bot/config.json
```

Generate a rollup report from archived `window_close` records:

```bash
polymarket-bot report --config config.json
```

Replay archived market-state snapshots:

```bash
polymarket-bot replay --config config.json --limit 100
```
tests/test_trade.py
