from __future__ import print_function

from collections import defaultdict
from datetime import datetime, timezone

from .archive import load_window_records


def build_report(records):
    total_windows = len(records)
    traded_windows = 0
    total_realized = 0.0
    winning_windows = 0
    losses = 0
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    by_strategy = defaultdict(lambda: {"windows": 0, "traded": 0, "realized": 0.0})
    by_day = defaultdict(lambda: {"windows": 0, "traded": 0, "realized": 0.0})

    for record in records:
        strategy_type = record.get("strategyType") or "none"
        realized = float(record.get("realizedPnl", 0.0) or 0.0)
        fill_count = int(record.get("activity", {}).get("fillCount", 0) or 0)
        closed_at_ms = int(record.get("closedAtMs", 0) or 0)
        closed_day = "unknown"
        if closed_at_ms > 0:
            closed_day = datetime.fromtimestamp(closed_at_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")

        total_realized += realized
        equity += realized
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)

        if realized > 0:
            winning_windows += 1
        elif realized < 0:
            losses += 1
        if fill_count > 0:
            traded_windows += 1

        by_strategy[strategy_type]["windows"] += 1
        by_strategy[strategy_type]["realized"] += realized
        by_day[closed_day]["windows"] += 1
        by_day[closed_day]["realized"] += realized
        if fill_count > 0:
            by_strategy[strategy_type]["traded"] += 1
            by_day[closed_day]["traded"] += 1

    average_realized = 0.0 if total_windows == 0 else total_realized / total_windows
    win_rate = 0.0 if total_windows == 0 else float(winning_windows) / float(total_windows)
    trade_rate = 0.0 if total_windows == 0 else float(traded_windows) / float(total_windows)

    return {
        "summary": {
            "total_windows": total_windows,
            "traded_windows": traded_windows,
            "trade_rate": trade_rate,
            "winning_windows": winning_windows,
            "losing_windows": losses,
            "win_rate": win_rate,
            "total_realized_pnl": total_realized,
            "average_realized_pnl": average_realized,
            "max_drawdown": max_drawdown,
        },
        "by_strategy": dict(sorted(by_strategy.items())),
        "by_day": dict(sorted(by_day.items())),
    }


def render_report(report):
    summary = report["summary"]
    lines = [
        "SUMMARY",
        "total_windows=%s traded_windows=%s trade_rate=%.2f%% win_rate=%.2f%% total_realized_pnl=%.4f avg_realized_pnl=%.4f max_drawdown=%.4f"
        % (
            summary["total_windows"],
            summary["traded_windows"],
            summary["trade_rate"] * 100.0,
            summary["win_rate"] * 100.0,
            summary["total_realized_pnl"],
            summary["average_realized_pnl"],
            summary["max_drawdown"],
        ),
        "",
        "BY_STRATEGY",
    ]
    for strategy_type, item in report["by_strategy"].items():
        lines.append(
            "%s windows=%s traded=%s realized_pnl=%.4f"
            % (strategy_type, item["windows"], item["traded"], item["realized"])
        )
    lines.append("")
    lines.append("BY_DAY")
    for day, item in report["by_day"].items():
        lines.append(
            "%s windows=%s traded=%s realized_pnl=%.4f"
            % (day, item["windows"], item["traded"], item["realized"])
        )
    return "\n".join(lines)


def run_report(path):
    records = load_window_records(path)
    return render_report(build_report(records))
