from __future__ import annotations

import re
import random
from dataclasses import dataclass
from time import sleep
from typing import Any

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

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
    "sign in to earn",
    "sign in to redeem",
    "earn points just for using bing",
    "redeem them for gift cards",
    "get started with microsoft rewards",
)

_SIGNED_IN_MARKERS = (
    "daily set",
    "keep earning",
    "streak",
    "points left",
    "my rewards",
)

DAILY_SET_SECTION_HEADING = "Daily set"
KEEP_EARNING_SECTION_HEADING = "Keep earning"

# --- Legacy CSS selectors (pre-2026 React sidebar) ---
DAILY_SET_TASK_SELECTOR = ["#daily_set_card .promo_cont"]
EXPLORE_TASK_SELECTOR = [
    "#exb-activityChecklist .promo_cont[data-is-inprogress-enabled='yes']"
]
DAILY_HALF_UNIT_TASK_SELECTOR = ["div.promo_cont"]
LEGACY_TASK_SELECTORS = (
    "a[href]",
    "button",
    "[role='button']",
    "[tabindex]:not([tabindex='-1'])",
)
LEGACY_TASK_TYPES = ("DAILY_SET_TASK_SELECTOR", "EXPLORE_TASK_SELECTOR", "DAILY_HALF_UNIT_TASK_SELECTOR")


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

    # Wait for iframe to finish loading content
    try:
        frame_locator = page.frame_locator(REWARDS_FLYOUT_FRAME_SELECTOR)
        frame = frame_locator.frame(timeout=5000)
        if frame is not None:
            frame.wait_for_load_state("domcontentloaded", timeout=10000)
    except (PlaywrightTimeoutError, AttributeError):
        pass

    task_scope = _task_locator_scope(page, sidebar)
    # Poll up to 3 times with brief waits — iframe content may load asynchronously
    for _ in range(3):
        try:
            text = task_scope.locator("body").inner_text(timeout=5000).lower()
        except Exception:
            return True  # can't read content; assume signed in
        if text:
            # Positive signal: if the sidebar shows task-related headings, user is signed in
            if any(marker in text for marker in _SIGNED_IN_MARKERS):
                return True
            # Negative signal: if sign-up CTA text is present, user is NOT signed in
            if any(marker in text for marker in _SIGNUP_MARKERS):
                return False
            return True
        sleep(1.5)
    # After all retries, assume signed in (better than false-negative)
    return True


def list_visible_tasks(
    page: Page,
    section_heading: str | None = None,
    sidebar: Locator | None = None,
    selector_list: tuple[str, ...] | None = None,
    limit: int = 30,
) -> list[RewardTask]:
    task_scope = sidebar or find_rewards_sidebar(page)
    if task_scope is None:
        return []

    if selector_list:
        return _find_tasks_by_selectors(page, task_scope, selector_list, limit)

    if section_heading:
        frame = _get_rewards_frame(page, task_scope)
        if frame is not None:
            tasks = _find_cards_by_section(frame, section_heading)
            if tasks:
                return tasks
        return _find_tasks_by_selectors(page, task_scope, LEGACY_TASK_SELECTORS, limit)

    # Default: try new React section-based approach first, fall back to legacy selectors
    frame = _get_rewards_frame(page, task_scope)
    if frame is not None:
        tasks: list[RewardTask] = []
        for heading in (DAILY_SET_SECTION_HEADING, KEEP_EARNING_SECTION_HEADING):
            tasks.extend(_find_cards_by_section(frame, heading))
        if tasks:
            return tasks

    return _find_tasks_by_selectors(page, task_scope, LEGACY_TASK_SELECTORS, limit)


