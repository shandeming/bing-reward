from __future__ import annotations

from dataclasses import dataclass
import random
from time import sleep
from typing import Any
from urllib.parse import quote_plus

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from bing_rewardd.browser import BING_URL
from bing_rewardd.config import load_credentials


@dataclass(frozen=True)
class RewardTask:
    index: int
    title: str
    status: str
    selector: Locator


class RewardsSidebarError(RuntimeError):
    """Raised when the Bing Rewards sidebar cannot be opened or found."""


class NotLoggedInError(RuntimeError):
    """Raised when the user is not signed in to Microsoft Rewards."""


_SIGNUP_MARKERS = (
    "get started",
    "sign in to earn",
    "sign in to redeem",
    "earn points just for using bing",
    "redeem them for gift cards",
)


TASKS_TYPES = [
    "DAILY_SET_TASK_SELECTOR",
    "EXPLORE_TASK_SELECTOR",
    "DAILY_HALF_UNIT_TASK_SELECTOR",
]

HALF_UNIT_TASK_TEXTS = (
    "Quote of the day",
    "Take today's news quiz",
    "Complete this puzzle",
    "Do you know the answer?",
    "Mid-week puzzle",
    "Try Visual Search",
)

DAILY_HALF_UNIT_TASK_SELECTOR = [
    "a:has-text('Quote of the day')",
    "a:has-text('Take today\\'s news quiz')",
    "a:has-text('Complete this puzzle')",
    "a:has-text('Do you know the answer?')",
    "a:has-text('Mid-week puzzle')",
    "a:has-text('Try Visual Search')",
]

REWARDS_ICON_SELECTORS = (
    "#id_rh_w",
    "div.medal",
    "[aria-label='Microsoft Rewards']",
    "#rh_rwm",
    "a[aria-label*='Rewards']",
    "button[aria-label*='Rewards']",
    "a[title*='Rewards']",
    "button[title*='Rewards']",
    "a:has-text('Rewards')",
    "button:has-text('Rewards')",
    "[role='button']:has-text('Rewards')",
)

SIDEBAR_SELECTORS = (
    "#rewid-f",
    "#b_idPanel",
    "#id_d",
    "[role='dialog']",
    "[role='complementary']",
    "[class*='reward']",
    "[id*='reward']",
)

SEARCH_INPUT_SELECTOR = ["textarea[name='q']", "#sb_form_q"]

REWARDS_FLYOUT_FRAME_SELECTOR = "#rewid-f iframe"

DAILY_SET_TASK_SELECTOR = ["#daily_set_card .promo_cont"]

EXPLORE_TASK_SELECTOR = [
    "#exb-activityChecklist .promo_cont[data-is-inprogress-enabled='yes']"
]

TASK_SELECTORS = (
    "a[href]",
    "button",
    "[role='button']",
    "[tabindex]:not([tabindex='-1'])",
)


def wait_for_possible_login(page: Page) -> None:
    if "login" in page.url.lower() or "signin" in page.url.lower():
        page.wait_for_load_state("domcontentloaded")


