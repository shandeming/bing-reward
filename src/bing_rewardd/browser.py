from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from playwright.sync_api import Browser, BrowserContext, Error, Page, Playwright, sync_playwright

BING_URL = (
    "https://cn.bing.com/"
    "?features=vstooltip"
    "&form=ML2XYA"
    "&OCID=ML2XYA"
    "&PUBL=RewardsDO"
    "&CREA=ML2XYA"
    "&rdr=1"
)


@dataclass(frozen=True)
class BrowserConfig:
    headless: bool = False
    slow_mo_ms: int = 0
    extra_args: list[str] = field(default_factory=list)
    storage_state: Path | None = None


def default_storage_state_path(base_dir: Path | None = None) -> Path:
    root = base_dir or Path.cwd()
    return root / "storage_state.json"


@contextmanager
def launch_browser(config: BrowserConfig) -> Iterator[BrowserContext]:
    with sync_playwright() as playwright:
        browser = _launch_with_preferred_channel(playwright, config)
        context_args: dict[str, object] = {
            "viewport": {"width": 1280, "height": 900},
        }
        if config.storage_state and config.storage_state.exists():
            context_args["storage_state"] = str(config.storage_state)
        context = browser.new_context(**context_args)
        try:
            yield context
        finally:
            context.close()
            browser.close()


def _launch_with_preferred_channel(
    playwright: Playwright, config: BrowserConfig
) -> Browser:
    launch_args = {
        "headless": config.headless,
        "slow_mo": config.slow_mo_ms,
    }
    if config.extra_args:
        launch_args["args"] = config.extra_args

    try:
        return playwright.chromium.launch(channel="chrome", **launch_args)
    except Error as exc:
        chrome_missing = "Chromium distribution 'chrome' is not found" in str(exc)
        if not chrome_missing:
            # Chrome exists but crashed (e.g. SIGTRAP in WSL2)
            print(f"[!] Chrome channel crashed ({type(exc).__name__}); falling back to bundled Chromium.")
        else:
            print("[!] Chrome channel not found; falling back to bundled Chromium.")
        return playwright.chromium.launch(**launch_args)


def active_page(context: BrowserContext) -> Page:
    if context.pages:
        return context.pages[0]
    return context.new_page()


def open_url(context: BrowserContext, url: str) -> Page:
    page = active_page(context)
    page.goto(url, wait_until="domcontentloaded")
    return page
