#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path("/root/polymarket_bot")
TZ = datetime.now().astimezone().tzinfo or timezone.utc


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


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"last_closed_at_ms": 0, "last_activity_at_ms": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"last_closed_at_ms": 0, "last_activity_at_ms": 0}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _fmt_money(value: float) -> str:
    return f"{value:+.4f}"


def _fmt_time(ms: int | None) -> str:
    if not ms:
        return "-"
    return datetime.fromtimestamp(ms / 1000.0, tz=TZ).strftime("%m-%d %H:%M:%S")


def _send_wecom(content: str) -> None:
    script_path = PROJECT_ROOT / "scripts" / "wecom_send.py"
    subprocess.run(
        [sys.executable, str(script_path), "--content", content],
        check=True,
        cwd=str(PROJECT_ROOT),
    )


def _latest_state_record(path: Path) -> dict | None:
    records = _read_jsonl(path)
    for record in reversed(records):
        if record.get("recordType") == "state":
            return record
    return None


def _today_pnl(rows: list[dict]) -> float:
    today = datetime.now(tz=TZ).date()
    total = 0.0
    for row in rows:
        closed_at = row.get("closedAtMs")
        if not closed_at:
            continue
        if datetime.fromtimestamp(closed_at / 1000.0, tz=TZ).date() == today:
            total += float(row.get("realizedPnl", 0.0) or 0.0)
    return total


def _summarize(iteration: str, data_dir: Path, state: dict) -> tuple[str, dict]:
    window_rows = sorted(_read_jsonl(data_dir / "window_close.jsonl"), key=lambda item: item.get("closedAtMs", 0))
    activity_rows = sorted(_read_jsonl(data_dir / "activity.jsonl"), key=lambda item: item.get("eventAtMs", 0))
    latest_state = _latest_state_record(data_dir / "market_state.jsonl")

    new_windows = [row for row in window_rows if int(row.get("closedAtMs", 0)) > int(state.get("last_closed_at_ms", 0))]
    new_activity = [row for row in activity_rows if int(row.get("eventAtMs", 0)) > int(state.get("last_activity_at_ms", 0))]
    fill_rows = [row for row in new_activity if row.get("eventType") == "fill"]
    execution_rows = [row for row in new_activity if row.get("eventType") == "execution"]
    traded_new_windows = [row for row in new_windows if int(((row.get("activity") or {}).get("fillCount", 0))) > 0]

    delta_pnl = sum(float(row.get("realizedPnl", 0.0) or 0.0) for row in new_windows)
    total_pnl = sum(float(row.get("realizedPnl", 0.0) or 0.0) for row in window_rows)
    today_pnl = _today_pnl(window_rows)

    side_counter = Counter(row.get("side", "Unknown") for row in fill_rows)
    fill_notional = sum(float(row.get("size", 0.0) or 0.0) * float(row.get("price", 0.0) or 0.0) for row in fill_rows)

    latest_traded = traded_new_windows[-1] if traded_new_windows else None
    latest_any = window_rows[-1] if window_rows else None

    lines = [
        f"[{iteration}] 5分钟订单监督",
        f"窗口增量: {len(new_windows)} | 交易窗口: {len(traded_new_windows)}",
        f"新增fill: {len(fill_rows)} | 执行事件: {len(execution_rows)} | 新增成交额: {fill_notional:.4f}",
        f"本轮PnL: {_fmt_money(delta_pnl)} | 累计PnL: {_fmt_money(total_pnl)} | 今日PnL: {_fmt_money(today_pnl)}",
        f"方向分布: Up={side_counter.get('Up', 0)} Down={side_counter.get('Down', 0)}",
    ]

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

    next_state = {
        "last_closed_at_ms": max([int(state.get("last_closed_at_ms", 0))] + [int(row.get("closedAtMs", 0)) for row in new_windows]),
        "last_activity_at_ms": max([int(state.get("last_activity_at_ms", 0))] + [int(row.get("eventAtMs", 0)) for row in new_activity]),
    }
    return "\n".join(lines), next_state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send 5-minute live trading summaries to WeCom")
    parser.add_argument("iteration")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between pushes")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_dir = PROJECT_ROOT / "data" / args.iteration
    state_path = PROJECT_ROOT / ".runtime" / f"monitor_{args.iteration}.json"

    if not data_dir.exists():
        print(f"Data dir not found: {data_dir}", file=sys.stderr)
        return 1

    while True:
        state = _load_state(state_path)
        content, next_state = _summarize(args.iteration, data_dir, state)
        _send_wecom(content)
        _save_state(state_path, next_state)
        if args.once:
            return 0
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
