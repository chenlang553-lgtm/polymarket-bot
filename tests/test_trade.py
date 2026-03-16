import json
import subprocess
import sys

from trade import OrderRequest, SimulatedPolymarketTrader


def test_simulated_full_fill():
    trader = SimulatedPolymarketTrader()
    req = OrderRequest(token_id="t1", side="buy", size=20, price=0.72, order_type="FAK")
    report = trader.submit_market_fak_order(req)

    assert report.success is True
    assert report.status == "filled"
    assert report.filled_size == 20
    assert report.avg_price == 0.72
    assert report.mode == "simulate"


def test_simulated_partial_fill():
    trader = SimulatedPolymarketTrader()
    req = OrderRequest(token_id="t1", side="buy", size=70, order_type="FAK")
    report = trader.submit_market_fak_order(req)

    assert report.success is True
    assert report.status == "partial"
    assert report.filled_size == 50


def test_order_request_validation():
    req = OrderRequest(token_id="", side="buy", size=1, order_type="FAK")
    try:
        req.validate()
    except ValueError as exc:
        assert "token_id" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_price_validation():
    req = OrderRequest(token_id="t1", side="buy", size=1, price=1.2, order_type="FAK")
    try:
        req.validate()
    except ValueError as exc:
        assert "price" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_only_fak_order_type_supported():
    req = OrderRequest(token_id="t1", side="buy", size=1, order_type="GTC")
    try:
        req.validate()
    except ValueError as exc:
        assert "FAK" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_live_requires_api_credentials():
    proc = subprocess.run(
        [
            sys.executable,
            "trade.py",
            "--live",
            "--token-id",
            "demo_yes_token",
            "--side",
            "buy",
            "--size",
            "1",
            "--order-type",
            "FAK",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert payload["status"] == "rejected"
    assert "--api-key" in payload["error"]