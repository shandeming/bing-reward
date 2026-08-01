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

Flags: `--profile-dir DIR` (default `.browser-profile`), `--storage-state FILE`, `--save-storage-state FILE`, `--slow-mo N`, `--no-wait` (deprecated, no-op), `--screenshot-dir DIR`

## Notes

- The browser runs visibly, not headless.
- No user input required — all actions are automatic.
- Credentials are not stored by the app. Use the persistent browser profile to log in once.

## GitHub Actions login state

The workflow restores a Playwright storage-state file instead of uploading the full browser profile. After logging in locally, export the state with:

```powershell
bing-rewardd --save-storage-state storage_state.json tasks
```

Encode that file and save the result as the repository secret `BING_STORAGE_STATE_B64`:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("storage_state.json")) | gh secret set BING_STORAGE_STATE_B64
```

The storage state contains login cookies and localStorage, so treat it like a password. Do not commit it or upload it as an artifact. Microsoft may expire the session, in which case export a fresh state and update the secret.