def _find_cards_by_section(frame: Any, section_heading: str) -> list[RewardTask]:
    result = frame.locator("body").evaluate(
        """(el, heading) => {
            const sections = el.querySelectorAll('section');
            for (const section of sections) {
                if (!section.innerText.includes(heading)) continue;
                const cards = section.querySelectorAll('a[href]');
                return Array.from(cards).map((a, idx) => {
                    const imgs = a.querySelectorAll('img');
                    const titleEl = imgs[0] || a.querySelector('p:first-child');
                    const title = (titleEl ? (titleEl.alt || titleEl.innerText) : a.innerText).trim();
                    const descEl = a.querySelectorAll('p')[1];
                    const desc = descEl ? descEl.innerText.trim() : '';
                    const ptsEls = a.querySelectorAll('.text-globalBody2Strong, .text-metadata');
                    let points = '';
                    for (const p of ptsEls) {
                        const t = p.innerText.trim();
                        if (/^[+]?\\d+/.test(t)) { points = t; break; }
                    }
                    const completed = a.innerText.includes('Completed');
                    return { idx, title, desc, points, completed };
                }).filter(c => c.title.length > 2 && c.title !== 'Show more');
            }
            return [];
        }""",
        section_heading,
        timeout=5000,
    )

    # Build the section + card locator once, share across tasks
    section_locator = frame.locator(f"section:has-text('{section_heading}')")
    card_locator = section_locator.locator("a[href]")

    tasks: list[RewardTask] = []
    for card in result:
        title = f"{card['title']} | {card['desc']}" if card.get("desc") else card["title"]
        status = "complete" if card.get("completed") else ("available" if card.get("points") else "visible")
        card_idx = card["idx"]
        tasks.append(
            RewardTask(
                index=len(tasks) + 1,
                title=title,
                status=status,
                selector=card_locator.nth(card_idx),
            )
        )

    return tasks


def _find_tasks_by_selectors(
    page: Page,
    task_scope: Locator,
    selector_list: tuple[str, ...],
    limit: int,
) -> list[RewardTask]:
    locator_scope = _task_locator_scope(page, task_scope)

    tasks: list[RewardTask] = []
    seen: set[str] = set()

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

            try:
                elem_key = item.evaluate("el => el.outerHTML") if hasattr(item, "evaluate") else id(item)
            except Exception:
                elem_key = str(i)

            if elem_key in seen:
                continue
            seen.add(elem_key)

            title = _clean_text(item)
            if not title or not _is_task_like(title):
                continue

            tasks.append(
                RewardTask(
                    index=len(tasks) + 1,
                    title=title,
                    status=_infer_status(title),
                    selector=item,
                )
            )

    return tasks


def _get_rewards_frame(page: Page, sidebar: Locator) -> Any:
    try:
        if sidebar.locator("iframe").count() > 0:
            return page.frame_locator(REWARDS_FLYOUT_FRAME_SELECTOR)
    except Exception:
        pass
    return None


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
    print("  [login] Filled email, clicking Next...")

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
        print("  [login] Falling back to Enter key submit...")
        try:
            page.keyboard.press("Enter")
            submit_clicked = True
        except Exception:
            # Last resort: use JavaScript to submit
            print("  [login] Falling back to JS form submit...")
            try:
                page.evaluate("() => { document.querySelector('form')?.submit(); }")
                submit_clicked = True
            except Exception:
                print("  [login] JS submit fallback failed")
                return False

    # Wait for password input to appear (give extra time for page transition)
    try:
        page.wait_for_selector('input[type="password"]', timeout=15000)
    except PlaywrightTimeoutError:
        print("  [login] Password input did not appear after email submission")
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
    print("  [login] Filled password, submitting...")

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
        print("  [login] Falling back to Enter key submit...")
        try:
            page.keyboard.press("Enter")
            submit_clicked = True
        except Exception:
            # Last resort: use JavaScript to submit
            print("  [login] Falling back to JS form submit...")
            try:
                page.evaluate("() => { document.querySelector('form')?.submit(); }")
                submit_clicked = True
            except Exception:
                print("  [login] JS submit fallback failed")
                return False

    # Wait for "Stay signed in?" prompt or redirect
    page.wait_for_timeout(3000)

    # Check if we're on "Stay signed in?" page
    yes_btn = page.locator("button:has-text('Yes')").first
    if _is_visible(yes_btn):
        print("  [login] Clicking 'Yes' on 'Stay signed in?' prompt...")
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