def open_rewards_sidebar(page: Page) -> Locator:
    page.goto(BING_URL, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass
    wait_for_possible_login(page)

    if find_rewards_sidebar(page) is None:
        click_rewards_icon(page, raise_on_missing=True)

    try:
        page.wait_for_timeout(5000)
    except PlaywrightTimeoutError:
        pass

    sidebar = find_rewards_sidebar(page)
    if sidebar is None:
        raise RewardsSidebarError(
            "Could not find the Bing Rewards sidebar after clicking the upper-right Rewards icon."
        )
    if not _is_signed_in_to_rewards(page):
        raise NotLoggedInError(
            "Not signed in to Microsoft Rewards. Open Bing in the browser, sign in, "
            "then re-run bing-rewardd tasks."
        )
    return sidebar


def click_rewards_icon(page: Page, *, raise_on_missing: bool = True) -> bool:
    for selector in REWARDS_ICON_SELECTORS:
        locator = page.locator(selector).first
        if not _is_visible(locator):
            continue
        locator.click(timeout=5000)
        return True

    if _click_rewards_icon_by_dom(page):
        return True

    if raise_on_missing:
        raise RewardsSidebarError(
            "Could not find the upper-right Microsoft Rewards icon on Bing."
        )
    return False


def find_rewards_sidebar(page: Page) -> Locator | None:
    for selector in SIDEBAR_SELECTORS:
        locator = page.locator(selector)
        try:
            count = min(locator.count(), 10)
        except PlaywrightTimeoutError:
            continue

        for index in range(count):
            candidate = locator.nth(index)
            if _looks_like_rewards_sidebar(page, candidate):
                return candidate

    return None


def _is_signed_in_to_rewards(page: Page) -> bool:
    """Check whether the Rewards sidebar iframe shows tasks, not a sign-up CTA."""
    sidebar = find_rewards_sidebar(page)
    if sidebar is None:
        return False
    task_scope = _task_locator_scope(page, sidebar)
    try:
        text = task_scope.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        return True  # can't read content; assume signed in
    return not any(marker in text for marker in _SIGNUP_MARKERS)


def list_visible_tasks(
    page: Page,
    selector_list: list[str] | tuple[str, ...] = TASK_SELECTORS,
    sidebar: Locator | None = None,
    limit: int = 30,
) -> list[RewardTask]:
    task_scope = sidebar or find_rewards_sidebar(page)
    if task_scope is None:
        return []
    locator_scope = _task_locator_scope(page, task_scope)

    tasks: list[RewardTask] = []
    seen_titles: set[str] = set()

    for selector in selector_list:
        locator = locator_scope.locator(selector)
        try:
            count = min(locator.count(), limit)
        except PlaywrightTimeoutError:
            continue

        for i in range(count):
            item = locator.nth(i)
            if not _is_visible(item):
                continue

            title = _clean_text(item)
            if not title or title in seen_titles or not _is_task_like(title):
                continue

            seen_titles.add(title)
            tasks.append(
                RewardTask(
                    index=len(tasks) + 1,
                    title=title,
                    status=_infer_status(title),
                    selector=item,
                )
            )

    return tasks


def _try_auto_login(page: Page, email: str, password: str) -> bool:
    """Attempt to sign in to Microsoft via the login.live.com page.

    Directly navigates to login.live.com/login.srf, fills email/password,
    and verifies redirect back to Bing. Returns True if login succeeded, False otherwise.
    """
    try:
        page.goto(
            "https://login.live.com/login.srf?wa=wsignin1.0&wreply=https://cn.bing.com/",
            wait_until="domcontentloaded",
            timeout=15000,
        )
    except PlaywrightTimeoutError:
        return False

    print(f"  [login] At {page.url}, filling credentials...")

    # Find email input
    email_input = _find_email_input(page)
    if not email_input or not _is_visible(email_input):
        for frame in page.frames:
            email_input = _find_email_input(frame)
            if email_input and _is_visible(email_input):
                break
        else:
            email_input = None

    if not email_input:
        print(f"  [login] No email input found at {page.url}")
        return False

    email_input.fill(email, timeout=5000)
    print(f"  [login] Filled email, clicking Next...")

    # Click Next/Submit - try multiple selectors in order
    submit_clicked = False
    submit_selectors = [
        "button:has-text('Next')",
        "input[id='idSIButton9']",
        "input[id='id_btnNext']",
        "button[type='submit']",
        "input[type='submit']",
    ]
    for sel in submit_selectors:
        try:
            btn = page.locator(sel).first
            if _is_visible(btn):
                btn.click(timeout=5000)
                print(f"  [login] Clicked submit button via '{sel}'")
                submit_clicked = True
                break
        except PlaywrightTimeoutError:
            continue

    if not submit_clicked:
        # Fallback: try pressing Enter to submit
        print(f"  [login] Falling back to Enter key submit...")
        try:
            page.keyboard.press("Enter")
            submit_clicked = True
        except Exception:
            # Last resort: use JavaScript to submit
            print(f"  [login] Falling back to JS form submit...")
            try:
                page.evaluate("() => { document.querySelector('form')?.submit(); }")
                submit_clicked = True
            except Exception:
                print(f"  [login] JS submit fallback failed")
                return False

    # Wait for password input to appear (give extra time for page transition)
    try:
        page.wait_for_selector('input[type="password"]', timeout=15000)
    except PlaywrightTimeoutError:
        print(f"  [login] Password input did not appear after email submission")
        # Debug: show what buttons/inputs are on the page
        try:
            btns = page.locator("button, input[type='submit']").count()
            print(f"  [login] Debug: {btns} button/submit inputs on page")
            # Show page text to identify the issue
            body = page.locator("body").inner_text(timeout=5000)
            print(f"  [login] Debug: page text: {body[:200]}")
        except Exception:
            pass
        return False

    # Find password input
    pw_input = _find_password_input(page)
    if not pw_input or not _is_visible(pw_input):
        for frame in page.frames:
            pw_input = _find_password_input(frame)
            if pw_input and _is_visible(pw_input):
                break
        else:
            pw_input = None

    if not pw_input:
        print(f"  [login] No password input found at {page.url}")
        return False

    pw_input.fill(password, timeout=5000)
    print(f"  [login] Filled password, submitting...")

    # Click Sign in / Submit - try multiple selectors
    submit_clicked = False
    sign_in_selectors = [
        "button:has-text('Sign in')",
        "button:has-text('Next')",
        "input[id='idSIButton9']",
        "button[type='submit']",
        "input[type='submit']",
    ]
    for sel in sign_in_selectors:
        try:
            btn = page.locator(sel).first
            if _is_visible(btn):
                btn.click(timeout=5000)
                print(f"  [login] Clicked sign-in button via '{sel}'")
                submit_clicked = True
                break
        except PlaywrightTimeoutError:
            continue

    if not submit_clicked:
        # Fallback: try pressing Enter to submit
        print(f"  [login] Falling back to Enter key submit...")
        try:
            page.keyboard.press("Enter")
            submit_clicked = True
        except Exception:
            # Last resort: use JavaScript to submit
            print(f"  [login] Falling back to JS form submit...")
            try:
                page.evaluate("() => { document.querySelector('form')?.submit(); }")
                submit_clicked = True
            except Exception:
                print(f"  [login] JS submit fallback failed")
                return False

    # Wait for "Stay signed in?" prompt or redirect
    page.wait_for_timeout(3000)

    # Check if we're on "Stay signed in?" page
    yes_btn = page.locator("button:has-text('Yes')").first
    if _is_visible(yes_btn):
        print(f"  [login] Clicking 'Yes' on 'Stay signed in?' prompt...")
        yes_btn.click(timeout=5000)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except PlaywrightTimeoutError:
            pass

    # Verify: should be back on Bing (not on login.live.com)
    url = page.url.lower()
    if "login.live.com" in url:
        print(f"  [login] Still on login.live.com: {url}")
        return False

    print(f"  [login] Success, redirected to {url}")
    return True


def _find_email_input(locator: Page) -> Locator | None:
    """Find an email/text input on the page or frame."""
    candidates = [
        locator.locator('input[type="email"], input[name="loginfmt"]'),
        locator.locator('input[type="text"]'),
    ]
    for c in candidates:
        try:
            if c.count() > 0 and _is_visible(c.first):
                return c.first
        except Exception:
            continue
    return None


def _find_password_input(locator: Page) -> Locator | None:
    pw = locator.locator('input[type="password"]')
    try:
        if pw.count() > 0 and _is_visible(pw.first):
            return pw.first
    except Exception:
        pass
    return None


def _click_submit(locator: Page) -> None:
    submit_candidates = [
        "input[type='submit']",
        "button[type='submit']",
        "input[id='idSIButton9']",
        "input[id='id_btnNext']",
        "button:has-text('Next')",
        "button:has-text('Sign in')",
    ]
    for selector in submit_candidates:
        loc = locator.locator(selector)
        if _is_visible(loc.first):
            loc.first.click(timeout=5000)
            return


def guide_tasks(page: Page) -> None:
    try:
        sidebar = open_rewards_sidebar(page)
    except NotLoggedInError:
        print("[!] Not signed in. Attempting auto-login...")
        creds = load_credentials()
        if not creds:
            print(
                "[!] No credentials found. Create .credentials.json with "
                "email/password, or sign in manually, then re-run."
            )
            return
        if _try_auto_login(page, creds["email"], creds["password"]):
            print("[✓] Logged in. Retrying tasks...")
            guide_tasks(page)
            return
        print("[!] Auto-login failed. Please sign in manually, then re-run.")
        return
    except RewardsSidebarError as exc:
        print(f"[!] {exc}")
        return

    found_any = False
    for task_type in TASKS_TYPES:
        selector_list = globals().get(task_type)
        if not selector_list:
            continue
        tasks = list_visible_tasks(page, selector_list, sidebar=sidebar)
        if not tasks:
            continue
        found_any = True

        print(f"Detected {len(tasks)} {task_type.replace('_', ' ').title()}s:")
        for task in tasks:
            print(f"{task.index}. {task.title} [{task.status}]")

        if task_type == "EXPLORE_TASK_SELECTOR":
            complete_explore_tasks(page, tasks)
        elif task_type == "DAILY_SET_TASK_SELECTOR":
            complete_daily_set(page, tasks)
        elif task_type == "DAILY_HALF_UNIT_TASK_SELECTOR":
            complete_half_unit_tasks(page, tasks)

    if not found_any:
        print("No Rewards tasks found in the sidebar.")


def complete_explore_tasks(page: Page, tasks: list[RewardTask]) -> None:
    # activate each task, then do searches for each task
    for task in tasks:
        selector = task.selector
        if not _is_visible(selector):
            continue
        task.selector.click(timeout=5000)
        sleep(random.uniform(2.0, 4.0))
    search_for_term(page, "weather")
    page.click("#est_en")
    for task in tasks:
        search_term = task.title.split("|")[-1].strip().split("Search on Bing")[-1]
        search_for_term(page, search_term)
        sleep(random.uniform(2.0, 4.0))


def complete_daily_set(page: Page, tasks: list[RewardTask]) -> None:
    count = len(tasks)
    for i in range(count):
        sidebar = open_rewards_sidebar(page)
        tasks = list_visible_tasks(page, DAILY_SET_TASK_SELECTOR, sidebar=sidebar)
        card = tasks[i].selector if i < len(tasks) else None
        if not card or not _is_visible(card):
            continue
        label = card.get_attribute("aria-label") or ""
        status = label.split(" - Offer ")[-1] if " - Offer " in label else ""
        if "is" in status.lower():
            continue
        card.click()
        sleep(random.uniform(2.0, 4.0))


def complete_half_unit_tasks(page: Page, tasks: list[RewardTask]) -> None:
    """Click each half-unit task link to open and mark it as done."""
    for task in tasks:
        selector = task.selector
        if not _is_visible(selector):
            continue
        if task.status == "complete":
            print(f"  [half-unit] Skipping completed task '{task.title}'")
            continue
        print(f"  [half-unit] Clicking '{task.title}'...")
        selector.click(timeout=5000)
        sleep(random.uniform(1.0, 2.0))
        # Half-unit tasks often open a new page/tab instead of an overlay.
        # Check if a new page appeared; if so, briefly visit it then close it.
        # (The actual completion tracking is done server-side on click.)
        try:
            page.wait_for_timeout(2000)
            # Check if a new page was opened
            context = page.context
            new_pages = [p for p in context.pages if p != page and not p.is_closed()]
            if new_pages:
                new_page = new_pages[0]
                print(f"  [half-unit] New page opened: {new_page.url}")
                # Give the page a moment to render
                try:
                    new_page.wait_for_load_state("domcontentloaded", timeout=3000)
                except PlaywrightTimeoutError:
                    pass
                sleep(random.uniform(2.0, 4.0))
                # Close the new page and return to original
                new_page.close()
                page.bring_to_front()
                sleep(random.uniform(1.0, 2.0))
            else:
                # No new page — try closing any overlay on the current page
                try:
                    page.locator("button:has-text('Close'), a:has-text('Close')").first.click(timeout=2000)
                except PlaywrightTimeoutError:
                    pass
                sleep(random.uniform(1.0, 2.0))
        except PlaywrightTimeoutError:
            pass





def search_for_term(page: Page, term: str) -> None:
    for selector in SEARCH_INPUT_SELECTOR:
        locator = page.locator(selector).first
        if _is_visible(locator):
            locator.fill(term, timeout=5000)
            locator.press("Enter")
            return


def _is_visible(locator: Locator) -> bool:
    try:
        return locator.is_visible(timeout=500)
    except PlaywrightTimeoutError:
        return False


def _clean_text(locator: Locator) -> str:
    try:
        text = locator.inner_text(timeout=500)
    except PlaywrightTimeoutError:
        return ""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines)[:180]


