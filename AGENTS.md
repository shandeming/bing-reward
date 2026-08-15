# bing-rewardd — Agent Guide

## Entrypoint

- Package: `bing_rewardd`, registered as console script `bing-rewardd` → `bing_rewardd.cli:main`
- Source under `src/bing_rewardd/` (setuptools `find`, `where = ["src"]`), tests under `tests/`
- Python `>=3.10`, single runtime dep: `playwright>=1.44`; test extra: `pytest>=8`
- Pytest config in `pyproject.toml`: `testpaths = ["tests"]`, `pythonpath = ["src"]`

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m playwright install chromium
```

To save login state for reuse, run once logged in:
```powershell
bing-rewardd --save-storage-state storage_state.json tasks
```

For CI, encode `storage_state.json` as Base64 and set as the `BING_STORAGE_STATE_B64` repository secret.

## Commands & flags

| Command | Action |
|---------|--------|
| `bing-rewardd tasks` | Detect and complete all visible Rewards tasks |

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--slow-mo N` | int | `0` | Playwright slow-motion delay (ms) |
| `--no-wait` | bool | — | Deprecated, no-op (hidden from help) |
| `--screenshot-dir DIR` | Path | `None` | Save final browser screenshot |
| `--headless` | bool | `False` | Run browser in headless mode |
| `--storage-state FILE` | Path | `storage_state.json` (cwd) | Restore cookies/localStorage |
| `--save-storage-state FILE` | Path | `None` | Save cookies/localStorage after run |
| `--extra-args ...` | list | `[]` | Extra args for `browser.launch()` (e.g. `--no-sandbox`) |

`main()` returns exit code `0`. Flow: parse args → `launch_browser(config)` context manager → `open_url(context, BING_URL)` → dispatch to subcommand handler → on exit: save screenshot / storage state.

## Running tests

```powershell
python -m pytest
```

Tests are pure unit tests — no browser required. Uses monkeypatching and fake Playwright classes: `FakePage`, `FakeLocator`, `FakeLocatorList`, `FakeLocatorPage`, `FakeFramePage`, `FakeLocatorWithForm`. No test markers or fixtures.

| File | Tests | Covers |
|------|-------|--------|
| `tests/test_cli.py` | 5 | Parser flags, `default_storage_state_path`, `main()` dispatch |
| `tests/test_rewards.py` | 16 | Status inference, sidebar/icon detection, task listing, auto-login, `guide_tasks` error handling |
| `tests/test_config.py` | 6 | Credential loading (missing file, bad JSON, missing fields, valid) |

## Browser behavior

- Runs **visible** (not headless) by default; `--headless` overrides
- `launch_browser` is a context manager: creates `sync_playwright` session, launches browser, yields `BrowserContext`, closes on exit
- Viewport is fixed at **1280×900**
- Tries `channel="chrome"` first; falls back to bundled Chromium on error
- Browser closes immediately after command finishes — no persistent session between runs
- Injects two init scripts into every page:
  - **Anti-detect**: sets `navigator.webdriver = false`
  - **Bing auth fix**: modifies `localStorage['digital-id-cache']` — changes `at` from `'NA'` to `'MSA'` and sets `it` to `'Web'` when `muid` is present

## Authentication layers

1. **Storage state** (`--storage-state`): restores cookies and localStorage from a Playwright JSON file
2. **Init script hack**: the Bing auth fix script above handles cached `digital-id-cache` tokens
3. **Fallback auto-login**: if the sidebar shows a signup CTA, `guide_tasks` calls `load_credentials()` (reads `.credentials.json`), then `_try_auto_login()` drives the `login.live.com` flow (email → Next → password → Sign in → "Stay signed in?" → verify redirect). `.credentials.json` is git-ignored.

## Side-panel detection strategy

`rewards.py` uses a **dual-strategy** approach for finding the Rewards sidebar and its tasks:

