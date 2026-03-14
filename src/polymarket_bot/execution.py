from datetime import datetime, timezone
import logging

from .models import OutcomeSide, Position


LOGGER = logging.getLogger(__name__)


def _build_report(raw, requested_size, fallback_price, error=""):
    status = "submitted"
    filled_size = None
    avg_price = fallback_price

    if isinstance(raw, dict):
        for key in ("status", "state"):
            if raw.get(key):
                status = str(raw.get(key)).lower()
                break
        for key in ("filled_size", "size_matched", "filled", "executed_size", "matched_size"):
            value = raw.get(key)
            if value is not None:
                try:
                    filled_size = float(value)
                    break
                except (TypeError, ValueError):
                    pass
        for key in ("avg_price", "average_price", "price"):
            value = raw.get(key)
            if value is not None:
                try:
                    avg_price = float(value)
                    break
                except (TypeError, ValueError):
                    pass

    if filled_size is None:
        if status in {"filled", "matched", "executed", "complete", "completed"}:
            filled_size = float(requested_size)
        else:
            filled_size = 0.0

    if error:
        normalized_status = "rejected"
    elif filled_size >= float(requested_size) - 1e-9:
        normalized_status = "filled"
    elif filled_size > 0:
        normalized_status = "partial"
    elif status in {"submitted", "accepted", "open", "pending"}:
        normalized_status = "submitted"
    else:
        normalized_status = "rejected"

    success = normalized_status in {"filled", "partial", "submitted"} and not error
    return {
        "success": success,
        "status": normalized_status,
        "requested_size": float(requested_size),
        "filled_size": float(filled_size),
        "avg_price": avg_price,
        "error": error,
        "raw": raw,
    }


def _extract_balance(payload):
    if payload is None:
        return None
    if isinstance(payload, (int, float)):
        return float(payload)
    if isinstance(payload, dict):
        for key in ("balance", "available", "amount", "size"):
            value = payload.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
    return None


class PaperExecutor:
    def __init__(self):
        self.fills = []
        self.last_report = None

    def open_position(self, market, signal, ask_price):
        LOGGER.info("PAPER OPEN side=%s size=%.4f price=%.4f reason=%s", signal.side, signal.size, ask_price, signal.reason)
        self.fills.append({"side": signal.side, "size": signal.size, "price": ask_price, "status": "filled"})
        self.last_report = _build_report({"status": "filled", "filled_size": signal.size}, signal.size, ask_price)
        return Position(
            side=signal.side,
            size=signal.size,
            entry_price=ask_price,
            edge_at_entry=signal.snapshot.edge_yes if signal.side == OutcomeSide.YES else signal.snapshot.edge_no,
            opened_at=datetime.now(timezone.utc),
        )

    def close_position(self, market, position):
        LOGGER.info("PAPER CLOSE side=%s size=%.4f entry=%.4f", position.side, position.size, position.entry_price)
        self.last_report = _build_report({"status": "filled", "filled_size": position.size}, position.size, position.entry_price)

    def reconcile_position(self, market, local_position):
        return local_position, True, "paper"


