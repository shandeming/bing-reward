# bing-rewardd — Agent Guide

## Entrypoint

- Package: `bing_rewardd`, registered as console script `bing-rewardd` → `bing_rewardd.cli:main`
- Source under `src/bing_rewardd/` (setuptools `find`), tests under `tests/`

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m playwright install chromium
```

## Commands & flags

| Command | Action |
|---------|--------|
| `bing-rewardd tasks` | Detect and complete all visible Rewards tasks |

Flags: `--profile-dir` (default `.browser-profile`), `--slow-mo N`, `--no-wait` (deprecated, no-op), `--screenshot-dir DIR`

## Running tests

```powershell
python -m pytest
```

Tests are pure unit tests — no browser required. Uses monkeypatching and fake Playwright classes (`FakePage`, `FakeLocator`). No test markers or fixtures.

## Browser behavior

- Runs **visible** (not headless), persistent Chrome profile at `.browser-profile/`
- Tries `channel="chrome"` first; falls back to Chromium if Chrome not found
- Browser closes immediately after command finishes (no user input required)
- CI (`.github/workflows/daily.yml`): self-hosted Windows runner, daily at 09:00 Asia/Shanghai (UTC 01:00), 30-min timeout

## Playwright-based testing & layout awareness

Whenever a feature or fix touches page interaction, **always use Playwright to visually inspect the live page** — don't guess about DOM layout from code alone. Open the browser, load `cn.bing.com` with the same query params the code uses, and verify:
- Selector paths actually match visible elements
- Lazy-loaded content renders within expected timeout windows
- Sidebar/flyout structure matches what the code assumes
- Any new page behavior (animations, overlays, JS transitions) is accounted for

This catches drift between code assumptions and real-world page state before it breaks at runtime.

## Architecture notes

- `browser.py` — Playwright context management, Chrome/Chromium fallback
- `rewards.py` — All Rewards-sidebar interaction (sidebar detection by DOM scoring, task listing, click-through, search submission)
- `cli.py` — Argparse-based CLI glue
- No lint/formatter/typecheck config exists; no pre-commit hooks
- All credentials handled via live browser login (not stored by code)