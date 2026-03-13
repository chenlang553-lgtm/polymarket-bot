import argparse
import asyncio
import logging
from pprint import pprint

from .app import TradingApplication
from .config import load_config
from .gamma import resolve_market


def main():
    parser = argparse.ArgumentParser(prog="polymarket-bot")
    parser.add_argument("command", choices=["inspect", "run"])
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()

    config = load_config(args.config)
    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.command == "inspect":
        market = resolve_market(config.market)
        pprint(market)
        return

    app = TradingApplication(config)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
