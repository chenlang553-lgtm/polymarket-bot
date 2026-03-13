import argparse
import asyncio
import logging
import os
from pprint import pprint

from .app import TradingApplication
from .archive import load_window_records
from .config import apply_iteration_paths, load_config
from .gamma import current_window_start, resolve_market, resolve_market_for_window
from .replay import run_replay
from .report import run_report
from .validate import render_validation, validate_config


def main():
    parser = argparse.ArgumentParser(prog="polymarket-bot")
    parser.add_argument("command", choices=["inspect", "run", "report", "replay", "validate"])
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--profile", default="")
    parser.add_argument("--iteration", default="")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    config = load_config(args.config, profile=args.profile or None)
    runtime_paths = apply_iteration_paths(config, args.iteration or "", os.path.dirname(os.path.abspath(args.config)))
    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.command == "inspect":
        if config.market.market_slug or config.market.condition_id or (config.market.yes_token_id and config.market.no_token_id):
            market = resolve_market(config.market)
        else:
            market = resolve_market_for_window(config.market, current_window_start())
        pprint(market)
        return

    if args.command == "report":
        print(run_report(config.logging.window_close_path))
        return

    if args.command == "replay":
        print(run_replay(config.logging.market_state_path, limit=args.limit))
        return

    if args.command == "validate":
        result = validate_config(config)
        print(render_validation(result))
        if result["errors"]:
            raise SystemExit(1)
        return

    app = TradingApplication(config)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
