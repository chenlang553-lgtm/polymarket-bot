# Patch Suggestions: Top 3 Critical Issues

This document provides concrete patch suggestions (minimal, targeted) for:
1. midpoint pricing
2. stale book gating
3. live `close_position` implementation

---

## 1) Midpoint pricing -> executable ask pricing

### Problem
Current edge computation uses `market_price()` (midpoint/last-trade fallbacks), which violates the strategy requirement to compute edge from executable ask prices.

### Target files
- `src/polymarket_bot/strategy.py`
- `src/polymarket_bot/models.py` (optional helper)
- `tests/test_strategy.py`

### Suggested patch

#### A. Use ask-side prices in `compute_snapshot`
In `StrategyEngine.compute_snapshot()`, replace:
- `yes_price = book_yes.market_price()`
- `no_price = book_no.market_price()`

with ask-based pricing:

```python
yes_price = book_yes.ask if yes_price is None else yes_price
no_price = book_no.ask if no_price is None else no_price
```

Then keep:

```python
edge_yes = None if yes_price is None else fair_yes - yes_price
edge_no = None if no_price is None else fair_no - no_price
```

This directly aligns edges with executable buy prices.

#### B. Tighten evaluate guardrail
In `StrategyEngine.evaluate()`, change `missing_market_price` semantics to ask availability:
- If selected side has no ask, `HOLD(reason="missing_executable_ask")`.

Optionally require both sides ask quotes when computing both edges to avoid asymmetric stale comparisons.

#### C. Update tests
Add/modify tests to assert:
- `compute_snapshot` uses ask, not midpoint.
- Edge equals `fair - ask`.
- Entry is blocked when selected side ask is missing.

---

## 2) Stale book gating

### Problem
`_effective_book()` computes fallback age limits but always returns `current`. Stale fallback is never actually filtered.

### Target files
- `src/polymarket_bot/app.py`
- `src/polymarket_bot/models.py` (optional helper)
- `tests/test_strategy.py` (or new app-level tests)

### Suggested patch

#### A. Make fallback age logic effective
Proposed replacement for `_effective_book()` in `app.py`:

```python
def _effective_book(self, asset_id, now):
    latest_book = self._latest_books.get(asset_id)
    fallback_book = self._last_usable_books.get(asset_id)

    if latest_book is None and fallback_book is None:
        return BestBidAsk(asset_id=asset_id, bid=None, ask=None)

    reference_ms = int(now.timestamp() * 1000)
    max_age_ms = int(self.config.strategy.book_fallback_max_age_seconds * 1000)

    # Prefer latest if present and fresh enough.
    if latest_book is not None:
        if latest_book.timestamp_ms:
            if (reference_ms - latest_book.timestamp_ms) <= max_age_ms:
                return latest_book
        else:
            # No timestamp on latest: conservative fallback to latest object
            return latest_book

    # Otherwise use fallback only if fresh.
    if fallback_book is not None and fallback_book.timestamp_ms:
        if (reference_ms - fallback_book.timestamp_ms) <= max_age_ms:
            return fallback_book

    # Stale / unusable
    return BestBidAsk(asset_id=asset_id, bid=None, ask=None)
```

#### B. Ensure stale books block trading
Because `compute_snapshot` / `evaluate` depend on ask availability, returning empty book (`ask=None`) automatically prevents entries.

#### C. Add tests
Add tests for:
- Fresh latest book selected.
- Stale latest + fresh fallback selects fallback.
- Both stale returns empty book and strategy holds.

---

## 3) Live `close_position` implementation

### Problem
`LiveExecutor.close_position()` is unimplemented, so live exits/flip-close risk control cannot execute.

### Target file
- `src/polymarket_bot/execution.py`

### Suggested patch

Implement a sell market order for the held token (YES/NO token chosen by current position side).

```python
def close_position(self, market, position):
    token_id = market.yes_token_id if position.side == OutcomeSide.YES else market.no_token_id

    # BUY constant exists today; import SELL in __post_init__ similarly.
    order = self._market_order_args_cls(
        token_id=token_id,
        amount=float(position.size),
        side=self._sell_constant,
        order_type=getattr(self._order_type_cls, self.execution.order_type.upper()),
    )
    signed = self._client.create_market_order(order)
    self._client.post_order(signed, getattr(self._order_type_cls, self.execution.order_type.upper()))
    LOGGER.info("LIVE CLOSE side=%s size=%.4f token=%s", position.side, position.size, token_id)
```

In `__post_init__`, import and store `SELL`:

```python
from py_clob_client.order_builder.constants import BUY, SELL
...
self._sell_constant = SELL
```

If the client API does not support SELL on this endpoint, fail fast at startup in live mode with explicit actionable error (rather than silently warning at runtime).

### Optional hardening
- Track order/fill response IDs in logs.
- Add exception handling with retries/backoff around post calls.
- For `FLIP`, ensure close succeeds before issuing open.

---

## Minimal acceptance checks after patching

1. Strategy pricing:
- edge calculations reference `ask` in snapshot logs.

2. Stale gating:
- stale books produce `missing_executable_ask`/hold outcomes.

3. Live close:
- `close_position` creates and posts an opposite-side market order in live mode.

4. Tests:
- Add targeted unit tests for all three behaviors.
