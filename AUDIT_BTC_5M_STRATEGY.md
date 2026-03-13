# BTC 5-Minute Strategy Compliance Audit

This document audits the current implementation against the provided strategy specification.

## Verdict

Partial match only. Core math pieces exist (x_t, multi-horizon volatility, drift, jump multipliers, edge thresholds, sizing buckets), but there are material deviations in pricing/execution realism, window boundary handling, and live exit capability that make the implementation **not faithful enough for production use as-specified**.

## Key mismatches vs specification

1. **Edges are computed from midpoint/last trade, not executable asks.**
   - `StrategyEngine.compute_snapshot()` uses `book_yes.market_price()` / `book_no.market_price()` for `yes_price` / `no_price`, then computes edges from those values.
   - `market_price()` returns midpoint when bid+ask exist, not ask.
   - Spec requires ask-side executable prices for entry edge.

2. **Decision window starts at tau <=45, but model sigma weighting includes 40<tau<=60 branch not used for entries.**
   - This is not necessarily wrong, but it introduces dead config/logic for tau in (45,60] because entry is blocked by decision window defaults.

3. **Optional OBI/trade-imbalance layer is always on and non-small by default.**
   - The implementation always applies `logit` correction with fixed coefficients (`0.08 * obi + 0.05 * trade_imbalance`), rather than making this clearly optional/tunable as a small additive layer.

4. **Stale-book fallback age check is effectively a no-op.**
   - `_effective_book()` computes age bounds but always returns `current` in all branches. This means stale quote handling is not enforced despite config field suggesting it is.

5. **Live close/flatten is unimplemented.**
   - `LiveExecutor.close_position()` logs warning only. Exit rules cannot be executed in live mode.

6. **Window-open anchor (S0) may be stale/implicit rather than strict event-open tick.**
   - On market reset, state seeds `open_price` with the latest observed spot at reset time if available. If reset lags true market open, S0 may be shifted.

7. **Mislabeling bug in stats fields.**
   - `last_yes_bid` is assigned from `snapshot.no_price`, indicating YES/NO fields are mixed in archival stats.

## Formula/sign/unit checks

- `x_t = ln(S_t/S_0)` implemented correctly.
- `m5`, `m15` implemented as finite-difference slopes over x history and divided by horizon seconds.
- Drift uses `0.2*m5*tau + 0.1*m15*tau` (configurable defaults match spec).
- `sigma_eff` piecewise weights match specified ranges for (40,60], (15,40], (5,15].
- Jump/outlier multipliers and thresholds match spec defaults.
- `z=(x_t+drift)/(sigma_eff*sqrt(tau))` implemented, with denominator floor.

Potential unit caveat:
- Returns are computed from every incoming price update, assumed to be 1-second returns. If feed cadence is irregular/high-frequency, `sigma_10/sigma_30` are sample-count windows, not true time windows.

## Lookahead / leakage

- No explicit future-leakage found in strategy math; features are based on rolling historical state.
- Main realism risk is quote staleness and midpoint-based edge estimation, which can make backtests/sim behavior optimistic relative to executable conditions.

## Entry/exit/sizing behavior

- Entry gate uses 8 <= tau <= 45 (default config aligns with spec).
- Min edge default 4% aligns with lower end of required 4%-6% band.
- Size buckets match exactly (0.5,1.0,1.5 at 4/6/10%).
- No pyramiding/averaging-down is respected because only one position is stored; repeated OPEN while holding is not allowed.
- Exit:
  - If tau>15 and edge decays below half entry edge, CLOSE.
  - If side flips, FLIP occurs at any tau in decision window (including <=15), which is consistent with “hold unless fully flips sides”.

## Priority fixes

1. Compute `edge_up/edge_down` using executable ask prices directly (`ask` for buy side), not midpoint/last-trade fallbacks.
2. Fix `_effective_book()` fallback age logic to actually reject stale books and require fresh/executable quotes before trading.
3. Implement real live close/flatten path in `LiveExecutor.close_position()`; without this, risk controls are incomplete.
4. Make microstructure correction explicitly optional and lower-weight/tunable; allow disabling via config.
5. Enforce true 1-second sampling for return windows (resample/interpolate or time-based deques), not message-count windows.
6. Correct stats bug assigning `last_yes_bid` from NO price.
7. Add tests for: ask-based edge, stale-book rejection, live-close behavior (or explicit fail-fast in live mode), and event-open anchoring assumptions.
