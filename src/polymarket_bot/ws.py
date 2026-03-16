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


async def market_books_stream(asset_ids):
    asset_ids = [str(asset_id) for asset_id in asset_ids]
    while True:
        try:
            async with websockets.connect(
                MARKET_WS_URL,
                ping_interval=None,
                ping_timeout=None,
                close_timeout=5,
            ) as websocket:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "market",
                            "assets_ids": asset_ids,
                            "custom_feature_enabled": True,
                            "initial_dump": True,
                        }
                    )
                )
                heartbeat = asyncio.create_task(_heartbeat(websocket, "PING", 10))
                try:
                    async for raw in websocket:
                        if raw == "PONG":
                            continue
                        payload = json.loads(raw)
                        messages = payload if isinstance(payload, list) else [payload]
                        for message in messages:
                            if not isinstance(message, dict):
                                continue
                            event_type = message.get("event_type")
                            if event_type == "price_change":
                                for item in _parse_price_change_messages(message, asset_ids):
                                    yield item
                                continue
                            if event_type not in {"book", "best_bid_ask"}:
                                continue
                            message_asset_id = str(message.get("asset_id") or message.get("assetId") or "")
                            if message_asset_id and message_asset_id not in asset_ids:
                                continue
                            if not message_asset_id:
                                if len(asset_ids) != 1:
                                    continue
                                message_asset_id = asset_ids[0]
                            yield _parse_book_like_message(message, message_asset_id)
                finally:
                    heartbeat.cancel()
        except Exception as exc:
            LOGGER.warning("Market WebSocket dropped for %s: %s", ",".join(asset_ids), exc)
            await asyncio.sleep(2)


async def market_book_stream(asset_id):
    async for book in market_books_stream([asset_id]):
        yield book


async def _heartbeat(websocket, payload, interval_seconds=5):
    while True:
        await asyncio.sleep(interval_seconds)
        await websocket.send(payload)


def _parse_book_like_message(message, asset_id):
    event_type = message.get("event_type")
    if event_type == "best_bid_ask":
        best_bid = _parse_optional_float(message.get("best_bid"))
        best_ask = _parse_optional_float(message.get("best_ask"))
        return BestBidAsk(
            asset_id=asset_id,
            bid=best_bid,
            ask=best_ask,
            bid_size=0.0,
            ask_size=0.0,
            timestamp_ms=int(message.get("timestamp", 0) or 0),
            last_trade_price=None,
            bids=[],
            asks=[],
            tick_size=_parse_optional_float(message.get("tick_size")),
        )

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


def _parse_price_change_messages(message, asset_ids):
    timestamp_ms = int(message.get("timestamp", 0) or 0)
    for change in message.get("price_changes", []) or []:
        asset_id = str(change.get("asset_id") or change.get("assetId") or "")
        if not asset_id or asset_id not in asset_ids:
            continue
        yield BestBidAsk(
            asset_id=asset_id,
            bid=_parse_optional_float(change.get("best_bid")),
            ask=_parse_optional_float(change.get("best_ask")),
            bid_size=0.0,
            ask_size=0.0,
            timestamp_ms=timestamp_ms,
            last_trade_price=_parse_optional_float(change.get("price")),
            bids=[],
            asks=[],
            tick_size=None,
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
