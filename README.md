# Bing Rewardd

A fully automatic Python + Playwright CLI tool for opening Chrome, navigating to Bing and Microsoft Rewards, and completing visible Rewards tasks.

This tool intentionally does not farm rewards, generate fake searches, bypass captchas, or store credentials. Search terms are extracted from task descriptions; the browser runs visibly with a persistent profile at `.browser-profile/` so your Microsoft login persists across sessions.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m playwright install chromium
```

## Commands

```powershell
bing-rewardd tasks      # Detect and complete all visible Rewards tasks
```

Flags: `--profile-dir DIR` (default `.browser-profile`), `--slow-mo N`, `--no-wait` (deprecated, no-op), `--screenshot-dir DIR`

## Notes

- The browser runs visibly, not headless.
- No user input required — all actions are automatic.
- Credentials are not stored by the app. Use the persistent browser profile to log in once.