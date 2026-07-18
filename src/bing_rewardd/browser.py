from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext, Error, Page, Playwright, sync_playwright

BING_URL = "https://www.bing.com"


@dataclass(frozen=True)
class BrowserConfig:
    profile_dir: Path
    headless: bool = False
    slow_mo_ms: int = 0
    extra_args: list[str] = field(default_factory=list)


def default_profile_dir(base_dir: Path | None = None) -> Path:
    root = base_dir or Path.cwd()
    return root / ".browser-profile"


@contextmanager
def launch_persistent_browser(config: BrowserConfig) -> Iterator[BrowserContext]:
    config.profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = _launch_with_preferred_channel(playwright, config)
        try:
            yield context
        finally:
            context.close()


def _launch_with_preferred_channel(
    playwright: Playwright, config: BrowserConfig
) -> BrowserContext:
    launch_args = {
        "user_data_dir": str(config.profile_dir),
        "headless": config.headless,
        "slow_mo": config.slow_mo_ms,
        "viewport": {"width": 1280, "height": 900},
    }
    if config.extra_args:
        launch_args["args"] = config.extra_args

    try:
        return playwright.chromium.launch_persistent_context(
            channel="chrome", **launch_args
        )
    except Error as exc:
        chrome_missing = "Chromium distribution 'chrome' is not found" in str(exc)
        if not chrome_missing:
            # Chrome exists but crashed (e.g. SIGTRAP in WSL2)
            print(f"[!] Chrome channel crashed ({type(exc).__name__}); falling back to bundled Chromium.")
        else:
            print("[!] Chrome channel not found; falling back to bundled Chromium.")
        return playwright.chromium.launch_persistent_context(**launch_args)


def active_page(context: BrowserContext) -> Page:
    if context.pages:
        return context.pages[0]
    return context.new_page()


def open_url(context: BrowserContext, url: str) -> Page:
    page = active_page(context)
    page.goto(url, wait_until="domcontentloaded")
    return page
