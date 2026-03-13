from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import MarketConfig
from .models import MarketDefinition


GAMMA_BASE_URL = "https://gamma-api.polymarket.com"


def _get_json(path, params):
    query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
    request = Request(
        f"{GAMMA_BASE_URL}{path}?{query}",
        headers={
            "User-Agent": "polymarket-bot/1.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_datetime(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _window_start_from_slug(slug):
    match = re.search(r"(\d{10})$", slug or "")
    if not match:
        return None
    return datetime.fromtimestamp(int(match.group(1)), tz=timezone.utc)


def build_market_slug(slug_prefix, start_time):
    start_ts = int(start_time.replace(second=0, microsecond=0).timestamp())
    start_ts -= start_ts % 300
    return "%s-%s" % (slug_prefix, start_ts)


def current_window_start(now=None):
    now = now or datetime.now(timezone.utc)
    start_ts = int(now.timestamp())
    start_ts -= start_ts % 300
    return datetime.fromtimestamp(start_ts, tz=timezone.utc)


def next_window_start(now=None):
    return current_window_start(now) + timedelta(seconds=300)


def resolve_market(config):
    if config.yes_token_id and config.no_token_id:
        return MarketDefinition(
            question=config.market_slug or config.condition_id or "manual market",
            slug=config.market_slug,
            condition_id=config.condition_id,
            yes_token_id=config.yes_token_id,
            no_token_id=config.no_token_id,
            start_time=config.start_time_utc,
            end_time=config.end_time_utc,
        )

    if config.market_slug:
        payload = _get_json("/markets", {"slug": config.market_slug})
    elif config.condition_id:
        payload = _get_json("/markets", {"conditionId": config.condition_id})
    else:
        raise ValueError("config must include market_slug, condition_id, or both token IDs")

    if not payload:
        raise RuntimeError("no market returned by Gamma API")

    market = payload[0]
    token_ids = market.get("clobTokenIds") or []
    outcomes = market.get("outcomes") or []
    if isinstance(token_ids, str):
        token_ids = json.loads(token_ids)
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    outcomes = [item.lower() for item in outcomes]
    if len(token_ids) < 2:
        raise RuntimeError("market is missing token IDs")

    if outcomes and len(outcomes) >= 2 and outcomes[0] in {"yes", "up"}:
        yes_token_id, no_token_id = token_ids[0], token_ids[1]
    else:
        yes_token_id, no_token_id = token_ids[0], token_ids[1]

    return MarketDefinition(
        question=market.get("question", ""),
        slug=market.get("slug", ""),
        condition_id=market.get("conditionId", ""),
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        start_time=config.start_time_utc or _parse_datetime(market.get("startDate")) or _window_start_from_slug(market.get("slug", "")),
        end_time=config.end_time_utc or _parse_datetime(market.get("endDate")),
        neg_risk=bool(market.get("negRisk", False)),
        tick_size=float(market.get("minimum_tick_size", 0.01) or 0.01),
        window_size_seconds=300,
    )


def resolve_market_for_window(config, window_start):
    slug = build_market_slug(config.slug_prefix, window_start)
    derived = MarketConfig(
        market_slug=slug,
        condition_id=config.condition_id,
        slug_prefix=config.slug_prefix,
        yes_token_id=config.yes_token_id,
        no_token_id=config.no_token_id,
        trade_side=config.trade_side,
        start_time_utc=window_start,
        end_time_utc=window_start + timedelta(seconds=300),
        auto_roll_windows=config.auto_roll_windows,
    )
    market = resolve_market(derived)
    if market.start_time is None:
        market.start_time = window_start
    if market.end_time is None:
        market.end_time = window_start + timedelta(seconds=300)
    return market
