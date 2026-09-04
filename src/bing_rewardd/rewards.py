from __future__ import annotations

import os
import random
import re
import sys
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
DAILY_SET_TASK_SELECTOR = [
    "#daily_set_card .promo_cont > a[href]",
    "#daily_set_card .promo_cont:not(:has(a[href]))",
]
KEEP_EARNING_TASK_SELECTOR = [
    ".flyout_control_halfUnit:has(> .promo_cont[role='banner']) > "
    ".promo_cont[role='banner'] > a[href]",
    ".flyout_control_halfUnit:has(> .promo_cont[role='banner']) > "
    ".promo_cont[role='banner']:not(:has(a[href]))",
    "#exb-activityChecklist .promo_cont > a[href]",
    "#exb-activityChecklist .promo_cont:not(:has(a[href]))",
]
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

REWARDS_BALANCE_SELECTORS = (
    ".balance_card_points_clickable",
    "[aria-label='Rewards dashboard' i]",
    "[class*='balance'][class*='point']",
    "[aria-label*='rewards points' i]",
    "[aria-label*='points balance' i]",
)

DASHBOARD_CLAIM_CONTROL_SELECTORS = (
    "button:has-text('Claim points')",
    "[role='button']:has-text('Claim points')",
    "a:has-text('Claim points')",
)

# The bonus card is rendered inside the Rewards flyout and has changed its
# classes between Bing layouts.  Discovery therefore starts with all
# clickable controls and validates the nearby card text instead of depending
# on one generated class name.
BONUS_CLAIM_CONTROL_SELECTOR = (
    'button, a, [role="button"], input[type="button"], input[type="submit"]'
)
BONUS_CARD_MAX_TEXT_LENGTH = 500

_FIND_BONUS_CLAIM_SCRIPT = rf"""
    (el) => {{
        const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim();
        const controls = Array.from(el.querySelectorAll(
            '{BONUS_CLAIM_CONTROL_SELECTOR}'
        ));
        const visible = (node) => {{
            const rect = node.getBoundingClientRect();
            const style = window.getComputedStyle(node);
            return rect.width > 0 && rect.height > 0 &&
                style.visibility !== 'hidden' && style.display !== 'none';
        }};
        const labelFor = (node) => normalize([
            node.innerText,
            node.value,
            node.getAttribute('aria-label'),
            node.getAttribute('title')
        ].filter(Boolean).join(' '));
        const isBonusCard = (text) =>
            text.length <= {BONUS_CARD_MAX_TEXT_LENGTH} &&
            (/\bbonus\s+points?\b/i.test(text) ||
                /\bpoints?\s+bonus\b/i.test(text));

        for (let index = 0; index < controls.length; index += 1) {{
            const control = controls[index];
            if (!visible(control) || !/\bclaim\b/i.test(labelFor(control))) {{
                continue;
            }}
            for (
                let ancestor = control;
                ancestor && ancestor !== el;
                ancestor = ancestor.parentElement
            ) {{
                const cardText = normalize(ancestor.innerText || ancestor.textContent);
                if (cardText.length > {BONUS_CARD_MAX_TEXT_LENGTH}) {{
                    break;
                }}
                if (isBonusCard(cardText)) {{
                    return {{ index, cardText }};
                }}
            }}
        }}
        return null;
    }}
"""

