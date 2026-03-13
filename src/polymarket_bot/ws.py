import asyncio
import json
import logging

import websockets

from .models import BestBidAsk, PriceTick


LOGGER = logging.getLogger(__name__)
RTDS_URL = "wss://ws-live-data.polymarket.com"
MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"


async def price_stream(symbol, topic="crypto_prices", provider="binance"):
    if provider == "binance":
        async for tick in binance_price_stream(symbol):
            yield tick
        return
    async for tick in rtds_price_stream(symbol, topic):
        yield tick


async def binance_price_stream(symbol):
    stream_name = "%s@aggTrade" % symbol.lower()
    while True:
        try:
            async with websockets.connect("%s/%s" % (BINANCE_WS_URL, stream_name), ping_interval=None) as websocket:
                async for raw in websocket:
                    payload = json.loads(raw)
                    if payload.get("e") != "aggTrade":
                        continue
                    yield PriceTick(
                        symbol=str(payload.get("s", symbol)).lower(),
                        price=float(payload["p"]),
                        timestamp_ms=int(payload.get("T", payload.get("E", 0))),
                    )
        except Exception as exc:
            LOGGER.warning("Binance price connection dropped for %s: %s", symbol, exc)
            await asyncio.sleep(2)


async def rtds_price_stream(symbol, topic="crypto_prices"):
    while True:
        try:
            async with websockets.connect(RTDS_URL, ping_interval=None) as websocket:
                await websocket.send(
                    json.dumps(
                        {
                            "action": "subscribe",
                            "subscriptions": [
                                {
                                    "topic": topic,
                                    "type": "update" if topic == "crypto_prices" else "*",
                                    "filters": "[\"%s\"]" % symbol.lower(),
                                }
                            ],
                        }
                    )
                )

                heartbeat = asyncio.create_task(_heartbeat(websocket, "PING"))
                try:
                    async for raw in websocket:
                        payload = json.loads(raw)
                        if not payload:
                            continue
                        if payload.get("topic") != topic:
                            continue
                        inner = payload.get("payload", {})
                        if str(inner.get("symbol", "")).lower() != symbol.lower():
                            continue
                        yield PriceTick(
                            symbol=str(inner["symbol"]).lower(),
                            price=float(inner["value"]),
                            timestamp_ms=int(inner.get("timestamp", payload.get("timestamp", 0))),
                        )
                finally:
                    heartbeat.cancel()
        except Exception as exc:
            LOGGER.warning("RTDS connection dropped: %s", exc)
            await asyncio.sleep(2)


async def market_book_stream(asset_id):
    while True:
        try:
            async with websockets.connect(MARKET_WS_URL) as websocket:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "market",
                            "assets_ids": [asset_id],
                            "custom_feature_enabled": True,
                            "initial_dump": True,
                        }
                    )
                )
                async for raw in websocket:
                    payload = json.loads(raw)
                    messages = payload if isinstance(payload, list) else [payload]
                    for message in messages:
                        if not isinstance(message, dict):
                            continue
                        event_type = message.get("event_type")
                        if event_type not in {"book", "price_change", "best_bid_ask"}:
                            continue
                        yield _parse_book_like_message(message, asset_id)
        except Exception as exc:
            LOGGER.warning("Market WebSocket dropped for %s: %s", asset_id, exc)
            await asyncio.sleep(2)


async def _heartbeat(websocket, payload):
    while True:
        await asyncio.sleep(5)
        await websocket.send(payload)


def _parse_book_like_message(message, asset_id):
    if message.get("event_type") == "best_bid_ask":
        return BestBidAsk(
            asset_id=asset_id,
            bid=_parse_optional_float(message.get("best_bid")),
            ask=_parse_optional_float(message.get("best_ask")),
            bid_size=float(message.get("best_bid_size", 0.0) or 0.0),
            ask_size=float(message.get("best_ask_size", 0.0) or 0.0),
            timestamp_ms=int(message.get("timestamp", 0) or 0),
            last_trade_price=_parse_last_trade_price(message),
        )

    bids = message.get("bids", [])
    asks = message.get("asks", [])
    best_bid = bids[0] if bids else {}
    best_ask = asks[0] if asks else {}
    return BestBidAsk(
        asset_id=asset_id,
        bid=_parse_optional_float(best_bid.get("price")),
        ask=_parse_optional_float(best_ask.get("price")),
        bid_size=float(best_bid.get("size", 0.0) or 0.0),
        ask_size=float(best_ask.get("size", 0.0) or 0.0),
        timestamp_ms=int(message.get("timestamp", 0) or 0),
        last_trade_price=_parse_last_trade_price(message),
    )


def _parse_optional_float(value):
    if value in (None, ""):
        return None
    return float(value)


def _parse_last_trade_price(message):
    for key in ("last_trade_price", "lastTradePrice", "price"):
        value = message.get(key)
        if value not in (None, ""):
            return float(value)
    return None
