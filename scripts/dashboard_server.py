#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path("/root/polymarket_bot")
DATA_ROOT = PROJECT_ROOT / "data"


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Polymarket Bot Dashboard</title>
  <style>
    :root {
      --bg: #f5f1e8;
      --panel: #fffdf8;
      --ink: #17202a;
      --muted: #5d6d7e;
      --line: #d9d0c2;
      --good: #0f8b4c;
      --bad: #b42318;
      --accent: #a63d40;
      --accent-2: #155e75;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, #efe5cf 0, transparent 28%),
        radial-gradient(circle at bottom right, #e2eef4 0, transparent 25%),
        var(--bg);
    }
    .wrap {
      max-width: 1480px;
      margin: 0 auto;
      padding: 28px 20px 40px;
    }
    .topbar {
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 18px;
    }
    h1 {
      margin: 0 18px 0 0;
      font-size: 28px;
      letter-spacing: 0.02em;
    }
    select, button {
      height: 40px;
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      padding: 0 12px;
      font: inherit;
      border-radius: 10px;
    }
    button {
      cursor: pointer;
      background: linear-gradient(135deg, #fff, #f2ebe0);
    }
    .muted { color: var(--muted); }
    .grid {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px 16px;
      min-height: 92px;
      box-shadow: 0 8px 24px rgba(23, 32, 42, 0.04);
    }
    .card .label {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .card .value {
      font-size: 24px;
      font-weight: 700;
    }
    .good { color: var(--good); }
    .bad { color: var(--bad); }
    .panels {
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      gap: 14px;
      margin-bottom: 14px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
      box-shadow: 0 8px 24px rgba(23, 32, 42, 0.04);
    }
    .panel h2 {
      margin: 0 0 12px;
      font-size: 16px;
      color: var(--accent-2);
    }
    .kv {
      display: grid;
      grid-template-columns: 140px 1fr;
      gap: 8px 12px;
      font-size: 13px;
      line-height: 1.5;
    }
    .tables {
      display: grid;
      grid-template-columns: 1fr;
      gap: 14px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    th, td {
      padding: 8px 10px;
      border-top: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-weight: 600;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      border-top: 0;
    }
    .pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      background: #efe8da;
      border: 1px solid var(--line);
      font-size: 11px;
    }
    @media (max-width: 1100px) {
      .grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .panels { grid-template-columns: 1fr; }
    }
    @media (max-width: 700px) {
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .kv { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <h1>Polymarket Bot Dashboard</h1>
      <select id="iteration"></select>
      <button id="refresh">刷新</button>
      <span class="muted" id="stamp">-</span>
    </div>

    <div class="grid" id="summary"></div>

    <div class="panels">
      <section class="panel">
        <h2>当前状态</h2>
        <div class="kv" id="state"></div>
      </section>
      <section class="panel">
        <h2>最近持仓与结算</h2>
        <div class="kv" id="meta"></div>
      </section>
    </div>

    <div class="tables">
      <section class="panel">
        <h2>最新成交</h2>
        <div id="fills"></div>
      </section>
      <section class="panel">
        <h2>最新窗口结算</h2>
        <div id="windows"></div>
      </section>
    </div>
  </div>

  <script>
    const iterationEl = document.getElementById("iteration");
    const stampEl = document.getElementById("stamp");

    function fmtNum(value, digits = 4) {
      if (value === null || value === undefined || value === "") return "-";
      const n = Number(value);
      if (Number.isNaN(n)) return String(value);
      return n.toFixed(digits);
    }

    function fmtSigned(value, digits = 4) {
      if (value === null || value === undefined || value === "") return "-";
      const n = Number(value);
      if (Number.isNaN(n)) return String(value);
      return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}`;
    }

    function card(label, value, cls = "") {
      return `<div class="card"><div class="label">${label}</div><div class="value ${cls}">${value}</div></div>`;
    }

    function setSummary(data) {
      const pnlCls = Number(data.summary.total_pnl || 0) >= 0 ? "good" : "bad";
      const ddCls = Number(data.summary.max_drawdown || 0) >= 0 ? "good" : "bad";
      document.getElementById("summary").innerHTML = [
        card("Iteration", data.iteration),
        card("Total PnL", fmtSigned(data.summary.total_pnl), pnlCls),
        card("Windows", data.summary.total_windows),
        card("Traded Windows", data.summary.traded_windows),
        card("Trade Rate", `${fmtNum(data.summary.trade_rate * 100, 2)}%`),
        card("Max Drawdown", fmtSigned(data.summary.max_drawdown), ddCls),
      ].join("");
    }

    function setState(data) {
      const s = data.latest_state || {};
      document.getElementById("state").innerHTML = [
        ["window", s.marketSlug || "-"],
        ["tau", s.timeToExpirySec ?? "-"],
        ["position", s.position || "flat"],
        ["spot", fmtNum(s.spot, 2)],
        ["fair_yes", fmtNum(s.fairYes, 3)],
        ["fair_no", fmtNum(s.fairNo, 3)],
        ["yes_price", fmtNum(s.yesPrice, 3)],
        ["no_price", fmtNum(s.noPrice, 3)],
      ].map(([k,v]) => `<div class="muted">${k}</div><div>${v}</div>`).join("");
    }

    function setMeta(data) {
      const latest = data.latest_traded_window || data.latest_window || {};
      document.getElementById("meta").innerHTML = [
        ["latest_window", latest.marketSlug || "-"],
        ["latest_winner", latest.actualWinner || latest.finalDirection || "-"],
        ["latest_pnl", fmtSigned(latest.realizedPnl)],
        ["latest_fills", ((latest.activity || {}).fillCount ?? 0)],
        ["updated_at", data.summary.last_closed_at || "-"],
      ].map(([k,v]) => `<div class="muted">${k}</div><div>${v}</div>`).join("");
    }

    function renderTable(containerId, columns, rows) {
      const header = `<tr>${columns.map(c => `<th>${c.label}</th>`).join("")}</tr>`;
      const body = rows.map(row => `<tr>${columns.map(c => `<td>${c.render(row)}</td>`).join("")}</tr>`).join("");
      document.getElementById(containerId).innerHTML = `<table>${header}${body}</table>`;
    }

    async function loadIterations() {
      const resp = await fetch("/api/iterations");
      const data = await resp.json();
      iterationEl.innerHTML = data.iterations.map(x => `<option value="${x}">${x}</option>`).join("");
      if (data.iterations.length) {
        iterationEl.value = data.iterations[0];
      }
    }

    async function loadIteration() {
      const name = iterationEl.value;
      if (!name) return;
      const resp = await fetch(`/api/iteration?name=${encodeURIComponent(name)}&limit=60`);
      const data = await resp.json();
      setSummary(data);
      setState(data);
      setMeta(data);
      renderTable("fills", [
        {label: "Time", render: r => r.event_at || "-"},
        {label: "Window", render: r => r.marketSlug || "-"},
        {label: "Side", render: r => `<span class="pill">${r.side || "-"}</span>`},
        {label: "Size", render: r => fmtNum(r.size)},
        {label: "Price", render: r => fmtNum(r.price)},
        {label: "Notional", render: r => fmtNum(r.notional)},
        {label: "Reason", render: r => r.reason || "-"},
      ], data.fills);
      renderTable("windows", [
        {label: "Closed", render: r => r.closed_at || "-"},
        {label: "Window", render: r => r.marketSlug || "-"},
        {label: "Winner", render: r => r.actualWinner || r.finalDirection || "-"},
        {label: "PnL", render: r => `<span class="${Number(r.realizedPnl || 0) >= 0 ? "good" : "bad"}">${fmtSigned(r.realizedPnl)}</span>`},
        {label: "Fills", render: r => ((r.activity || {}).fillCount ?? 0)},
        {label: "Source", render: r => r.resolutionSource || "-"},
      ], data.windows);
      stampEl.textContent = `updated ${new Date().toLocaleString()}`;
    }

    document.getElementById("refresh").addEventListener("click", loadIteration);
    iterationEl.addEventListener("change", loadIteration);

    (async () => {
      await loadIterations();
      await loadIteration();
    })();
  </script>
</body>
</html>
"""


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _fmt_ts_ms(ms: int | None) -> str:
    if not ms:
        return "-"
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).astimezone().strftime("%m-%d %H:%M:%S")


def _max_drawdown(window_rows: list[dict]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for row in sorted(window_rows, key=lambda item: int(item.get("closedAtMs", 0) or 0)):
        equity += float(row.get("realizedPnl", 0.0) or 0.0)
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return max_dd


def _version_sort_key(name: str):
    if not name.startswith("v"):
        return (0, name)
    parts = []
    for part in name[1:].split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(part)
    return tuple(parts)


def _iterations() -> list[str]:
    if not DATA_ROOT.exists():
        return []
    items = [path.name for path in DATA_ROOT.iterdir() if path.is_dir()]
    return sorted(items, key=_version_sort_key, reverse=True)


def _iteration_payload(name: str, limit: int) -> dict:
    data_dir = DATA_ROOT / name
    window_rows = sorted(_read_jsonl(data_dir / "window_close.jsonl"), key=lambda item: int(item.get("closedAtMs", 0) or 0))
    activity_rows = sorted(_read_jsonl(data_dir / "activity.jsonl"), key=lambda item: int(item.get("eventAtMs", 0) or 0))
    state_rows = _read_jsonl(data_dir / "market_state.jsonl")
    fill_rows = [row for row in activity_rows if row.get("eventType") == "fill"]
    traded_windows = [row for row in window_rows if int(((row.get("activity") or {}).get("fillCount", 0))) > 0]
    latest_state = next((row for row in reversed(state_rows) if row.get("recordType") == "state"), None)
    latest_window = window_rows[-1] if window_rows else None
    latest_traded_window = traded_windows[-1] if traded_windows else None

    fills = []
    for row in fill_rows[-limit:]:
        fills.append(
            {
                "event_at": _fmt_ts_ms(int(row.get("eventAtMs", 0) or 0)),
                "marketSlug": row.get("marketSlug"),
                "side": row.get("side"),
                "size": row.get("size"),
                "price": row.get("price"),
                "notional": (float(row.get("size", 0.0) or 0.0) * float(row.get("price", 0.0) or 0.0)),
                "reason": row.get("reason"),
            }
        )

    windows = []
    for row in window_rows[-limit:]:
        normalized = dict(row)
        normalized["closed_at"] = _fmt_ts_ms(int(row.get("closedAtMs", 0) or 0))
        windows.append(normalized)

    summary = {
        "total_windows": len(window_rows),
        "traded_windows": len(traded_windows),
        "trade_rate": 0.0 if not window_rows else len(traded_windows) / len(window_rows),
        "total_pnl": sum(float(row.get("realizedPnl", 0.0) or 0.0) for row in window_rows),
        "max_drawdown": _max_drawdown(window_rows),
        "last_closed_at": _fmt_ts_ms(int((latest_window or {}).get("closedAtMs", 0) or 0)),
    }

    return {
        "iteration": name,
        "summary": summary,
        "latest_state": latest_state,
        "latest_window": latest_window,
        "latest_traded_window": latest_traded_window,
        "fills": list(reversed(fills)),
        "windows": list(reversed(windows)),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/iterations":
            body = json.dumps({"iterations": _iterations()}, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if parsed.path == "/api/iteration":
            query = parse_qs(parsed.query)
            name = (query.get("name") or [""])[0]
            limit = int((query.get("limit") or ["50"])[0])
            if not name:
                self._send(400, b'{"error":"missing iteration"}', "application/json; charset=utf-8")
                return
            payload = _iteration_payload(name, limit)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):  # noqa: A003
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve a local dashboard for iteration data")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"dashboard listening on http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
