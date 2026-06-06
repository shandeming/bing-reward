from __future__ import annotations

import argparse
from pathlib import Path

from bing_rewardd.browser import (
    BING_URL,
    BrowserConfig,
    default_profile_dir,
    launch_persistent_browser,
    open_url,
)
from bing_rewardd.rewards import (
    guide_tasks,
    open_rewards_sidebar,
    run_confirmed_searches,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bing-rewardd",
        description="Open Bing and guide Microsoft Rewards tasks with explicit user confirmation.",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=default_profile_dir(),
        help="Persistent browser profile directory. Defaults to .browser-profile.",
    )
    parser.add_argument(
        "--slow-mo",
        type=int,
        default=0,
        help="Optional Playwright slow motion delay in milliseconds.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("start", help="Open Chrome to Bing.")
    subparsers.add_parser(
        "rewards", help="Open Bing and show the Microsoft Rewards sidebar."
    )
    subparsers.add_parser(
        "tasks",
        help="List visible Rewards sidebar tasks and prompt before opening each.",
    )
    subparsers.add_parser(
        "search", help="Submit user-provided Bing searches after per-term confirmation."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = BrowserConfig(profile_dir=args.profile_dir, slow_mo_ms=args.slow_mo)

    with launch_persistent_browser(config) as context:
        if args.command == "tasks":
            page = open_url(context, BING_URL)
            guide_tasks(page)
            _wait_for_exit()
        else:
            raise AssertionError(f"Unhandled command: {args.command}")

    return 0


def _wait_for_exit() -> None:
    input("Browser is open. Press Enter to close this assistant session.")


if __name__ == "__main__":
    raise SystemExit(main())
