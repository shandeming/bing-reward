"""Optional browser regressions: BING_REWARDS_DOM_TESTS=1 python -m pytest tests/test_rewards_dom.py."""
import os

import pytest
from playwright.sync_api import sync_playwright

from bing_rewardd.rewards import _find_cards_by_section

pytestmark = pytest.mark.skipif(
    os.environ.get("BING_REWARDS_DOM_TESTS") != "1",
    reason="Opt-in DOM checks require installed Playwright Chromium",
)


@pytest.mark.parametrize(
    "markup, expected",
    [
        # The live Bing structure: daily streak progress is a separate card.
        ('<div><p>Daily Set (0/3 activities)</p><a href="/streak">Progress</a></div>'
         '<div id="daily_set_card"><p>DAILY SET</p>'
         '<div class="promo_cont"><a href="/daily"><div class="promo-title">Daily activity</div>'
         '<span class="shortPoint point">10</span></a></div></div>', ["Daily activity"]),
        # A shared section must not turn every Rewards link into a daily task.
        ('<section><div><h2>Daily set</h2><a href="/daily">Daily activity</a></div>'
         '<div><h2>Keep earning</h2><a href="/other">Other activity</a></div></section>',
         ["Daily activity"]),
        # Nonsemantic headings and containers occur in React variants.
        ('<div><div><span> Daily set </span></div><a href="/daily">Daily activity</a></div>'
         '<div><p>Keep earning</p><a href="/other">Other activity</a></div>', ["Daily activity"]),
        ('<section><div id="daily_set_card"><p>Daily set</p>'
         '<div class="dset_completion_comp">All done</div></div>'
         '<div><h2>Keep earning</h2><a href="/other">Other activity</a></div></section>', []),
        ('<section><h2>Keep earning</h2>'
         '<a href="/other">Explore your daily set</a></section>', []),
        # Hidden stale cards are ignored; offscreen rendered cards are retained.
        ('<div id="daily_set_card"><p>Daily set</p>'
         '<a href="/hidden" style="display:none">Hidden activity</a>'
         '<a href="/daily" style="display:block;margin-top:1500px">Daily activity</a></div>',
         ["Daily activity"]),
    ],
)
def test_daily_set_dom_boundaries(markup, expected):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(markup)
            tasks = _find_cards_by_section(page, "Daily set")
            assert [task.title for task in tasks] == expected
            for task in tasks:
                assert task.selector.get_attribute("href") == "/daily"
        finally:
            browser.close()


@pytest.mark.parametrize("placeholder", [True, False])
def test_daily_set_lazy_loads_inside_scrolling_iframe(placeholder):
    from bing_rewardd.rewards import list_visible_tasks

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content('<div id="rewid-f"><iframe style="height:300px"></iframe></div>')
            frame = page.frames[1]
            target_id = 'daily_set_card' if placeholder else 'pending'
            frame.set_content(
                '<div style="height:1800px">Rewards summary and streaks</div>'
                f'<div id="{target_id}" style="min-height:50px">Daily set</div>'
            )
            frame.evaluate("""() => {
                const target = document.querySelector('#daily_set_card, #pending');
                const observer = new IntersectionObserver(entries => {
                    if (!entries.some(entry => entry.isIntersecting)) return;
                    target.id = 'daily_set_card';
                    target.innerHTML = '<p>Daily set</p><a href="/daily">Daily activity</a>';
                    observer.disconnect();
                });
                observer.observe(target);
            }""")
            tasks = list_visible_tasks(page, "Daily set", page.locator('#rewid-f'))
            assert [task.title for task in tasks] == ["Daily activity"]
            assert tasks[0].selector.get_attribute('href') == '/daily'
        finally:
            browser.close()