def _click_rewards_icon_by_dom(page: Page) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                () => {
                  const candidates = Array.from(document.querySelectorAll(
                    'a,button,[role="button"],[aria-label],[title],[id],[class]'
                  ));
                  const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                  };
                  const score = (el) => {
                    const rect = el.getBoundingClientRect();
                    const haystack = [
                      el.id || '',
                      el.className ? String(el.className) : '',
                      el.getAttribute('aria-label') || '',
                      el.getAttribute('title') || '',
                      el.innerText || el.textContent || ''
                    ].join(' ').toLowerCase();
                    if (!haystack.includes('reward')) return -1;
                    let value = 1;
                    if (el.id === 'id_rh_w') value += 10;
                    if (el.id === 'rh_rwm') value += 8;
                    if (rect.x > window.innerWidth * 0.55 && rect.y < 160) value += 4;
                    if (['A', 'BUTTON'].includes(el.tagName) || el.getAttribute('role') === 'button') value += 2;
                    return value;
                  };
                  const target = candidates
                    .filter(visible)
                    .map((el) => ({ el, score: score(el) }))
                    .filter((item) => item.score > 0)
                    .sort((a, b) => b.score - a.score)[0]?.el;
                  if (!target) return false;
                  const clickable = target.closest('a,button,[role="button"]') || target;
                  clickable.click();
                  return true;
                }
                """
            )
        )
    except Exception:
        return False


def _looks_like_rewards_sidebar(page: Page, locator: Locator) -> bool:
    if not _is_visible(locator):
        return False

    try:
        box = locator.bounding_box(timeout=500)
    except PlaywrightTimeoutError:
        return False

    if not box or box["width"] < 180 or box["height"] < 120:
        return False

    viewport = page.viewport_size or {"width": 1280, "height": 900}
    is_right_side = box["x"] >= viewport["width"] * 0.45
    is_overlay = box["width"] <= viewport["width"] * 0.75 and box["height"] >= 180
    if not (is_right_side or is_overlay):
        return False

    if _get_attribute(locator, "id") == "rewid-f":
        return True

    iframe_src = _first_attribute(locator.locator("iframe"), "src")
    if iframe_src and "/rewards/panelflyout" in iframe_src:
        return True

    text = _clean_text(locator).lower()
    return any(
        marker in text for marker in ("reward", "points", "earn", "streak", "daily")
    )


def _infer_status(text: str) -> str:
    lowered = text.lower()
    # Only treat "complete" as done when it appears as a status indicator,
    # not as part of a task name like "Complete this puzzle".
    # Check for patterns like "is complete", "marked complete", "completed"
    # or when "complete" appears at the end of the text.
    status_complete = any(
        marker in lowered
        for marker in (
            "is complete",
            "marked complete",
            "is completed",
            "completed",
        )
    ) or lowered.endswith("complete")
    if status_complete:
        return "complete"
    if "points" in lowered or "pts" in lowered or "+" in lowered:
        return "available"
    return "visible"


def _task_locator_scope(page: Page, sidebar: Locator) -> Any:
    try:
        if sidebar.locator("iframe").count() > 0:
            return page.frame_locator(REWARDS_FLYOUT_FRAME_SELECTOR)
    except Exception:
        pass
    return sidebar


def _get_attribute(locator: Locator, name: str) -> str | None:
    try:
        return locator.get_attribute(name, timeout=500)
    except Exception:
        return None


def _first_attribute(locator: Locator, name: str) -> str | None:
    try:
        if locator.count() == 0:
            return None
        return locator.first.get_attribute(name, timeout=500)
    except Exception:
        return None


def _is_task_like(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    if normalized in {"close", "redeem", "view dashboard", "silver member"}:
        return False
    if len(normalized) < 3:
        return False
    ignored_prefixes = (
        "my rewards points",
        "points to gold",
    )
    return not any(normalized.startswith(prefix) for prefix in ignored_prefixes)
