"""Command line entrypoint for setup checks, MTProto login and runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from pydantic import ValidationError

from market_digest.application import (
    login_telegram,
    preflight_codex,
    run_app,
    safe_settings_summary,
)
from market_digest.config import Settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="market-digest")
    parser.add_argument(
        "command",
        nargs="?",
        default="app",
        choices=("app", "login", "check-config", "codex-preflight"),
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        settings = Settings()
    except ValidationError as exc:
        print("Configuration error. Check .env:\n" + str(exc), file=sys.stderr)
        raise SystemExit(2) from exc

    if args.command == "check-config":
        print(json.dumps(safe_settings_summary(settings), ensure_ascii=False, indent=2))
        return
    try:
        if args.command == "login":
            asyncio.run(login_telegram(settings))
            print("Telegram MTProto session authorized successfully.")
        elif args.command == "codex-preflight":
            asyncio.run(preflight_codex(settings))
            print("Codex research and analyst routes: Structured Outputs preflight passed.")
        elif args.command == "app":
            asyncio.run(run_app(settings))
        else:
            asyncio.run(run_app(settings))
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
