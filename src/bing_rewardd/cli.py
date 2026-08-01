from __future__ import annotations

import argparse
from pathlib import Path

from bing_rewardd.browser import (
    BING_URL,
    BrowserConfig,
    default_storage_state_path,
    launch_browser,
    open_url,
)
from bing_rewardd.rewards import guide_tasks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bing-rewardd",
        description="Open Bing and guide Microsoft Rewards tasks with explicit user confirmation.",
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
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser in headless mode (no visible window).",
    )
    parser.add_argument(
        "--storage-state",
        type=Path,
        default=default_storage_state_path(),
        help="Load cookies and localStorage from a Playwright storage-state JSON file.",
    )
    parser.add_argument(
        "--save-storage-state",
        type=Path,
        help="Save the current cookies and localStorage to a Playwright storage-state JSON file.",
    )
    parser.add_argument(
        "--extra-args",
        nargs="*",
        help="Extra arguments to pass to the browser (e.g. --extra-args --no-sandbox).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "tasks",
        help="Detect and complete all visible Rewards tasks.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = BrowserConfig(
        headless=args.headless,
        slow_mo_ms=args.slow_mo,
        extra_args=args.extra_args or [],
        storage_state=args.storage_state,
    )

    with launch_browser(config) as context:
        page = open_url(context, BING_URL)

        try:
            if args.command == "tasks":
                guide_tasks(page)
            else:
                raise AssertionError(f"Unhandled command: {args.command}")
        finally:
            if args.screenshot_dir:
                _save_final_screenshot(page, args.screenshot_dir)
            save_path = args.save_storage_state or args.storage_state
            if save_path:
                save_path.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(save_path))

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
