# Bing Rewardd

A Python + Playwright CLI tool for opening Chrome, navigating to Bing and Microsoft Rewards, and completing visible Rewards tasks.

This tool intentionally does not farm rewards, generate fake searches, or bypass captchas. Search terms are extracted from task descriptions. Both local runs and GitHub Actions use a Playwright storage-state file to restore login cookies and localStorage.

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

Flags: `--storage-state FILE` (default `storage_state.json`), `--save-storage-state FILE`, `--slow-mo N`, `--no-wait` (deprecated, no-op), `--screenshot-dir DIR`

## Notes

- The browser runs visibly, not headless. This is required by the GitHub Actions workflow because Bing may return a reduced page in headless mode.
- No user input required — all actions are automatic.
- The app does not store a username or password. Local and CI login state is stored in a Playwright storage-state file; CI receives it through `BING_STORAGE_STATE_B64`.

## GitHub Actions login state

The workflow restores a Playwright storage-state file instead of uploading the full browser profile. After logging in locally, export the state with:

```powershell
bing-rewardd --save-storage-state storage_state.json tasks
```

Encode that file and save the result as the repository secret `BING_STORAGE_STATE_B64`.

On Linux or WSL, use:

```bash
base64 -w 0 storage_state.json | gh secret set BING_STORAGE_STATE_B64
```

If GitHub CLI is not installed, generate the value without printing it to a shared log:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("storage_state.json"))
```

Then create a repository Secret named `BING_STORAGE_STATE_B64` under `Settings → Secrets and variables → Actions` and paste the generated value.

The storage state contains login cookies and localStorage, so treat it like a password. Do not commit it or upload it as an artifact. Microsoft may expire the session, in which case export a fresh state and update the Secret. GitHub Actions can be tested manually from `Actions → Daily → Run workflow`.
