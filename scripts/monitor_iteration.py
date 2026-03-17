#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path("/root/polymarket_bot/src")))

from polymarket_bot.config import MarketConfig
from polymarket_bot.gamma import resolve_market

from wecom_send import DEFAULT_CONFIG_PATH, get_access_token, load_config, send_text


PROJECT_ROOT = Path("/root/polymarket_bot")
TZ = datetime.now().astimezone().tzinfo or timezone.utc
USDC_HOST = "https://clob.polymarket.com"
LOGGER = logging.getLogger(__name__)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _fmt_money(value: float) -> str:
    return f"{value:+.4f}"


def _fmt_balance(value: float | str | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    return f"{value:.4f}"


def _fmt_time(ms: int | None) -> str:
    if not ms:
        return "-"
    return datetime.fromtimestamp(ms / 1000.0, tz=TZ).strftime("%m-%d %H:%M:%S")


def _send_wecom(content: str, config: dict) -> None:
    wecom = config.get("wecom", {})
    corp_id = str(wecom.get("corp_id", "")).strip()
    corp_secret = str(wecom.get("corp_secret", "")).strip()
    agent_id = str(wecom.get("agent_id", "")).strip()
    to_user = str(wecom.get("to_user", "")).strip()
    to_party = str(wecom.get("to_party", "")).strip()
    to_tag = str(wecom.get("to_tag", "")).strip()
    if not corp_id or not corp_secret or not agent_id:
        raise RuntimeError("monitor_config.json is missing wecom credentials")
    if not to_user and not to_party and not to_tag:
        raise RuntimeError("monitor_config.json has no WeCom recipients configured")
    token = get_access_token(corp_id, corp_secret)
    send_text(
        token=token,
        agent_id=agent_id,
        content=content,
        to_user=to_user,
        to_party=to_party,
        to_tag=to_tag,
    )


def _latest_state_record(path: Path) -> dict | None:
    records = _read_jsonl(path)
    for record in reversed(records):
        if record.get("recordType") == "state":
            return record
    return None


def _record_day(ms: int | None) -> date | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=TZ).date()


def _extract_number(payload, keys: tuple[str, ...]) -> float | None:
    if payload is None:
        return None
    if isinstance(payload, (int, float)):
        return float(payload)
    if isinstance(payload, str):
        try:
            return float(payload)
        except ValueError:
            return None
    if isinstance(payload, list):
        for item in payload:
            value = _extract_number(item, keys)
            if value is not None:
                return value
        return None
    if isinstance(payload, dict):
        for key in keys:
            if key in payload:
                value = _extract_number(payload.get(key), keys)
                if value is not None:
                    return value
        for value in payload.values():
            found = _extract_number(value, keys)
            if found is not None:
                return found
    return None


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _scale_six_decimals(value: float | None) -> float | None:
    if value is None:
        return None
    return float(value) / 1_000_000.0


def _extract_collateral_balance(payload) -> float | None:
    if not isinstance(payload, dict):
        return None
    return _scale_six_decimals(_to_float(payload.get("balance")))


def _extract_conditional_balance(payload) -> float | None:
    if not isinstance(payload, dict):
        return None
    return _scale_six_decimals(_to_float(payload.get("balance")))


def _extract_allowance_label(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    allowances = payload.get("allowances")
    if not isinstance(allowances, dict):
        return None
    values = [_to_float(value) for value in allowances.values()]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return "set" if max(values) > 0 else "none"


def _build_live_client():
    private_key = str(os.getenv("POLYMARKET_PRIVATE_KEY", "")).strip()
    if not private_key:
        return None

    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds

    funder = str(os.getenv("POLYMARKET_FUNDER", "")).strip() or None
    signature_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "2"))
    chain_id = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
    client = ClobClient(
        USDC_HOST,
        key=private_key,
        chain_id=chain_id,
        signature_type=signature_type,
        funder=funder,
    )

    api_key = str(os.getenv("POLYMARKET_API_KEY", "")).strip()
    api_secret = str(os.getenv("POLYMARKET_API_SECRET", "")).strip()
    api_passphrase = str(os.getenv("POLYMARKET_API_PASSPHRASE", "")).strip()
    if api_key and api_secret and api_passphrase:
        client.set_api_creds(ApiCreds(api_key, api_secret, api_passphrase))
    else:
        client.set_api_creds(client.create_or_derive_api_creds())
    return client


