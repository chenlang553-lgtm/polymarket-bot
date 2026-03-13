from datetime import datetime, timezone
import logging

from .models import MarketDefinition, OutcomeSide, Position, TradeSignal


LOGGER = logging.getLogger(__name__)


class PaperExecutor:
    def __init__(self):
        self.fills = []

    def open_position(self, market, signal, ask_price):
        LOGGER.info("PAPER OPEN side=%s size=%.4f price=%.4f reason=%s", signal.side, signal.size, ask_price, signal.reason)
        self.fills.append({"side": signal.side, "size": signal.size, "price": ask_price, "status": "filled"})
        return Position(
            side=signal.side,
            size=signal.size,
            entry_price=ask_price,
            edge_at_entry=signal.snapshot.edge_yes if signal.side == OutcomeSide.YES else signal.snapshot.edge_no,
            opened_at=datetime.now(timezone.utc),
        )

    def close_position(self, market, position):
        LOGGER.info("PAPER CLOSE side=%s size=%.4f entry=%.4f", position.side, position.size, position.entry_price)


class LiveExecutor:
    def __init__(self, execution, wallet):
        self.execution = execution
        self.wallet = wallet
        self.__post_init__()

    def __post_init__(self) -> None:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY
        except ImportError as exc:
            raise RuntimeError("live trading requires pip install -e .[trading]") from exc
        self._client_cls = ClobClient
        self._market_order_args_cls = MarketOrderArgs
        self._order_type_cls = OrderType
        self._buy_constant = BUY
        self._client = ClobClient(
            "https://clob.polymarket.com",
            key=self.wallet.private_key,
            chain_id=self.wallet.chain_id,
            signature_type=self.wallet.signature_type,
            funder=self.wallet.funder or None,
        )
        self._client.set_api_creds(self._client.create_or_derive_api_creds())

    def open_position(self, market, signal, ask_price):
        token_id = market.yes_token_id if signal.side == OutcomeSide.YES else market.no_token_id
        order = self._market_order_args_cls(
            token_id=token_id,
            amount=float(signal.size),
            side=self._buy_constant,
            order_type=getattr(self._order_type_cls, self.execution.order_type.upper()),
        )
        signed = self._client.create_market_order(order)
        self._client.post_order(signed, getattr(self._order_type_cls, self.execution.order_type.upper()))
        LOGGER.info("LIVE OPEN side=%s size=%.4f token=%s", signal.side, signal.size, token_id)
        return Position(
            side=signal.side,
            size=signal.size,
            entry_price=ask_price,
            edge_at_entry=signal.snapshot.edge_yes if signal.side == OutcomeSide.YES else signal.snapshot.edge_no,
            opened_at=datetime.now(timezone.utc),
        )

    def close_position(self, market, position):
        LOGGER.warning("live close is not implemented; flatten manually or extend executor")


def build_executor(execution, wallet):
    if execution.mode.lower() == "live":
        return LiveExecutor(execution, wallet)
    return PaperExecutor()