### Sidebar detection
- Iterates `SIDEBAR_SELECTORS` (8 CSS selectors: `#rewid-f`, `#b_idPanel`, `#id_d`, `[role='dialog']`, etc.)
- Falls back to `_looks_like_rewards_sidebar()`: checks visibility, bounding box ≥ 180×120, right-side/overlay position, `id="rewid-f"`, iframe src containing `/rewards/panelflyout`, or text markers ("reward", "points", "earn", "streak", "daily")

### Rewards icon
- Iterates `REWARDS_ICON_SELECTORS` (12 CSS selectors)
- Falls back to `_click_rewards_icon_by_dom()`: scores all `a`, `button`, `[role="button"]` elements by ID match, position (upper-right), and tag type; clicks highest-scored element containing "reward"

### Task listing
- **Modern (React)**: `_find_cards_by_section()` uses `frame.locator("body").evaluate()` — queries `section` elements by heading, extracts `a[href]` cards with titles from `img.alt` / `p:first-child`, descriptions, and points
- **Legacy (CSS)**: falls back to selectors like `#daily_set_card .promo_cont` and generic `a[href]`/`button` if React approach finds nothing
- Task sections: `Daily set` and `Keep earning`

## Task completion flow (`guide_tasks`)

1. Opens Rewards sidebar via `open_rewards_sidebar()`
2. Performs a Bing search for "weather" (Search Streak)
3. Iterates "Daily set" then "Keep earning" sections
4. For each non-completed task:
   - Clicks the task
   - Checks for new page (popup): if opened, waits for load, sleeps randomly, closes tab, brings original page forward
   - If no new page, tries clicking a "Close" button in the sidebar
   - Random sleep `uniform(2.0, 4.0)` between tasks for human-like timing

## CI/CD

File: `.github/workflows/daily.yml`

- **Trigger**: `workflow_dispatch` (manual only)
- **Runner**: `windows-latest` with PowerShell
- **Timeout**: 20 minutes
- **Steps**: checkout → setup Python 3.12 → `pip install -e ".[test]"` → install chromium → `pytest` → decode `BING_STORAGE_STATE_B64` secret to `.auth/storage_state.json` → run `bing-rewardd --storage-state .auth/storage_state.json --screenshot-dir debug_screenshots tasks` → upload `debug_screenshots` as artifact (3-day retention)

## Playwright-based testing & layout awareness

Whenever a feature or fix touches page interaction, **always use Playwright to visually inspect the live page** — don't guess about DOM layout from code alone. Open the browser, load `cn.bing.com`, and verify:
- Selector paths actually match visible elements
- Lazy-loaded content renders within expected timeout windows
- Sidebar/flyout structure matches what the code assumes
- Any new page behavior (animations, overlays, JS transitions) is accounted for

The project has an `opencode.json` MCP config that launches `npx -y @playwright/mcp` for AI-driven browser interaction. VS Code `.vscode/launch.json` runs the CLI in the integrated terminal for debugging.

## Architecture notes

- `browser.py` — `BrowserConfig` dataclass, `launch_browser` context manager, Chrome/Chromium fallback, anti-detect + auth-fix init scripts, storage-state restore, `active_page` / `open_url` helpers
- `rewards.py` — All Rewards interaction: sidebar detection by DOM scoring, icon click, task listing (React + legacy), `guide_tasks` orchestration, `complete_section_tasks` with popup handling, `_try_auto_login` fallback, `NotLoggedInError` / `RewardsSidebarError` exceptions, `RewardTask` dataclass
- `config.py` — `load_credentials()` reads `.credentials.json`, returns `dict` or `None` defensively
- `cli.py` — `build_parser()` (argparse with subparsers), `main()` entry point, `_save_final_screenshot()`
- VS Code debug: `.vscode/launch.json` runs `python -m bing_rewardd.cli tasks` in the integrated terminal
- No lint/formatter/typecheck config; no pre-commit hooks
- Login state is never committed to the repository; treat `storage_state.json`, `BING_STORAGE_STATE_B64`, and `.credentials.json` as credentials
