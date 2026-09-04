# Bing Rewardd

A Python + Playwright CLI tool for opening Chrome, navigating to Bing and Microsoft Rewards, completing visible Rewards tasks, and claiming an available bonus-points card.

This tool intentionally does not farm rewards, generate fake searches, or bypass captchas. It opens the Rewards sidebar and completes visible tasks. The Search Streak task is handled by performing a single "weather" search. Both local runs and GitHub Actions use a Playwright storage-state file to restore login cookies and localStorage.

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

Flags: `--storage-state FILE` (default `storage_state.json`), `--save-storage-state FILE`, `--slow-mo N`, `--no-wait` (deprecated, no-op), `--screenshot-dir DIR`, `--headless`, `--extra-args ...`

## Notes

- The browser runs visibly, not headless. Use `--headless` to override (may reduce task reliability on Bing).
- No user input required — task actions and a visible bonus-points claim are automatic.
- After task completion, the tool refreshes the Rewards flyout, opens the bonus card's Rewards dashboard link, and clicks the final `Claim points` control. It skips the claim step when no bonus card is available.
- Login state is stored in a Playwright storage-state file; CI receives it through `BING_STORAGE_STATE_B64`.
- Optional auto-login fallback: if the sidebar shows a sign-in prompt, the tool can read `.credentials.json` (git-ignored) to drive the Microsoft login flow. Credentials are never stored in the storage-state file or committed.

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
