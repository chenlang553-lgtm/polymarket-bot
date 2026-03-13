import asyncio
import json
import logging

import websockets

from .models import BestBidAsk, OrderBookLevel, PriceTick


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
                        if message.get("event_type") != "book":
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
    bids = _parse_levels(message.get("bids", []))
    asks = _parse_levels(message.get("asks", []))
    best_bid_level = _best_bid_level(bids)
    best_ask_level = _best_ask_level(asks)
    return BestBidAsk(
        asset_id=asset_id,
        bid=None if best_bid_level is None else best_bid_level.price,
        ask=None if best_ask_level is None else best_ask_level.price,
        bid_size=0.0 if best_bid_level is None else best_bid_level.size,
        ask_size=0.0 if best_ask_level is None else best_ask_level.size,
        timestamp_ms=int(message.get("timestamp", 0) or 0),
        last_trade_price=_parse_last_trade_price(message),
        bids=bids,
        asks=asks,
        tick_size=_parse_optional_float(message.get("tick_size")),
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


def _parse_levels(raw_levels):
    levels = []
    for item in raw_levels or []:
        if isinstance(item, dict):
            price = _parse_optional_float(item.get("price"))
            size = _parse_optional_float(item.get("size"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            price = _parse_optional_float(item[0])
            size = _parse_optional_float(item[1])
        else:
            continue
        if price is None:
            continue
        levels.append(OrderBookLevel(price, 0.0 if size is None else size))
    return levels


def _best_bid_level(levels):
    if not levels:
        return None
    return max(levels, key=lambda level: level.price)


def _best_ask_level(levels):
    if not levels:
        return None
    return min(levels, key=lambda level: level.price)