class LiveExecutor:
    def __init__(self, execution, wallet):
        self.execution = execution
        self.wallet = wallet
        self.last_report = None
        self.__post_init__()

    def __post_init__(self) -> None:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL
        except ImportError as exc:
            raise RuntimeError("live trading requires pip install -e .[trading]") from exc
        self._client_cls = ClobClient
        self._market_order_args_cls = MarketOrderArgs
        self._order_type_cls = OrderType
        self._buy_constant = BUY
        self._sell_constant = SELL
        self._client = ClobClient(
            "https://clob.polymarket.com",
            key=self.wallet.private_key,
            chain_id=self.wallet.chain_id,
            signature_type=self.wallet.signature_type,
            funder=self.wallet.funder or None,
        )
        self._client.set_api_creds(self._client.create_or_derive_api_creds())

    def _token_balance(self, token_id):
        getter = getattr(self._client, "get_balance", None)
        if callable(getter):
            return _extract_balance(getter(token_id))
        return None

    def reconcile_position(self, market, local_position):
        try:
            yes_balance = self._token_balance(market.yes_token_id)
            no_balance = self._token_balance(market.no_token_id)
        except Exception as exc:
            LOGGER.warning("LIVE RECONCILE error=%s", exc)
            return local_position, False, "reconcile_error"

        if yes_balance is None and no_balance is None:
            return local_position, False, "reconcile_unavailable"

        yes_size = 0.0 if yes_balance is None else max(0.0, float(yes_balance))
        no_size = 0.0 if no_balance is None else max(0.0, float(no_balance))

        if yes_size <= 1e-9 and no_size <= 1e-9:
            return None, True, "flat"

        if yes_size >= no_size:
            size = yes_size
            side = OutcomeSide.YES
        else:
            size = no_size
            side = OutcomeSide.NO

        if local_position is not None and local_position.side == side:
            local_position.size = size
            return local_position, True, "updated"

        position = Position(
            side=side,
            size=size,
            entry_price=0.0 if local_position is None else local_position.entry_price,
            edge_at_entry=0.0 if local_position is None else local_position.edge_at_entry,
            opened_at=datetime.now(timezone.utc),
        )
        return position, True, "reconstructed"

    def open_position(self, market, signal, ask_price):
        token_id = market.yes_token_id if signal.side == OutcomeSide.YES else market.no_token_id
        try:
            order = self._market_order_args_cls(
                token_id=token_id,
                amount=float(signal.size),
                side=self._buy_constant,
                order_type=getattr(self._order_type_cls, self.execution.order_type.upper()),
            )
            signed = self._client.create_market_order(order)
            raw = self._client.post_order(signed, getattr(self._order_type_cls, self.execution.order_type.upper()))
            self.last_report = _build_report(raw, signal.size, ask_price)
            LOGGER.info(
                "LIVE OPEN side=%s size=%.4f token=%s status=%s filled=%.4f",
                signal.side,
                signal.size,
                token_id,
                self.last_report["status"],
                self.last_report["filled_size"],
            )
        except Exception as exc:
            self.last_report = _build_report(None, signal.size, ask_price, error=str(exc))
            LOGGER.error("LIVE OPEN failed side=%s size=%.4f token=%s error=%s", signal.side, signal.size, token_id, exc)
            raise
        return Position(
            side=signal.side,
            size=signal.size,
            entry_price=ask_price,
            edge_at_entry=signal.snapshot.edge_yes if signal.side == OutcomeSide.YES else signal.snapshot.edge_no,
            opened_at=datetime.now(timezone.utc),
        )

    def close_position(self, market, position):
        token_id = market.yes_token_id if position.side == OutcomeSide.YES else market.no_token_id
        try:
            order = self._market_order_args_cls(
                token_id=token_id,
                amount=float(position.size),
                side=self._sell_constant,
                order_type=getattr(self._order_type_cls, self.execution.order_type.upper()),
            )
            signed = self._client.create_market_order(order)
            raw = self._client.post_order(signed, getattr(self._order_type_cls, self.execution.order_type.upper()))
            self.last_report = _build_report(raw, position.size, position.entry_price)
            LOGGER.info(
                "LIVE CLOSE side=%s size=%.4f token=%s status=%s filled=%.4f",
                position.side,
                position.size,
                token_id,
                self.last_report["status"],
                self.last_report["filled_size"],
            )
        except Exception as exc:
            self.last_report = _build_report(None, position.size, position.entry_price, error=str(exc))
            LOGGER.error("LIVE CLOSE failed side=%s size=%.4f token=%s error=%s", position.side, position.size, token_id, exc)
            raise


def build_executor(execution, wallet):
    if execution.mode.lower() == "live":
        return LiveExecutor(execution, wallet)
    return PaperExecutor()