def get_points(page: Page, sidebar: Locator | None = None) -> str | None:
    """Read the current Rewards points total from the sidebar."""
    task_scope = sidebar or find_rewards_sidebar(page)
    if task_scope is None:
        return None
    frame = _get_rewards_frame(page, task_scope)
    if frame is not None:
        try:
            result = frame.locator("body").evaluate(
                r"""() => {
                  const body = document.body.innerText;
                  const matches = [...body.matchAll(/\d[\d,]*\s*(?:points?|pts?)\b/gi)];
                  for (const m of matches) {
                    const num = parseInt(m[0].replace(/[^0-9]/g, ''), 10);
                    if (num >= 100) return m[0].trim().replace(/\s+/g, ' ');
                  }
                  const ptsEl = document.querySelector(
                    '[class*="points"], [class*="Points"], [aria-label*="points"], [aria-label*="Points"]'
                  );
                  if (ptsEl) return ptsEl.innerText.trim();
                  return null;
                }"""
            )
            if result:
                return result
        except Exception:
            pass
    try:
        text = task_scope.locator("body").inner_text(timeout=3000)
        for match in re.finditer(r"(\d[\d,]*)\s*(?:points?|pts?)", text, re.IGNORECASE):
            num = int(match.group(1).replace(",", ""))
            if num >= 100:
                return re.sub(r"\s+", " ", match.group(0).strip())
    except Exception:
        pass
    return None


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
            print("[✓] Logged in. Waiting for page to settle...")
            sleep(5)
            print("[✓] Retrying tasks...")
            guide_tasks(page)
            return
        print("[!] Auto-login failed. Please sign in manually, then re-run.")
        return
    except RewardsSidebarError as exc:
        print(f"[!] {exc}")
        return

    found_any = False

    # Display starting points
    start_points = get_points(page, sidebar)
    print(f"Starting points: {start_points if start_points else 'N/A'}")

    # 1. Bing Search Streak: do a search (works for both old and new sidebar)
    print("Completing Bing Search streak...")
    search_for_term(page, "weather")
    sleep(random.uniform(3.0, 5.0))
    try:
        page.locator("#est_en").first.click(timeout=2000)
    except PlaywrightTimeoutError:
        pass
    sleep(random.uniform(2.0, 4.0))
    found_any = True

    # 2. Try new React section-based approach first
    for heading, label in [
        (DAILY_SET_SECTION_HEADING, "Daily Set"),
        (KEEP_EARNING_SECTION_HEADING, "Keep Earning"),
    ]:
        try:
            sidebar = open_rewards_sidebar(page)
        except RewardsSidebarError:
            continue
        tasks = list_visible_tasks(page, section_heading=heading, sidebar=sidebar)
        if tasks:
            found_any = True
            print(f"Detected {len(tasks)} {label} tasks:")
            for task in tasks:
                print(f"  {task.index}. {task.title} [{task.status}]")
            complete_section_tasks(page, tasks, label.lower().replace(" ", "-"))

    # 3. If nothing found via new approach, try legacy CSS selectors
    if not found_any:
        for task_type in LEGACY_TASK_TYPES:
            selector_list = globals().get(task_type)
            if not selector_list:
                continue
            try:
                sidebar = open_rewards_sidebar(page)
            except RewardsSidebarError:
                continue
            tasks = list_visible_tasks(page, selector_list=tuple(selector_list), sidebar=sidebar)
            if not tasks:
                continue
            found_any = True
            task_label = task_type.replace("_TASK_SELECTOR", "").replace("_", " ").lower()
            print(f"Detected {len(tasks)} {task_label.title()} tasks:")
            for task in tasks:
                print(f"  {task.index}. {task.title} [{task.status}]")
            complete_section_tasks(page, tasks, task_label)

    if not found_any:
        print("No Rewards tasks found in the sidebar.")

    # Display ending points
    end_points = get_points(page, sidebar)
    print(f"Ending points: {end_points if end_points else 'N/A'}")
    if start_points and end_points:
        try:
            start_num = int(start_points.replace(",", "").split()[0])
            end_num = int(end_points.replace(",", "").split()[0])
            diff = end_num - start_num
            print(f"Points earned: {diff:+d}")
        except (ValueError, IndexError):
            pass


def complete_section_tasks(page: Page, tasks: list[RewardTask], section_label: str) -> None:
    for task in tasks:
        if task.status == "complete":
            print(f"  [{section_label}] Skipping completed '{task.title}'")
            continue
        print(f"  [{section_label}] Clicking '{task.title}'...")
        try:
            task.selector.click(timeout=5000)
        except PlaywrightTimeoutError:
            print(f"  [{section_label}] Could not click '{task.title}', skipping")
            continue
        sleep(random.uniform(2.0, 4.0))

        try:
            page.wait_for_timeout(2000)
            context = page.context
            new_pages = [p for p in context.pages if p != page and not p.is_closed()]
            if new_pages:
                new_page = new_pages[0]
                print(f"  [{section_label}] New page: {new_page.url}")
                try:
                    new_page.wait_for_load_state("domcontentloaded", timeout=3000)
                except PlaywrightTimeoutError:
                    pass
                sleep(random.uniform(2.0, 4.0))
                new_page.close()
                page.bring_to_front()
                sleep(random.uniform(1.0, 2.0))
            else:
                try:
                    page.locator("button:has-text('Close'), a:has-text('Close')").first.click(
                        timeout=2000
                    )
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
