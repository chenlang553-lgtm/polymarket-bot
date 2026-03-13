import argparse
import asyncio
import logging
from pprint import pprint

from .app import TradingApplication
from .archive import load_window_records
from .config import load_config
from .gamma import resolve_market
from .replay import run_replay
from .report import run_report


def main():
    parser = argparse.ArgumentParser(prog="polymarket-bot")
    parser.add_argument("command", choices=["inspect", "run", "report", "replay"])
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--profile", default="")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    config = load_config(args.config, profile=args.profile or None)
    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.command == "inspect":
        market = resolve_market(config.market)
        pprint(market)
        return

    if args.command == "report":
        print(run_report(config.logging.window_close_path))
        return

    if args.command == "replay":
        print(run_replay(config.logging.market_state_path, limit=args.limit))
        return

    app = TradingApplication(config)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
