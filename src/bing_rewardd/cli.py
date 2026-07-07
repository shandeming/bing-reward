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
from bing_rewardd.rewards import guide_tasks


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
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        help="Optional directory for a final browser screenshot.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "tasks",
        help="Detect and complete all visible Rewards tasks.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = BrowserConfig(profile_dir=args.profile_dir, slow_mo_ms=args.slow_mo)

    with launch_persistent_browser(config) as context:
        page = open_url(context, BING_URL)

        try:
            if args.command == "tasks":
                guide_tasks(page)
            else:
                raise AssertionError(f"Unhandled command: {args.command}")
        finally:
            if args.screenshot_dir:
                _save_final_screenshot(page, args.screenshot_dir)

    return 0


def _save_final_screenshot(page, screenshot_dir: Path) -> None:
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    try:
        page.wait_for_timeout(3000)
    except Exception:
        pass
    page.screenshot(path=str(screenshot_dir / "final.png"), full_page=True)


if __name__ == "__main__":
    raise SystemExit(main())