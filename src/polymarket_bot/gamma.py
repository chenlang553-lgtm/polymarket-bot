from datetime import datetime
import json
from urllib.parse import urlencode
from urllib.request import urlopen

from .config import MarketConfig
from .models import MarketDefinition


GAMMA_BASE_URL = "https://gamma-api.polymarket.com"


def _get_json(path, params):
    query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
    with urlopen(f"{GAMMA_BASE_URL}{path}?{query}") as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_datetime(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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
    outcomes = [item.lower() for item in market.get("outcomes", [])]
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
        start_time=config.start_time_utc or _parse_datetime(market.get("startDate")),
        end_time=config.end_time_utc or _parse_datetime(market.get("endDate")),
        neg_risk=bool(market.get("negRisk", False)),
        tick_size=float(market.get("minimum_tick_size", 0.01) or 0.01),
    )
