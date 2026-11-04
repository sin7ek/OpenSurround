from __future__ import annotations
import argparse
import asyncio
import logging
from .config import load_config
from .server import run


def main() -> None:
    p = argparse.ArgumentParser(description="OpenSurround Sendspin PoC")
    p.add_argument("wav", help="16-bit multichannel PCM WAV")
    p.add_argument("-c", "--config", default="configs/4ch-demo.toml")
    p.add_argument("--state-dir", default=".opensurround")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run(load_config(args.config), args.wav, args.state_dir))

if __name__ == "__main__":
    main()
