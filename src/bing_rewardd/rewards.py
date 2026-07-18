from __future__ import annotations

from dataclasses import dataclass
import random
from time import sleep
from typing import Any
from urllib.parse import quote_plus

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from bing_rewardd.browser import BING_URL


@dataclass(frozen=True)
class RewardTask:
    index: int
    title: str
    status: str
    selector: Locator


class RewardsSidebarError(RuntimeError):
    """Raised when the Bing Rewards sidebar cannot be opened or found."""


TASKS_TYPES = ["DAILY_SET_TASK_SELECTOR", "EXPLORE_TASK_SELECTOR"]

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
        page.wait_for_timeout(1500)
    except PlaywrightTimeoutError:
        pass

    sidebar = find_rewards_sidebar(page)
    if sidebar is None:
        raise RewardsSidebarError(
            "Could not find the Bing Rewards sidebar after clicking the upper-right Rewards icon."
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


def guide_tasks(page: Page) -> None:
    sidebar = open_rewards_sidebar(page)
    for task_type in TASKS_TYPES:
        selector_list = globals().get(task_type)
        if not selector_list:
            continue
        tasks = list_visible_tasks(page, selector_list, sidebar=sidebar)
        if not tasks:
            continue

        print(f"Detected {len(tasks)} {task_type.replace('_', ' ').title()}s:")
        for task in tasks:
            print(f"{task.index}. {task.title} [{task.status}]")

        if task_type == "EXPLORE_TASK_SELECTOR":
            complete_explore_tasks(page, tasks)
        elif task_type == "DAILY_SET_TASK_SELECTOR":
            complete_daily_set(page, tasks)


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
    if "complete" in lowered or "completed" in lowered:
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