def _is_invalid_api_key_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "invalid api key" in message or "unauthorized" in message


def _call_with_auth_retry(client, call, *, can_derive: bool):
    try:
        return call()
    except Exception as exc:
        if can_derive and _is_invalid_api_key_error(exc):
            LOGGER.warning("monitor balance auth retry due to invalid api key")
            client.set_api_creds(client.create_or_derive_api_creds())
            return call()
        raise


def _fetch_account_snapshot(latest_state: dict | None) -> dict:
    result = {
        "usdc_balance": None,
        "usdc_allowance": None,
        "yes_balance": None,
        "no_balance": None,
        "balance_error": None,
    }
    try:
        client = _build_live_client()
        if client is None:
            result["balance_error"] = "missing_private_key"
            return result

        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        can_derive = bool(str(os.getenv("POLYMARKET_PRIVATE_KEY", "")).strip())

        collateral = _call_with_auth_retry(
            client,
            lambda: client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            ),
            can_derive=can_derive,
        )
        result["usdc_balance"] = _extract_collateral_balance(collateral)
        result["usdc_allowance"] = _extract_allowance_label(collateral)

        market_slug = None if latest_state is None else latest_state.get("marketSlug")
        if not market_slug:
            return result

        market = resolve_market(MarketConfig(market_slug=market_slug))
        yes_payload = _call_with_auth_retry(
            client,
            lambda: client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=market.yes_token_id)
            ),
            can_derive=can_derive,
        )
        no_payload = _call_with_auth_retry(
            client,
            lambda: client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=market.no_token_id)
            ),
            can_derive=can_derive,
        )
        result["yes_balance"] = _extract_conditional_balance(yes_payload)
        result["no_balance"] = _extract_conditional_balance(no_payload)
    except Exception as exc:
        result["balance_error"] = str(exc)
        LOGGER.exception("monitor balance lookup failed: %s", exc)
        return result
    return result


def _day_window_rows(rows: list[dict], target_day: date) -> list[dict]:
    return [row for row in rows if _record_day(int(row.get("closedAtMs", 0) or 0)) == target_day]


def _day_fill_rows(rows: list[dict], target_day: date) -> list[dict]:
    return [
        row
        for row in rows
        if row.get("eventType") == "fill"
        and _record_day(int(row.get("eventAtMs", 0) or 0)) == target_day
    ]


