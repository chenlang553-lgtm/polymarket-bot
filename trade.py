#!/usr/bin/env python3
"""Polymarket market-order helper with FAK semantics.

默认运行在 `--simulate` 模式，可直接验证下单流程（不触发真实下单）。
如需真实请求，可使用 `--live` 并直接提供 API Key / Secret / Passphrase
完成 L2 鉴权后，通过官方 `create_and_post_market_order` 接口提交 FAK 市价单。
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

CLOB_HOST = "https://clob.polymarket.com"


@dataclass
class OrderRequest:
    token_id: str
    side: str  # buy | sell
    size: float
    price: float = 0.85
    tick_size: str = "0.01"
    neg_risk: bool = False
    order_type: str = "FAK"

    def validate(self) -> None:
        if not self.token_id:
            raise ValueError("token_id is required")
        if self.side not in {"buy", "sell"}:
            raise ValueError("side must be one of: buy, sell")
        if self.size <= 0:
            raise ValueError("size must be > 0")
        if not (0 < self.price <= 1):
            raise ValueError("price must be in (0, 1]")
        if self.order_type.upper() != "FAK":
            raise ValueError("only FAK order_type is supported in this helper")


@dataclass
class ExecutionReport:
    success: bool
    status: str
    mode: str
    requested_size: float
    filled_size: float
    avg_price: Optional[float]
    error: str
    raw: Optional[Dict[str, Any]]

    def to_json_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status,
            "mode": self.mode,
            "requested_size": self.requested_size,
            "filled_size": self.filled_size,
            "avg_price": self.avg_price,
            "error": self.error,
            "raw": self.raw,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def _build_report(raw: Optional[Dict[str, Any]], requested_size: float, mode: str, error: str = "") -> ExecutionReport:
    status = "submitted"
    filled_size = 0.0
    avg_price = None

    if isinstance(raw, dict):
        for key in ("status", "state"):
            value = raw.get(key)
            if value:
                status = str(value).lower()
                break

        for key in ("filled_size", "size_matched", "filled", "executed_size", "matched_size"):
            value = raw.get(key)
            if value is None:
                continue
            try:
                filled_size = float(value)
                break
            except (TypeError, ValueError):
                continue

        for key in ("avg_price", "average_price", "price"):
            value = raw.get(key)
            if value is None:
                continue
            try:
                avg_price = float(value)
                break
            except (TypeError, ValueError):
                continue

    if error:
        normalized = "rejected"
    elif filled_size >= requested_size - 1e-9:
        normalized = "filled"
    elif filled_size > 0:
        normalized = "partial"
    elif status in {"accepted", "open", "pending", "submitted", "live"}:
        normalized = "submitted"
    else:
        normalized = "canceled"

    return ExecutionReport(
        success=normalized in {"filled", "partial", "submitted"} and not error,
        status=normalized,
        mode=mode,
        requested_size=float(requested_size),
        filled_size=float(filled_size),
        avg_price=avg_price,
        error=error,
        raw=raw,
    )


class SimulatedPolymarketTrader:
    """Deterministic local simulation for verification without API keys."""

    def submit_market_fak_order(self, req: OrderRequest) -> ExecutionReport:
        req.validate()

        top_liquidity = 50.0
        filled = min(req.size, top_liquidity)
        raw = {
            "status": "filled" if filled >= req.size else "partial",
            "filled_size": filled,
            "avg_price": req.price,
            "order_type": "FAK",
            "simulated": True,
            "options": {"tick_size": req.tick_size, "neg_risk": req.neg_risk},
        }
        return _build_report(raw=raw, requested_size=req.size, mode="simulate")


class LivePolymarketTrader:
    """Official py-clob-client based trader using quickstart order API.

    Auth flow:
    1) 初始化 ClobClient
    2) set_api_creds(api_key/api_secret/api_passphrase)
    3) create_and_post_market_order(..., order_type=OrderType.FAK)
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        chain_id: int,
        signature_type: int,
        funder: str = "",
        private_key: str = "",
    ) -> None:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import (
                ApiCreds,
                MarketOrderArgs,
                OrderType,
                PartialCreateOrderOptions,
            )
            from py_clob_client.order_builder.constants import BUY, SELL
        except ImportError as exc:
            raise RuntimeError("live mode requires: pip install -e .[trading]") from exc

        self._api_creds_cls = ApiCreds
        self._market_order_args_cls = MarketOrderArgs
        self._order_type_cls = OrderType
        self._partial_create_order_options_cls = PartialCreateOrderOptions
        self._buy = BUY
        self._sell = SELL
        self._can_derive = bool(private_key)

        client_kwargs: Dict[str, Any] = {
            "chain_id": chain_id,
            "signature_type": signature_type,
            "funder": funder or None,
        }
        if private_key:
            client_kwargs["key"] = private_key

        self._client = ClobClient(CLOB_HOST, **client_kwargs)

        if api_key and api_secret and api_passphrase:
            self._set_api_creds(api_key, api_secret, api_passphrase)
        elif self._can_derive:
            self._derive_and_set_api_creds()

    def _set_api_creds(self, api_key: str, api_secret: str, api_passphrase: str) -> None:
        try:
            creds = self._api_creds_cls(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            )
        except TypeError:
            creds = self._api_creds_cls(api_key, api_secret, api_passphrase)
        self._client.set_api_creds(creds)

    def _derive_and_set_api_creds(self) -> None:
        creds = self._client.create_or_derive_api_creds()
        self._client.set_api_creds(creds)

    @staticmethod
    def _is_invalid_api_key_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "invalid api key" in message or "unauthorized" in message

    def _submit_order(self, req: OrderRequest) -> Dict[str, Any]:
        side = self._buy if req.side == "buy" else self._sell
        order_type = getattr(self._order_type_cls, req.order_type.upper())
        order_args = self._market_order_args_cls(
            token_id=req.token_id,
            amount=float(req.size),
            side=side,
            price=float(req.price),
            order_type=order_type,
        )
        options = self._partial_create_order_options_cls(
            tick_size=str(req.tick_size),
            neg_risk=bool(req.neg_risk),
        )
        signed = self._client.create_market_order(order_args, options=options)
        return self._client.post_order(signed, order_type)

    def submit_market_fak_order(self, req: OrderRequest) -> ExecutionReport:
        req.validate()
        try:
            raw = self._submit_order(req)
            return _build_report(raw=raw, requested_size=req.size, mode="live")
        except Exception as exc:
            if self._can_derive and self._is_invalid_api_key_error(exc):
                try:
                    self._derive_and_set_api_creds()
                    raw = self._submit_order(req)
                    return _build_report(raw=raw, requested_size=req.size, mode="live")
                except Exception as retry_exc:
                    return _build_report(raw=None, requested_size=req.size, mode="live", error=str(retry_exc))
            return _build_report(raw=None, requested_size=req.size, mode="live", error=str(exc))


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected a boolean value")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket market-order (FAK) helper")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--simulate", action="store_true", help="Run deterministic local simulation (default)")
    mode_group.add_argument("--live", action="store_true", help="Send live order via py-clob-client")

    parser.add_argument("--token-id", default="demo_yes_token")
    parser.add_argument("--side", choices=["buy", "sell"], default="buy")
    parser.add_argument("--size", type=float, default=30.0)
    parser.add_argument("--price", type=float, default=float(os.getenv("POLYMARKET_PRICE", "0.85")))
    parser.add_argument("--tick-size", default=os.getenv("POLYMARKET_TICK_SIZE", "0.01"))
    parser.add_argument(
        "--neg-risk",
        type=_parse_bool,
        default=_parse_bool(os.getenv("POLYMARKET_NEG_RISK", "false")),
        help="true/false, used in options.neg_risk",
    )
    parser.add_argument("--order-type", default="FAK", help="Only FAK is supported")

    parser.add_argument("--api-key", default=os.getenv("POLYMARKET_API_KEY", ""))
    parser.add_argument("--api-secret", default=os.getenv("POLYMARKET_API_SECRET", ""))
    parser.add_argument("--api-passphrase", default=os.getenv("POLYMARKET_API_PASSPHRASE", ""))
    parser.add_argument(
        "--private-key",
        default=os.getenv("POLYMARKET_PRIVATE_KEY", ""),
        help="Optional signer key for environments where py-clob-client requires it",
    )
    parser.add_argument("--chain-id", type=int, default=int(os.getenv("POLYMARKET_CHAIN_ID", "137")))
    parser.add_argument("--signature-type", type=int, default=int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1")))
    parser.add_argument("--funder", default=os.getenv("POLYMARKET_FUNDER", ""))
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def _print_human(req: OrderRequest, report: ExecutionReport) -> None:
    print("=== Polymarket Market Order (FAK) ===")
    print(f"mode          : {report.mode}")
    print(f"token_id      : {req.token_id}")
    print(f"side          : {req.side}")
    print(f"order_type    : {req.order_type}")
    print(f"requested     : {report.requested_size:.4f}")
    print(f"limit_price   : {req.price}")
    print(f"filled        : {report.filled_size:.4f}")
    print(f"avg_price     : {report.avg_price}")
    print(f"status        : {report.status}")
    print(f"error         : {report.error}")
    print("raw           :")
    print(json.dumps(report.raw, ensure_ascii=False, indent=2))


def main() -> int:
    args = parse_args()

    req = OrderRequest(
        token_id=args.token_id,
        side=args.side,
        size=args.size,
        price=args.price,
        tick_size=args.tick_size,
        neg_risk=args.neg_risk,
        order_type=args.order_type,
    )

    use_live = bool(args.live)
    if not args.live and not args.simulate:
        use_live = False  # default

    if use_live:
        missing = []
        has_level2 = bool(args.api_key and args.api_secret and args.api_passphrase)
        has_level1 = bool(args.private_key)
        if not has_level2 and not has_level1:
            missing.append("api-key")
            missing.append("api-secret")
            missing.append("api-passphrase")
            missing.append("private-key")
        if missing:
            payload = {
                "success": False,
                "status": "rejected",
                "error": "live mode requires valid L2 creds or a signer key; missing " + ", ".join(f"--{name}" for name in missing),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1
        trader: Any = LivePolymarketTrader(
            api_key=args.api_key,
            api_secret=args.api_secret,
            api_passphrase=args.api_passphrase,
            chain_id=args.chain_id,
            signature_type=args.signature_type,
            funder=args.funder,
            private_key=args.private_key,
        )
    else:
        trader = SimulatedPolymarketTrader()

    try:
        report = trader.submit_market_fak_order(req)
    except Exception as exc:
        report = ExecutionReport(
            success=False,
            status="rejected",
            mode="live" if use_live else "simulate",
            requested_size=float(req.size),
            filled_size=0.0,
            avg_price=None,
            error=str(exc),
            raw=None,
        )

    payload = report.to_json_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_human(req, report)

    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
