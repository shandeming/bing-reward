# Bing Rewardd

A compliant Python + Playwright CLI assistant for opening Chrome, navigating to Bing and Microsoft Rewards, and guiding user-confirmed Rewards actions.

This tool intentionally does not farm rewards, generate fake searches, bypass captchas, or click through Rewards tasks unattended. Search submissions require user-provided terms and explicit confirmation.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m playwright install chromium
```

## Commands

```powershell
bing-rewardd start
bing-rewardd rewards
bing-rewardd tasks
bing-rewardd search
```

By default, browser session data is stored in `.browser-profile/` so your Microsoft login can persist without using your normal Chrome profile.

## Notes

- The browser runs visibly, not headless.
- Credentials are not stored by the app.
- Rewards task cards are opened only after confirmation.
- Search terms must be entered by you and confirmed one at a time.