def _max_drawdown(rows: list[dict]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for row in sorted(rows, key=lambda item: int(item.get("closedAtMs", 0) or 0)):
        equity += float(row.get("realizedPnl", 0.0) or 0.0)
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return max_dd


def _day_stats(window_rows: list[dict], activity_rows: list[dict], target_day: date) -> dict:
    day_windows = _day_window_rows(window_rows, target_day)
    traded_windows = [row for row in day_windows if int(((row.get("activity") or {}).get("fillCount", 0))) > 0]
    fills = _day_fill_rows(activity_rows, target_day)
    pnls = [float(row.get("realizedPnl", 0.0) or 0.0) for row in traded_windows]
    return {
        "date": target_day,
        "pnl": sum(float(row.get("realizedPnl", 0.0) or 0.0) for row in day_windows),
        "fills": len(fills),
        "traded_windows": len(traded_windows),
        "max_profit": max(pnls) if pnls else 0.0,
        "max_drawdown": _max_drawdown(day_windows),
    }


def _latest_traded_window(window_rows: list[dict]) -> dict | None:
    traded = [row for row in window_rows if int(((row.get("activity") or {}).get("fillCount", 0))) > 0]
    return traded[-1] if traded else None


def _summarize(iteration: str, data_dir: Path) -> str:
    window_rows = sorted(_read_jsonl(data_dir / "window_close.jsonl"), key=lambda item: item.get("closedAtMs", 0))
    activity_rows = sorted(_read_jsonl(data_dir / "activity.jsonl"), key=lambda item: item.get("eventAtMs", 0))
    latest_state = _latest_state_record(data_dir / "market_state.jsonl")

    today = datetime.now(tz=TZ).date()
    yesterday = today - timedelta(days=1)
    yesterday_stats = _day_stats(window_rows, activity_rows, yesterday)
    today_stats = _day_stats(window_rows, activity_rows, today)

    total_pnl = sum(float(row.get("realizedPnl", 0.0) or 0.0) for row in window_rows)
    side_counter = Counter(row.get("side", "Unknown") for row in activity_rows if row.get("eventType") == "fill")
    latest_traded = _latest_traded_window(window_rows)
    latest_any = window_rows[-1] if window_rows else None
    account = _fetch_account_snapshot(latest_state)

    lines = [
        f"[{iteration}] 账户与订单监督",
        "昨天: pnl={pnl} fills={fills} traded_windows={traded} max_win={max_win} max_dd={max_dd}".format(
            pnl=_fmt_money(yesterday_stats["pnl"]),
            fills=yesterday_stats["fills"],
            traded=yesterday_stats["traded_windows"],
            max_win=_fmt_money(yesterday_stats["max_profit"]),
            max_dd=_fmt_money(yesterday_stats["max_drawdown"]),
        ),
        "今天: pnl={pnl} fills={fills} traded_windows={traded} max_win={max_win} max_dd={max_dd}".format(
            pnl=_fmt_money(today_stats["pnl"]),
            fills=today_stats["fills"],
            traded=today_stats["traded_windows"],
            max_win=_fmt_money(today_stats["max_profit"]),
            max_dd=_fmt_money(today_stats["max_drawdown"]),
        ),
        "累计PnL: {pnl} | 总fills: {fills} | 方向分布: Up={up} Down={down}".format(
            pnl=_fmt_money(total_pnl),
            fills=sum(1 for row in activity_rows if row.get("eventType") == "fill"),
            up=side_counter.get("Up", 0),
            down=side_counter.get("Down", 0),
        ),
        "账户余额: usdc={usdc} allowance={allowance} | 当前资产: Up={up} Down={down}".format(
            usdc=_fmt_balance(account.get("usdc_balance")),
            allowance=_fmt_balance(account.get("usdc_allowance")),
            up=_fmt_balance(account.get("yes_balance")),
            down=_fmt_balance(account.get("no_balance")),
        ),
    ]

    if account.get("balance_error"):
        lines.append(f"余额查询异常: {account['balance_error']}")

    if latest_traded is not None:
        lines.append(
            "最新交易窗: {slug} pnl={pnl} winner={winner} fills={fills}".format(
                slug=latest_traded.get("marketSlug", "-"),
                pnl=_fmt_money(float(latest_traded.get("realizedPnl", 0.0) or 0.0)),
                winner=latest_traded.get("actualWinner", "-"),
                fills=((latest_traded.get("activity") or {}).get("fillCount", 0)),
            )
        )
    elif latest_any is not None:
        lines.append(
            "最新结算窗: {slug} pnl={pnl} winner={winner}".format(
                slug=latest_any.get("marketSlug", "-"),
                pnl=_fmt_money(float(latest_any.get("realizedPnl", 0.0) or 0.0)),
                winner=latest_any.get("actualWinner", "-"),
            )
        )

    if latest_state is not None:
        lines.append(
            "当前状态: window={window} tau={tau}s pos={pos} fairY={fair_y} fairN={fair_n} yes={yes} no={no}".format(
                window=latest_state.get("marketSlug", "-"),
                tau=latest_state.get("timeToExpirySec", "-"),
                pos=latest_state.get("position", "flat"),
                fair_y=f"{float(latest_state.get('fairYes', 0.0) or 0.0):.3f}",
                fair_n=f"{float(latest_state.get('fairNo', 0.0) or 0.0):.3f}",
                yes="None" if latest_state.get("yesPrice") is None else f"{float(latest_state.get('yesPrice')):.3f}",
                no="None" if latest_state.get("noPrice") is None else f"{float(latest_state.get('noPrice')):.3f}",
            )
        )

    lines.append(f"统计时间: {_fmt_time(int(time.time() * 1000))}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send account and trading summaries to WeCom")
    parser.add_argument("iteration")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--interval", type=int, default=0, help="Seconds between pushes, 0 means use config")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config(args.config)
    data_dir = PROJECT_ROOT / "data" / args.iteration
    interval = int(args.interval or config.get("monitor", {}).get("interval_seconds", 300))

    if not data_dir.exists():
        print(f"Data dir not found: {data_dir}", file=sys.stderr)
        return 1

    while True:
        content = _summarize(args.iteration, data_dir)
        _send_wecom(content, config)
        if args.once:
            return 0
        time.sleep(max(60, interval))


if __name__ == "__main__":
    raise SystemExit(main())
