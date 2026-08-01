from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext, Error, Page, Playwright, sync_playwright

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
    profile_dir: Path
    headless: bool = False
    slow_mo_ms: int = 0
    extra_args: list[str] = field(default_factory=list)
    storage_state: Path | None = None


def default_profile_dir(base_dir: Path | None = None) -> Path:
    root = base_dir or Path.cwd()
    return root / ".browser-profile"


@contextmanager
def launch_persistent_browser(config: BrowserConfig) -> Iterator[BrowserContext]:
    config.profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = _launch_with_preferred_channel(playwright, config)
        try:
            if config.storage_state:
                _apply_storage_state(context, config.storage_state)
            yield context
        finally:
            context.close()


def _apply_storage_state(context: BrowserContext, path: Path) -> None:
    """Restore cookies and localStorage without copying a whole browser profile."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read storage state: {path}") from exc

    if not isinstance(state, dict):
        raise ValueError("Storage state must be a JSON object")

    cookies = state.get("cookies", [])
    if not isinstance(cookies, list):
        raise ValueError("Storage state cookies must be a list")
    if cookies:
        context.add_cookies(cookies)

    # launch_persistent_context does not accept Playwright's storage_state
    # option, so install localStorage before the first navigation instead.
    local_storage_by_origin: dict[str, dict[str, str]] = {}
    for origin in state.get("origins", []):
        if not isinstance(origin, dict) or not isinstance(origin.get("origin"), str):
            continue
        entries = origin.get("localStorage", [])
        if not isinstance(entries, list):
            continue
        local_storage_by_origin[origin["origin"]] = {
            item["name"]: item["value"]
            for item in entries
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("value"), str)
        }

    if local_storage_by_origin:
        context.add_init_script(
            "const storage = "
            + json.dumps(local_storage_by_origin)
            + "; const values = storage[location.origin];"
            + " if (values) Object.entries(values).forEach(([key, value]) => "
            + "localStorage.setItem(key, value));"
        )


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