_IS_BONUS_CLAIM_CONTROL_SCRIPT = rf"""
    (el) => {{
        const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim();
        const label = normalize([
            el.innerText,
            el.value,
            el.getAttribute('aria-label'),
            el.getAttribute('title')
        ].filter(Boolean).join(' '));
        if (!/\bclaim\b/i.test(label)) return false;
        for (
            let ancestor = el;
            ancestor;
            ancestor = ancestor.parentElement
        ) {{
            const cardText = normalize(ancestor.innerText || ancestor.textContent);
            if (cardText.length > {BONUS_CARD_MAX_TEXT_LENGTH}) break;
            if (/\bbonus\s+points?\b/i.test(cardText) ||
                /\bpoints?\s+bonus\b/i.test(cardText)) {{
                return cardText;
            }}
        }}
        return false;
    }}
"""


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
        normalized_heading = section_heading.casefold()
        if normalized_heading == DAILY_SET_SECTION_HEADING.casefold():
            fallback_selectors = tuple(DAILY_SET_TASK_SELECTOR)
        elif normalized_heading == KEEP_EARNING_SECTION_HEADING.casefold():
            fallback_selectors = tuple(KEEP_EARNING_TASK_SELECTOR)
        else:
            fallback_selectors = LEGACY_TASK_SELECTORS
        return _find_tasks_by_selectors(page, task_scope, fallback_selectors, limit)

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
            const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const normalizedHeading = normalize(heading).toLocaleLowerCase();
            const allCards = Array.from(el.querySelectorAll('a[href]'));
            const containers = [];
            const addContainer = (candidate) => {
                if (candidate && candidate.querySelector('a[href]') && !containers.includes(candidate)) {
                    containers.push(candidate);
                }
            };

            // The legacy/current Bing flyout does not use <section>. Its two task
            // groups instead use a stable id and a stable layout class.
            if (normalizedHeading === 'daily set') {
                addContainer(el.querySelector('#daily_set_card'));
            } else if (normalizedHeading === 'keep earning') {
                const halfUnitContainers = el.querySelectorAll(
                    '.flyout_control_halfUnit:has(> .promo_cont[role="banner"]), ' +
                    '.flyout_control_halfUnit:has(> .promo_cont[data-is-inprogress-enabled])'
                );
                for (const halfUnitContainer of halfUnitContainers) {
                    addContainer(halfUnitContainer);
                }
                addContainer(el.querySelector('#exb-activityChecklist'));
            }

            // Newer React variants use sections. Find an exact heading first so
            // a broad parent containing several Rewards groups is not selected.
            const headingCandidates = el.querySelectorAll(
                'h1,h2,h3,h4,h5,h6,[role="heading"],p,[id]'
            );
            for (const headingEl of headingCandidates) {
                if (normalize(headingEl.innerText).toLocaleLowerCase() !== normalizedHeading) {
                    continue;
                }
                const section = headingEl.closest('section');
                if (section) {
                    addContainer(section);
                    continue;
                }
                const knownTaskContainer = headingEl.closest(
                    '#daily_set_card, #exb-activityChecklist, .flyout_control_halfUnit'
                );
                if (knownTaskContainer) {
                    // A completed group may intentionally contain no task links.
                    // Stop here instead of walking into the entire Rewards flyout.
                    addContainer(knownTaskContainer);
                    continue;
                }
                if (normalizedHeading === 'daily set' || normalizedHeading === 'keep earning') {
                    continue;
                }
                for (
                    let ancestor = headingEl.parentElement;
                    ancestor && ancestor !== el;
                    ancestor = ancestor.parentElement
                ) {
                    if (ancestor.querySelector('a[href]')) {
                        addContainer(ancestor);
                        break;
                    }
                }
            }

            // Retain support for section variants whose heading is not a semantic
            // heading element.
            for (const section of el.querySelectorAll('section')) {
                if (normalize(section.innerText).toLocaleLowerCase().includes(normalizedHeading)) {
                    addContainer(section);
                }
            }

            const cards = [];
            const seenCards = new Set();
            for (const container of containers) {
                for (const card of container.querySelectorAll('a[href]')) {
                    if (seenCards.has(card)) continue;
                    seenCards.add(card);
                    cards.push(card);
                }
            }
            cards.sort((left, right) => allCards.indexOf(left) - allCards.indexOf(right));

            return cards.map((a) => {
                const titleEl = a.querySelector('.promo-title, [data-testid*="title" i]');
                const titleImage = Array.from(a.querySelectorAll('img[alt]'))
                    .find((img) => normalize(img.alt));
                const firstParagraph = a.querySelector('p');
                const title = normalize(
                    titleEl?.innerText || titleImage?.alt || a.getAttribute('aria-label') ||
                    firstParagraph?.innerText || a.innerText
                );

                const descEl = a.querySelector('.promo-desc') ||
                    Array.from(a.querySelectorAll('p')).find((p) => p !== titleEl && p !== firstParagraph);
                const desc = normalize(descEl?.innerText);

                const pointsEl = a.querySelector(
                    '[aria-label*="point" i], .shortPoint.point, ' +
                    '.text-globalBody2Strong, .text-metadata'
                );
                const points = normalize(
                    pointsEl?.getAttribute('aria-label') || pointsEl?.innerText
                );
                const text = normalize(a.innerText);
                const completed = /\\bcompleted\\b/i.test(text) || Boolean(a.querySelector(
                    '.complete, .completed, .checkMark, ' +
                    '[aria-label*="points added" i], [data-status="complete" i], ' +
                    '[data-status="completed" i]'
                ));

                return {
                    idx: allCards.indexOf(a),
                    title,
                    desc,
                    points,
                    completed,
                };
            }).filter((card) =>
                card.idx >= 0 && card.title.length > 2 &&
                card.title.toLocaleLowerCase() !== 'show more'
            );
        }""",
        section_heading,
        timeout=5000,
    )

    # Evaluation returns each card's index among all body links. That index remains
    # correct when non-task links are filtered out and works for both DOM variants.
    card_locator = frame.locator("a[href]")

    tasks: list[RewardTask] = []
    seen_card_indices: set[int] = set()
    for card in result:
        card_idx = card["idx"]
        if card_idx in seen_card_indices:
            continue
        seen_card_indices.add(card_idx)
        title = f"{card['title']} | {card['desc']}" if card.get("desc") else card["title"]
        status = "complete" if card.get("completed") else ("available" if card.get("points") else "visible")
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


def _format_points_number(text: str) -> str | None:
    match = re.search(r"(?<![\d,])(\d[\d,]*)(?![\d,])", text)
    if not match:
        return None
    return f"{match.group(1)} points"


def _extract_labeled_points_balance(text: str) -> str | None:
    """Extract a balance only when it is explicitly labeled as such."""
    normalized = re.sub(r"\s+", " ", text).strip()
    patterns = (
        r"(?P<points>\d[\d,]*)\s+(?:my\s+)?rewards\s+points\b",
        r"\b(?:my\s+)?rewards\s+points\s+(?P<points>\d[\d,]*)",
        r"(?P<points>\d[\d,]*)\s+(?:available|current|total)\s+points\b",
        r"\b(?:available|current|total)\s+points\s+(?P<points>\d[\d,]*)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            return f"{match.group('points')} points"
    return None


def get_points(page: Page, sidebar: Locator | None = None) -> str | None:
    """Read the current Rewards balance without mistaking offer values for it."""
    task_scope = sidebar or find_rewards_sidebar(page)
    if task_scope is None:
        return None

    frame = _get_rewards_frame(page, task_scope)
    scope = frame or task_scope

    # Prefer elements that identify the account balance. The flyout contains
    # many other point values (referral offers, streak bonuses, task values).
    for selector in REWARDS_BALANCE_SELECTORS:
        try:
            candidate = scope.locator(selector).first
            if not _is_visible(candidate):
                continue
            points = _format_points_number(candidate.inner_text(timeout=3000))
            if points:
                return points
        except Exception:
            continue

    # Support older layouts, but require an explicit balance label. Returning
    # N/A is safer than treating the first promotional "7,500 points" as the
    # user's balance.
    try:
        text = scope.locator("body").inner_text(timeout=3000)
        return _extract_labeled_points_balance(text)
    except Exception:
        return None


def _find_bonus_claim_target(
    page: Page,
    sidebar: Locator,
) -> tuple[Locator, str] | None:
    """Find the Claim control belonging to the visible bonus-points card."""
    scope = _task_locator_scope(page, sidebar)
    controls = scope.locator(BONUS_CLAIM_CONTROL_SELECTOR)

    # A DOM evaluation keeps the selector independent of generated React
    # classes while returning an index that can be converted into a live
    # Playwright locator in the same frame.
    discovery: Any = None
    try:
        body = scope.locator("body")
        try:
            body_count = body.count()
        except Exception:
            body_count = 0
        evaluation_target = body.first if body_count else scope
        if hasattr(evaluation_target, "evaluate"):
            discovery = evaluation_target.evaluate(
                _FIND_BONUS_CLAIM_SCRIPT,
                timeout=5000,
            )
    except Exception:
        discovery = None

    if isinstance(discovery, dict):
        raw_index = discovery.get("index")
        if isinstance(raw_index, int):
            try:
                if 0 <= raw_index < controls.count():
                    candidate = controls.nth(raw_index)
                    if _is_visible(candidate):
                        return candidate, str(discovery.get("cardText") or "")
            except Exception:
                pass

    # Fallback for older or unusual flyouts where evaluating the body is not
    # available.  Each candidate is still checked against its nearby card so
    # a generic Redeem/Claim control cannot be selected accidentally.
    try:
        count = min(controls.count(), 50)
    except Exception:
        count = 0
    for index in range(count):
        candidate = controls.nth(index)
        if not _is_visible(candidate):
            continue
        try:
            card_text = candidate.evaluate(
                _IS_BONUS_CLAIM_CONTROL_SCRIPT,
                timeout=3000,
            )
        except Exception:
            continue
        if isinstance(card_text, str) and card_text:
            return candidate, card_text

    return None


def _extract_bonus_points(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text).strip()
    patterns = (
        r"\b(?:claim|earn)\s+(?:your\s+)?(?P<points>\d[\d,]*)\s+bonus\s+points?\b",
        r"\b(?P<points>\d[\d,]*)\s+bonus\s+points?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            return f"{match.group('points')} points"
    return None


def _points_number(points: str | None) -> int | None:
    if not points:
        return None
    try:
        return int(points.replace(",", "").split()[0])
    except (ValueError, IndexError):
        return None


def _context_pages(page: Page) -> list[Page]:
    try:
        return list(page.context.pages)
    except Exception:
        return []


def _new_context_pages(page: Page, existing_pages: list[Page]) -> list[Page]:
    existing_ids = {id(existing) for existing in existing_pages}
    new_pages: list[Page] = []
    for candidate in _context_pages(page):
        if id(candidate) in existing_ids or candidate == page:
            continue
        try:
            if candidate.is_closed():
                continue
        except Exception:
            pass
        new_pages.append(candidate)
    return new_pages


def _wait_for_new_page(
    page: Page,
    existing_pages: list[Page],
    attempts: int = 12,
) -> Page | None:
    for attempt in range(attempts):
        new_pages = _new_context_pages(page, existing_pages)
        if new_pages:
            return new_pages[0]
        if attempt < attempts - 1:
            try:
                page.wait_for_timeout(500)
            except Exception:
                pass
    return None


def _find_dashboard_claim_control(page: Page) -> Locator | None:
    """Find the final Claim points button on the Rewards dashboard modal."""
    for selector in DASHBOARD_CLAIM_CONTROL_SELECTORS:
        controls = page.locator(selector)
        try:
            count = min(controls.count(), 10)
        except Exception:
            continue
        for index in range(count):
            candidate = controls.nth(index)
            if not _is_visible(candidate):
                continue
            label = " ".join(_clean_text(candidate).split()).casefold()
            if "claim points" in label:
                return candidate
    return None


def _close_new_pages(page: Page, existing_pages: list[Page]) -> None:
    """Close pages opened by a Rewards control and restore the main page."""
    for opened in _new_context_pages(page, existing_pages):
        try:
            opened.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
        try:
            opened.close()
        except Exception:
            pass
    try:
        page.bring_to_front()
    except Exception:
        pass


def _claim_dashboard_bonus(
    dashboard_page: Page,
    original_page: Page,
    sidebar: Locator,
    starting_points: str | None,
) -> bool:
    """Complete the second-step Claim points action in the dashboard modal."""
    claim_control: Locator | None = None
    for attempt in range(12):
        claim_control = _find_dashboard_claim_control(dashboard_page)
        if claim_control is not None:
            break
        if attempt < 11:
            try:
                dashboard_page.wait_for_timeout(500)
            except Exception:
                pass

    if claim_control is None:
        return False

    print("  [bonus] Confirming claim in the Rewards dashboard...")
    clicked = False
    try:
        claim_control.click(timeout=5000)
        clicked = True
    except PlaywrightTimeoutError:
        pass

    for attempt in range(8):
        if attempt:
            try:
                dashboard_page.wait_for_timeout(500)
            except Exception:
                pass
        if _find_dashboard_claim_control(dashboard_page) is None:
            return True

    # The dashboard may leave the button visible while the flyout is updated.
    # Accept the original flyout's card/balance transition as confirmation.
    return clicked and _wait_for_bonus_claim(original_page, sidebar, starting_points)


def _wait_for_bonus_claim(
    page: Page,
    sidebar: Locator,
    starting_points: str | None,
) -> bool:
    starting_number = _points_number(starting_points)
    for attempt in range(6):
        if attempt:
            try:
                page.wait_for_timeout(1000)
            except Exception:
                pass

        current_sidebar = find_rewards_sidebar(page) or sidebar
        target = _find_bonus_claim_target(page, current_sidebar)
        current_points = get_points(page, current_sidebar)
        current_number = _points_number(current_points)

        # The flyout normally removes or changes the card after claiming.  A
        # balance increase is accepted as a second, independent confirmation.
        if target is None:
            return True
        if (
            starting_number is not None
            and current_number is not None
            and current_number > starting_number
        ):
            return True
    return False


def claim_bonus_points(page: Page, sidebar: Locator | None = None) -> bool:
    """Claim the visible bonus-points card at the end of a Rewards run."""
    task_scope = sidebar or find_rewards_sidebar(page)
    if task_scope is None:
        print("  [bonus] Rewards sidebar unavailable; skipping bonus claim.")
        return False

    target = _find_bonus_claim_target(page, task_scope)
    if target is None:
        print("  [bonus] No bonus points are currently available to claim.")
        return False

    control, card_text = target
    amount = _extract_bonus_points(card_text)
    amount_label = f" ({amount})" if amount else ""
    starting_points = get_points(page, task_scope)
    print(f"  [bonus] Claiming available bonus points{amount_label}...")

    existing_pages = _context_pages(page)
    clicked = False
    for attempt in range(2):
        try:
            control.click(timeout=5000)
            clicked = True
            break
        except PlaywrightTimeoutError:
            if attempt:
                break
            refreshed_sidebar = find_rewards_sidebar(page)
            if refreshed_sidebar is None:
                break
            refreshed_target = _find_bonus_claim_target(page, refreshed_sidebar)
            if refreshed_target is None:
                # The first click may have succeeded while the iframe was
                # rerendering; let the verification pass decide.
                break
            control, card_text = refreshed_target
            amount = _extract_bonus_points(card_text)
            amount_label = f" ({amount})" if amount else ""

    try:
        page.wait_for_timeout(1500)
    except Exception:
        pass

    # The flyout's first Claim control opens the Rewards dashboard in a new
    # tab. That page contains a second, final "Claim points" button; closing
    # the tab here would leave the bonus in its pending state.
    dashboard_page = _wait_for_new_page(page, existing_pages)
    if dashboard_page is not None:
        dashboard_claimed = _claim_dashboard_bonus(
            dashboard_page,
            page,
            task_scope,
            starting_points,
        )
        _close_new_pages(page, existing_pages)
        if dashboard_claimed:
            print(f"  [bonus] Bonus points claimed{amount_label}.")
            return True
        print("  [bonus] Could not complete the Claim points dashboard action.")
        return False

    # Support layouts that reuse the current tab instead of opening a new
    # dashboard page.
    if _find_dashboard_claim_control(page) is not None:
        dashboard_claimed = _claim_dashboard_bonus(
            page,
            page,
            task_scope,
            starting_points,
        )
        if dashboard_claimed:
            print(f"  [bonus] Bonus points claimed{amount_label}.")
            return True

    _close_new_pages(page, existing_pages)
    if _wait_for_bonus_claim(page, task_scope, starting_points):
        print(f"  [bonus] Bonus points claimed{amount_label}.")
        return True

    if clicked:
        print("  [bonus] Claim was clicked, but the result could not be verified.")
    else:
        print("  [bonus] Could not click the bonus Claim control.")
    return False


def _report_points_change(start_points: str | None, end_points: str | None) -> int | None:
    start_num = _points_number(start_points)
    end_num = _points_number(end_points)
    if start_num is None or end_num is None:
        return None

    diff = end_num - start_num
    print(f"Points earned: {diff:+d}")
    if diff <= 0:
        print(
            "[!] ALERT: No new Microsoft Rewards points were earned.",
            file=sys.stderr,
        )
        if os.environ.get("GITHUB_ACTIONS", "").casefold() == "true":
            print(
                "::warning title=No Rewards points earned::"
                "The Microsoft Rewards balance did not increase during this run."
            )
    return diff


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
            complete_section_tasks(
                page,
                tasks,
                label.lower().replace(" ", "-"),
                section_heading=heading,
            )

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
            complete_section_tasks(
                page,
                tasks,
                task_label,
                selector_list=tuple(selector_list),
            )

    if not found_any:
        print("No Rewards tasks found in the sidebar.")

    # 4. The completed task groups can unlock a separate bonus card at the
    # top of the flyout. Refresh the sidebar so the claim uses live locators.
    print("Checking for bonus points to claim...")
    try:
        sidebar = open_rewards_sidebar(page)
    except (NotLoggedInError, RewardsSidebarError) as exc:
        print(f"  [bonus] Could not reopen Rewards sidebar: {exc}")
    else:
        claim_bonus_points(page, sidebar)

    # Display ending points
    end_points = get_points(page, sidebar)
    print(f"Ending points: {end_points if end_points else 'N/A'}")
    _report_points_change(start_points, end_points)


def _task_title_key(title: str) -> str:
    """Return the stable headline portion used to reacquire a Rewards card."""
    headline = title.split(" | ", 1)[0]
    return re.sub(r"\s+", " ", headline).strip().casefold()


def _refresh_task(
    page: Page,
    expected: RewardTask,
    *,
    section_heading: str | None,
    selector_list: tuple[str, ...] | None,
) -> RewardTask | None:
    """Reload the flyout and find a fresh locator for an expected task.

    Some Rewards cards navigate the flyout iframe instead of opening a popup.
    Every locator collected from the old iframe document is stale afterward, so
    return to Bing and rediscover the card rather than reusing those locators.
    """
    try:
        sidebar = open_rewards_sidebar(page)
    except (NotLoggedInError, RewardsSidebarError):
        return None

    current_tasks = list_visible_tasks(
        page,
        section_heading=section_heading,
        sidebar=sidebar,
        selector_list=selector_list,
    )
    expected_key = _task_title_key(expected.title)
    return next(
        (task for task in current_tasks if _task_title_key(task.title) == expected_key),
        None,
    )


def complete_section_tasks(
    page: Page,
    tasks: list[RewardTask],
    section_label: str,
    *,
    section_heading: str | None = None,
    selector_list: tuple[str, ...] | None = None,
) -> None:
    refresh_before_click = False
    for task in tasks:
        if task.status == "complete":
            print(f"  [{section_label}] Skipping completed '{task.title}'")
            continue

        active_task = task
        if refresh_before_click:
            refreshed_task = _refresh_task(
                page,
                task,
                section_heading=section_heading,
                selector_list=selector_list,
            )
            if refreshed_task is None:
                print(
                    f"  [{section_label}] Could not find "
                    f"'{task.title}' after refreshing, skipping"
                )
                continue
            active_task = refreshed_task

        print(f"  [{section_label}] Clicking '{task.title}'...")
        try:
            active_task.selector.click(timeout=5000)
        except PlaywrightTimeoutError:
            # The flyout can rerender between discovery and the first click too.
            # Refresh once and retry with a locator from the current iframe.
            refreshed_task = _refresh_task(
                page,
                task,
                section_heading=section_heading,
                selector_list=selector_list,
            )
            if refreshed_task is None:
                print(f"  [{section_label}] Could not click '{task.title}', skipping")
                continue
            try:
                refreshed_task.selector.click(timeout=5000)
            except PlaywrightTimeoutError:
                print(f"  [{section_label}] Could not click '{task.title}', skipping")
                continue

        refresh_before_click = True
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
