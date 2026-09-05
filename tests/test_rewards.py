import pytest
from typing import Any

from bing_rewardd.rewards import (
    REWARDS_ICON_SELECTORS,
    RewardsSidebarError,
    _infer_status,
    click_rewards_icon,
    list_visible_tasks,
)


class FakePage:
    def __init__(self, url: str = "") -> None:
        self.url = url
        self.urls: list[str] = []
        self.frames: list["FakeFrame"] = []
        self._locators: dict[str, list[FakeLocator]] = {}

    def goto(self, url: str, wait_until: str, timeout: int = 5000) -> None:
        self.url = url
        self.urls.append(url)

    def wait_for_load_state(self, state: str, timeout: int = 5000) -> None:
        pass

    def wait_for_timeout(self, timeout: int) -> None:
        pass

    def locator(self, selector: str) -> "FakeLocatorList":
        return FakeLocatorList(self._locators.get(selector, []))


class FakeLocator:
    def __init__(
        self,
        text: str = "",
        *,
        visible: bool = True,
        children: dict[str, list["FakeLocator"]] | None = None,
    ) -> None:
        self.text = text
        self.visible = visible
        self.children = children or {}
        self.clicks = 0

    @property
    def first(self) -> "FakeLocator":
        return self

    def locator(self, selector: str) -> "FakeLocatorList":
        return FakeLocatorList(self.children.get(selector, []))

    def evaluate(self, expression: str, *args: object, timeout: int = 5000) -> Any:
        return []

    def is_visible(self, timeout: int) -> bool:
        return self.visible

    def inner_text(self, timeout: int) -> str:
        return self.text

    def click(self, timeout: int) -> None:
        self.clicks += 1

    def evaluate(self, expression: str, *args: object, timeout: int = 5000) -> Any:
        return ""


class FakeLocatorList:
    def __init__(self, locators: list[FakeLocator]) -> None:
        self.locators = locators

    @property
    def first(self) -> FakeLocator:
        if not self.locators:
            return FakeLocator(visible=False)
        return self.locators[0]

    def count(self) -> int:
        return len(self.locators)

    def nth(self, index: int) -> FakeLocator:
        return self.locators[index]

    def inner_text(self, timeout: int) -> str:
        return self.first.inner_text(timeout)

    def is_visible(self, timeout: int) -> bool:
        return self.first.is_visible(timeout)

    def fill(self, value: str, timeout: int) -> None:
        self.first.fill(value, timeout)

    def locator(self, selector: str) -> "FakeLocatorList":
        return FakeLocatorList([])

    def evaluate(self, expression: str, *args: object, timeout: int = 5000) -> Any:
        return []


class FakeLocatorPage:
    def __init__(self, locators: dict[str, list[FakeLocator]]) -> None:
        self.locators = locators

    def locator(self, selector: str) -> FakeLocatorList:
        return FakeLocatorList(self.locators.get(selector, []))


class FakeFramePage(FakeLocatorPage):
    def __init__(
        self,
        locators: dict[str, list[FakeLocator]],
        frame_scope: FakeLocator,
    ) -> None:
        super().__init__(locators)
        self.frame_scope = frame_scope

    def frame_locator(self, selector: str) -> FakeLocator:
        return self.frame_scope


def test_infer_status_available_from_points_text() -> None:
    assert _infer_status("Daily poll +10 points") == "available"


def test_get_points_prefers_balance_element_over_promotional_points() -> None:
    from bing_rewardd.rewards import get_points

    sidebar = FakeLocator(
        children={
            ".balance_card_points_clickable": [
                FakeLocator("4,901\nMy rewards points")
            ],
            "body": [
                FakeLocator(
                    "Earn up to an extra 7,500 points a month. "
                    "4,901 My rewards points"
                )
            ],
        }
    )

    assert get_points(FakePage(), sidebar) == "4,901 points"


def test_get_points_fallback_requires_explicit_balance_label() -> None:
    from bing_rewardd.rewards import get_points

    sidebar = FakeLocator(
        children={
            "body": [
                FakeLocator(
                    "Earn up to an extra 7,500 points a month. "
                    "42 My rewards points"
                )
            ]
        }
    )

    assert get_points(FakePage(), sidebar) == "42 points"


def test_get_points_does_not_treat_offer_as_balance() -> None:
    from bing_rewardd.rewards import get_points

    sidebar = FakeLocator(
        children={"body": [FakeLocator("Earn up to 7,500 points a month.")]}
    )

    assert get_points(FakePage(), sidebar) is None


def test_get_points_reacquires_live_sidebar_after_flyout_rerender(monkeypatch) -> None:
    from bing_rewardd.rewards import get_points

    stale_sidebar = FakeLocator(
        children={
            "body": [
                FakeLocator("Ready to refer a friend? Earn up to 7,500 points a month.")
            ]
        }
    )
    live_sidebar = FakeLocator(
        children={
            ".balance_card_points_clickable": [
                FakeLocator("8,081\nMy rewards points")
            ]
        }
    )
    monkeypatch.setattr("bing_rewardd.rewards.find_rewards_sidebar", lambda page: live_sidebar)

    assert get_points(FakePage(), stale_sidebar) == "8,081 points"


def test_get_points_falls_back_to_outer_sidebar_when_frame_has_no_balance() -> None:
    from bing_rewardd.rewards import get_points

    frame_scope = FakeLocator(
        children={"body": [FakeLocator("Daily set Keep earning 10 points")]}
    )
    sidebar = FakeLocator(
        children={
            "iframe": [FakeLocator()],
            ".balance_card_points_clickable": [
                FakeLocator("8,081\nMy rewards points")
            ],
        }
    )
    page = FakeFramePage({}, frame_scope)

    assert get_points(page, sidebar) == "8,081 points"


def test_get_points_after_settle_retries_transient_empty_balance(monkeypatch) -> None:
    from bing_rewardd.rewards import _get_points_after_settle

    reads = iter([None, None, "8,081 points"])
    waits: list[int] = []
    monkeypatch.setattr("bing_rewardd.rewards.get_points", lambda page, sidebar: next(reads))
    page = FakePage()
    page.wait_for_timeout = lambda timeout: waits.append(timeout)  # type: ignore[method-assign]

    assert _get_points_after_settle(page, None, attempts=5) == "8,081 points"
    assert waits == [1000, 1000]


def test_report_points_change_warns_in_console(monkeypatch, capsys) -> None:
    from bing_rewardd.rewards import _report_points_change

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    result = _report_points_change("7,500 points", "7,500 points")
    captured = capsys.readouterr()

    assert result == 0
    assert "Points earned: +0" in captured.out
    assert "::warning" not in captured.out
    assert "ALERT: No new Microsoft Rewards points were earned" in captured.err


def test_report_points_change_emits_github_warning(monkeypatch, capsys) -> None:
    from bing_rewardd.rewards import _report_points_change

    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    result = _report_points_change("7,500 points", "7,500 points")
    captured = capsys.readouterr()

    assert result == 0
    assert "::warning title=No Rewards points earned::" in captured.out
    assert "ALERT: No new Microsoft Rewards points were earned" in captured.err


def test_report_points_change_does_not_warn_after_increase(monkeypatch, capsys) -> None:
    from bing_rewardd.rewards import _report_points_change

    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    result = _report_points_change("7,500 points", "7,510 points")
    captured = capsys.readouterr()

    assert result == 10
    assert "Points earned: +10" in captured.out
    assert "::warning" not in captured.out
    assert captured.err == ""


def test_infer_status_complete_from_completed_text() -> None:
    assert _infer_status("Task completed") == "complete"


def test_infer_status_not_complete_for_task_name_with_complete() -> None:
    """Task names containing 'Complete' (e.g. 'Complete this puzzle') are not marked done."""
    assert _infer_status("Complete this puzzle") == "visible"


def test_infer_status_complete_for_status_indicator() -> None:
    """Status phrases like 'is complete' or trailing 'complete' are still detected."""
    assert _infer_status("Daily poll is complete") == "complete"
    assert _infer_status("Task marked complete") == "complete"
    assert _infer_status("Puzzle complete") == "complete"


def test_rewards_icon_selector_prioritizes_bing_icon_id() -> None:
    assert REWARDS_ICON_SELECTORS[0] == "#id_rh_w"
    assert "[aria-label='Microsoft Rewards']" in REWARDS_ICON_SELECTORS


def test_click_rewards_icon_uses_primary_selector_first() -> None:
    primary = FakeLocator("Rewards")
    fallback = FakeLocator("Rewards")
    page = FakeLocatorPage(
        {
            "#id_rh_w": [primary],
            "[aria-label='Microsoft Rewards']": [fallback],
        }
    )

    click_rewards_icon(page)

    assert primary.clicks == 1
    assert fallback.clicks == 0


def test_click_rewards_icon_raises_clear_error_when_missing() -> None:
    page = FakeLocatorPage({})

    with pytest.raises(RewardsSidebarError, match="Microsoft Rewards icon"):
        click_rewards_icon(page)


def test_list_visible_tasks_returns_empty_without_sidebar() -> None:
    page = FakeLocatorPage({})

    tasks = list_visible_tasks(page)

    assert tasks == []


def test_list_visible_tasks_uses_section_heading(monkeypatch) -> None:
    found_calls: list[str] = []
    fake_result = [
        {"idx": 0, "title": "Daily poll", "desc": "Test task", "points": "+10", "completed": False},
    ]

    class FakeBody:
        def evaluate(self, expr: str, heading: str, timeout: int = 5000) -> list:
            found_calls.append(heading)
            return fake_result

    class FakeFrame:
        def locator(self, sel: str):
            if sel == "body":
                return FakeBody()
            return self

        def nth(self, idx: int) -> "FakeFrame":
            return self

    sidebar = FakeLocator(children={"iframe": [FakeLocator()]})
    page = FakeFramePage({}, FakeFrame())
    page.frame_locator = lambda sel: FakeFrame()  # type: ignore

    tasks = list_visible_tasks(page, section_heading="Daily set", sidebar=sidebar)

    assert found_calls == ["Daily set"]
    assert len(tasks) == 1
    assert "Daily poll" in tasks[0].title


def test_list_visible_tasks_uses_keep_earning_specific_fallback(monkeypatch) -> None:
    from bing_rewardd.rewards import KEEP_EARNING_TASK_SELECTOR

    captured_selectors: list[tuple[str, ...]] = []
    sidebar = FakeLocator()
    page = FakeLocatorPage({})

    monkeypatch.setattr("bing_rewardd.rewards._get_rewards_frame", lambda page, sidebar: object())
    monkeypatch.setattr("bing_rewardd.rewards._find_cards_by_section", lambda frame, heading: [])

    def fake_find_tasks(page, task_scope, selector_list, limit):
        captured_selectors.append(selector_list)
        return []

    monkeypatch.setattr("bing_rewardd.rewards._find_tasks_by_selectors", fake_find_tasks)

    tasks = list_visible_tasks(page, section_heading="Keep earning", sidebar=sidebar)

    assert tasks == []
    assert captured_selectors == [tuple(KEEP_EARNING_TASK_SELECTOR)]


def test_list_visible_tasks_falls_back_to_legacy_selectors(monkeypatch) -> None:
    from bing_rewardd.rewards import (
        LEGACY_TASK_SELECTORS,
        DAILY_SET_SECTION_HEADING,
        KEEP_EARNING_SECTION_HEADING,
    )

    calls: list[str] = []

    class FakeBody:
        def evaluate(self, expr: str, heading: str, timeout: int = 5000) -> list:
            calls.append(heading)
            return []

    class FakeFrame:
        def locator(self, sel: str):
            if sel == "body":
                return FakeBody()
            return FakeLocator()

        def nth(self, idx: int) -> FakeLocator:
            return FakeLocator()

    class FakeScope:
        def __init__(self, locators: dict[str, list[FakeLocator]]) -> None:
            self._locators = locators

        def locator(self, sel: str) -> FakeLocatorList:
            return FakeLocatorList(self._locators.get(sel, []))

    sidebar = FakeLocator(children={"iframe": [FakeLocator()]})
    legacy_tasks = [FakeLocator("Daily poll +10 points")]
    page = FakeFramePage({}, FakeFrame())
    page.frame_locator = lambda sel: FakeFrame()  # type: ignore

    def fake_task_scope(p: FakeFramePage, sb: FakeLocator) -> FakeScope:
        return FakeScope({"a[href]": legacy_tasks})

    monkeypatch.setattr("bing_rewardd.rewards._task_locator_scope", fake_task_scope)

    tasks = list_visible_tasks(page, sidebar=sidebar)

    assert calls == [DAILY_SET_SECTION_HEADING, KEEP_EARNING_SECTION_HEADING]
    assert len(tasks) == 1
    assert "Daily poll" in tasks[0].title


def test_open_rewards_sidebar_raises_not_logged_in_for_signup_cta(monkeypatch) -> None:
    from bing_rewardd.rewards import NotLoggedInError, open_rewards_sidebar

    sidebar = FakeLocator()
    fake_page = FakePage()
    monkeypatch.setattr("bing_rewardd.rewards.find_rewards_sidebar", lambda p: sidebar)
    monkeypatch.setattr("bing_rewardd.rewards.click_rewards_icon", lambda p, **kw: None)
    monkeypatch.setattr(
        "bing_rewardd.rewards._task_locator_scope",
        lambda page, sb: FakeLocator(
            children={
                "body": [
                    FakeLocator(
                        "Get started. Earn points just for using Bing. Redeem them for gift cards."
                    )
                ]
            }
        ),
    )

    with pytest.raises(NotLoggedInError, match="Not signed in"):
        open_rewards_sidebar(fake_page)


def test_open_rewards_sidebar_returns_sidebar_when_logged_in(monkeypatch) -> None:
    from bing_rewardd.rewards import open_rewards_sidebar

    sidebar = FakeLocator()
    fake_page = FakePage()
    monkeypatch.setattr("bing_rewardd.rewards.find_rewards_sidebar", lambda p: sidebar)
    monkeypatch.setattr("bing_rewardd.rewards.click_rewards_icon", lambda p, **kw: None)
    monkeypatch.setattr(
        "bing_rewardd.rewards._task_locator_scope",
        lambda page, sb: FakeLocator(
            children={"body": [FakeLocator("Welcome back! Complete your daily poll.")]},
        ),
    )

    result = open_rewards_sidebar(fake_page)
    assert result is sidebar


def test_guide_tasks_catches_not_logged_in(monkeypatch, capsys) -> None:
    from bing_rewardd.rewards import NotLoggedInError, guide_tasks

    monkeypatch.setattr(
        "bing_rewardd.rewards.open_rewards_sidebar",
        lambda p: (_ for _ in ()).throw(NotLoggedInError("Not signed in to Microsoft Rewards.")),
    )
    monkeypatch.setattr("bing_rewardd.rewards.load_credentials", lambda: None)

    guide_tasks(FakePage())
    out = capsys.readouterr()
    assert "No credentials found" in out.out


class FakeLocatorWithForm:
    def __init__(self, fill_target: str = "email", visible: bool = True) -> None:
        self.fill_target = fill_target
        self.visible = visible
        self.fill_count = 0

    def is_visible(self, timeout: int) -> bool:
        return self.visible

    def fill(self, value: str, timeout: int) -> None:
        self.fill_count += 1


def test_guide_tasks_attempts_auto_login_when_not_signed_in(monkeypatch, capsys) -> None:
    from bing_rewardd.rewards import NotLoggedInError, guide_tasks

    sidebar = FakeLocator()
    login_called = False

    def fake_open(p):
        nonlocal login_called
        if not login_called:
            login_called = True
            raise NotLoggedInError("Not signed in.")
        return sidebar

    monkeypatch.setattr("bing_rewardd.rewards.open_rewards_sidebar", fake_open)
    monkeypatch.setattr(
        "bing_rewardd.rewards.load_credentials",
        lambda: {"email": "a@b.com", "password": "secret"},
    )
    monkeypatch.setattr("bing_rewardd.rewards._try_auto_login", lambda p, e, pw: True)
    monkeypatch.setattr(
        "bing_rewardd.rewards.list_visible_tasks",
        lambda *args, **kwargs: [],
    )

    guide_tasks(FakePage())
    out = capsys.readouterr()
    assert "[✓] Logged in" in out.out
    assert login_called is True


def test_guide_tasks_reports_no_credentials(monkeypatch, capsys) -> None:
    from bing_rewardd.rewards import NotLoggedInError, guide_tasks

    monkeypatch.setattr(
        "bing_rewardd.rewards.open_rewards_sidebar",
        lambda p: (_ for _ in ()).throw(NotLoggedInError("Not signed in.")),
    )
    monkeypatch.setattr("bing_rewardd.rewards.load_credentials", lambda: None)

    guide_tasks(FakePage())
    out = capsys.readouterr()
    assert "No credentials found" in out.out


def test_guide_tasks_reports_auto_login_failed(monkeypatch, capsys) -> None:
    from bing_rewardd.rewards import NotLoggedInError, guide_tasks

    monkeypatch.setattr(
        "bing_rewardd.rewards.open_rewards_sidebar",
        lambda p: (_ for _ in ()).throw(NotLoggedInError("Not signed in.")),
    )
    monkeypatch.setattr(
        "bing_rewardd.rewards.load_credentials",
        lambda: {"email": "a@b.com", "password": "secret"},
    )
    monkeypatch.setattr("bing_rewardd.rewards._try_auto_login", lambda p, e, pw: False)

    guide_tasks(FakePage())
    out = capsys.readouterr()
    assert "Auto-login failed" in out.out


def test_try_auto_login_returns_true_on_success(monkeypatch) -> None:
    from bing_rewardd.rewards import _try_auto_login

    page = FakePage("https://www.bing.com/")
    email_input = FakeLocatorWithForm("email")
    pw_input = FakeLocatorWithForm("password")
    submit_btn = FakeLocator(visible=True)
    yes_btn = FakeLocator(visible=True)

    # Track selector calls for wait_for_selector simulation
    selector_calls: list[str] = []

    def locator(sel: str):
        if "email" in sel or "loginfmt" in sel or "type=\"text\"" in sel:
            return FakeLocatorList([email_input])
        if "password" in sel:
            return FakeLocatorList([pw_input])
        if "submit" in sel or "idSIButton9" in sel or "id_btnNext" in sel:
            return FakeLocatorList([submit_btn])
        if "Yes" in sel:
            return FakeLocatorList([yes_btn])
        return FakeLocatorList([])

    page.locator = locator  # type: ignore
    page.click = lambda sel, timeout=5000: None  # type: ignore

    def fake_wait_for_load_state(state: str, timeout: int = 5000) -> None:
        page.url = "https://www.bing.com/"

    def fake_wait_for_selector(selector: str, timeout: int = 5000) -> None:
        selector_calls.append(selector)
        # Simulate password input appearing
        page.url = "https://www.bing.com/"

    page.wait_for_load_state = fake_wait_for_load_state  # type: ignore
    page.wait_for_selector = fake_wait_for_selector  # type: ignore
    page.wait_for_timeout = lambda ms: None  # type: ignore
    page.frames = [page]  # type: ignore

    result = _try_auto_login(page, "a@b.com", "secret")
    assert result is True
    assert email_input.fill_count == 1
    assert pw_input.fill_count == 1
    assert submit_btn.clicks == 2
    assert 'input[type="password"]' in selector_calls


def test_try_auto_login_returns_false_when_email_missing(monkeypatch) -> None:
    from bing_rewardd.rewards import _try_auto_login

    page = FakePage("https://login.live.com/login.srf")
    page.locator = lambda sel: FakeLocatorList([])  # type: ignore
    page.wait_for_load_state = lambda state, timeout=5000: None  # type: ignore
    page.wait_for_selector = lambda sel, timeout=5000: None  # type: ignore
    page.wait_for_timeout = lambda ms: None  # type: ignore
    page.frames = [page]  # type: ignore

    result = _try_auto_login(page, "a@b.com", "secret")
    assert result is False


def test_try_auto_login_returns_false_when_password_missing(monkeypatch) -> None:
    from bing_rewardd.rewards import _try_auto_login

    page = FakePage("https://login.live.com/pass")
    email_input = FakeLocatorWithForm("email")
    submit_btn = FakeLocator(visible=True)

    def locator(sel: str):
        if "email" in sel or "loginfmt" in sel:
            return FakeLocatorList([email_input])
        if "submit" in sel or "idSIButton9" in sel or "id_btnNext" in sel:
            return FakeLocatorList([submit_btn])
        return FakeLocatorList([])

    page.locator = locator  # type: ignore
    page.click = lambda sel, timeout=5000: None  # type: ignore
    page.wait_for_load_state = lambda state, timeout=5000: None  # type: ignore
    page.wait_for_selector = lambda sel, timeout=5000: None  # type: ignore
    page.wait_for_timeout = lambda ms: None  # type: ignore
    page.frames = [page]  # type: ignore

    result = _try_auto_login(page, "a@b.com", "secret")
    assert result is False


def test_find_cards_by_section_aggregates_and_deduplicates_global_link_indices(
    monkeypatch,
) -> None:
    from bing_rewardd.rewards import _find_cards_by_section

    # Links 4 and 7 come from separate supported containers. Link 4 is repeated
    # because one container is nested in another, and must only produce one task.
    eval_result = [
        {"idx": 4, "title": "Keep", "desc": "desc A", "points": "+10", "completed": False},
        {"idx": 7, "title": "Keep", "desc": "desc B", "points": "+15", "completed": True},
        {"idx": 4, "title": "Keep", "desc": "desc A", "points": "+10", "completed": False},
    ]
    nth_calls: list[int] = []
    expressions: list[str] = []

    class FakeSection:
        def locator(self, sel: str):
            return self

        def nth(self, idx: int):
            nth_calls.append(idx)
            return FakeLocator()

    class FakeFrame:
        def locator(self, sel: str):
            if sel == "body":
                return self
            return FakeSection()

        def evaluate(self, expr: str, heading: str, timeout: int = 5000) -> list:
            expressions.append(expr)
            return eval_result

    tasks = _find_cards_by_section(FakeFrame(), "Keep earning")
    assert nth_calls == [4, 7]
    assert len(tasks) == 2
    assert tasks[1].status == "complete"
    assert "#daily_set_card" in expressions[0]
    assert ".flyout_control_halfUnit" in expressions[0]
    assert "knownTaskContainer" in expressions[0]
    assert "Stop here instead of walking into the entire Rewards flyout" in expressions[0]
    assert "for (const container of containers)" in expressions[0]
    assert "containers[0]" not in expressions[0]
    assert ".checkMark" in expressions[0]


def test_complete_section_tasks_refreshes_locators_after_each_click(monkeypatch) -> None:
    from bing_rewardd.rewards import RewardTask, complete_section_tasks

    class StaleAfterFirstClick(FakeLocator):
        def __init__(self, state: dict[str, bool]) -> None:
            super().__init__()
            self.state = state

        def click(self, timeout: int) -> None:
            if self.state["stale"]:
                pytest.fail("reused a locator from the old iframe document")
            super().click(timeout)

    state = {"stale": False}

    class FirstTaskLocator(FakeLocator):
        def click(self, timeout: int) -> None:
            super().click(timeout)
            state["stale"] = True

    first_locator = FirstTaskLocator()
    stale_second_locator = StaleAfterFirstClick(state)
    fresh_second_locator = FakeLocator()
    tasks = [
        RewardTask(1, "First task | old description", "available", first_locator),
        RewardTask(2, "Second task | old description", "available", stale_second_locator),
    ]
    refresh_calls: list[str] = []

    def fake_refresh(page, expected, *, section_heading, selector_list):
        refresh_calls.append(expected.title)
        return RewardTask(
            expected.index,
            "Second task | refreshed description",
            expected.status,
            fresh_second_locator,
        )

    monkeypatch.setattr("bing_rewardd.rewards._refresh_task", fake_refresh)
    monkeypatch.setattr("bing_rewardd.rewards.sleep", lambda seconds: None)
    page = FakePage()
    page.context = type("Context", (), {"pages": [page]})()  # type: ignore[attr-defined]
    page.is_closed = lambda: False  # type: ignore[attr-defined]

    complete_section_tasks(
        page,
        tasks,
        "daily-set",
        section_heading="Daily set",
    )

    assert first_locator.clicks == 1
    assert stale_second_locator.clicks == 0
    assert fresh_second_locator.clicks == 1
    assert refresh_calls == ["Second task | old description"]


def test_refresh_task_matches_headline_when_description_changes(monkeypatch) -> None:
    from bing_rewardd.rewards import RewardTask, _refresh_task

    sidebar = FakeLocator()
    stale_task = RewardTask(1, "Daily poll | old description", "available", FakeLocator())
    fresh_task = RewardTask(1, "Daily poll | new description", "available", FakeLocator())

    monkeypatch.setattr("bing_rewardd.rewards.open_rewards_sidebar", lambda page: sidebar)
    monkeypatch.setattr(
        "bing_rewardd.rewards.list_visible_tasks",
        lambda *args, **kwargs: [fresh_task],
    )

    result = _refresh_task(
        FakePage(),
        stale_task,
        section_heading="Daily set",
        selector_list=None,
    )

    assert result is fresh_task


def test_try_auto_login_returns_false_when_still_on_login_page(monkeypatch) -> None:
    from bing_rewardd.rewards import _try_auto_login

    page = FakePage("https://login.live.com/confirm")
    email_input = FakeLocatorWithForm("email")
    pw_input = FakeLocatorWithForm("password")
    submit_btn = FakeLocator(visible=True)
    yes_btn = FakeLocator(visible=True)

    def locator(sel: str):
        if "email" in sel or "loginfmt" in sel:
            return FakeLocatorList([email_input])
        if "password" in sel:
            return FakeLocatorList([pw_input])
        if "submit" in sel or "idSIButton9" in sel or "id_btnNext" in sel:
            return FakeLocatorList([submit_btn])
        if "Yes" in sel:
            return FakeLocatorList([yes_btn])
        return FakeLocatorList([])

    page.locator = locator  # type: ignore
    page.click = lambda sel, timeout=5000: None  # type: ignore
    page.wait_for_load_state = lambda state, timeout=5000: None  # type: ignore
    page.wait_for_selector = lambda sel, timeout=5000: None  # type: ignore
    page.wait_for_timeout = lambda ms: None  # type: ignore
    page.frames = [page]  # type: ignore

    result = _try_auto_login(page, "a@b.com", "secret")
    # URL still contains "login.live.com", so should return False
    assert result is False
    result = _try_auto_login(page, "a@b.com", "secret")
    assert result is False


def test_find_bonus_claim_target_uses_bonus_card_context() -> None:
    from bing_rewardd.rewards import BONUS_CLAIM_CONTROL_SELECTOR, _find_bonus_claim_target

    redeem = FakeLocator("Redeem")
    claim = FakeLocator("Claim")

    class FakeBody:
        first = None

        def __init__(self) -> None:
            self.first = self

        def count(self) -> int:
            return 1

        def evaluate(self, expression: str, timeout: int = 5000) -> dict:
            assert "bonus\\s+points?" in expression
            return {
                "index": 1,
                "cardText": "Claim your 6 bonus points before they expire Claim",
            }

    class FakeScope:
        def locator(self, selector: str):
            if selector == "iframe":
                return FakeLocatorList([])
            if selector == "body":
                return FakeBody()
            if selector == BONUS_CLAIM_CONTROL_SELECTOR:
                return FakeLocatorList([redeem, claim])
            raise AssertionError(f"Unexpected selector: {selector}")

    result = _find_bonus_claim_target(FakePage(), FakeScope())  # type: ignore[arg-type]

    assert result is not None
    assert result[0] is claim
    assert "6 bonus points" in result[1]
    assert redeem.clicks == 0


def test_extract_bonus_points_from_claim_card() -> None:
    from bing_rewardd.rewards import _extract_bonus_points

    assert (
        _extract_bonus_points(
            "Claim your 1,250 bonus points before they start expiring on Oct 4, 2026"
        )
        == "1,250 points"
    )
    assert _extract_bonus_points("Ready for your next prize? Redeem") is None


def test_claim_bonus_points_clicks_and_reports_verified_claim(monkeypatch, capsys) -> None:
    from bing_rewardd.rewards import claim_bonus_points

    sidebar = FakeLocator()
    claim = FakeLocator("Claim")
    monkeypatch.setattr(
        "bing_rewardd.rewards._find_bonus_claim_target",
        lambda page, sb: (claim, "Claim your 6 bonus points before they expire Claim"),
    )
    monkeypatch.setattr(
        "bing_rewardd.rewards.get_points",
        lambda page, sb: "100 points",
    )
    monkeypatch.setattr(
        "bing_rewardd.rewards._wait_for_bonus_claim",
        lambda page, sb, points: True,
    )

    assert claim_bonus_points(FakePage(), sidebar) is True
    assert claim.clicks == 1
    assert "Bonus points claimed (6 points)" in capsys.readouterr().out


def test_claim_bonus_points_reports_unverified_click(monkeypatch, capsys) -> None:
    from bing_rewardd.rewards import claim_bonus_points

    sidebar = FakeLocator()
    claim = FakeLocator("Claim")
    monkeypatch.setattr(
        "bing_rewardd.rewards._find_bonus_claim_target",
        lambda page, sb: (claim, "Claim your 6 bonus points before they expire Claim"),
    )
    monkeypatch.setattr(
        "bing_rewardd.rewards.get_points",
        lambda page, sb: "100 points",
    )
    monkeypatch.setattr(
        "bing_rewardd.rewards._wait_for_bonus_claim",
        lambda page, sb, points: False,
    )

    assert claim_bonus_points(FakePage(), sidebar) is False
    assert "could not be verified" in capsys.readouterr().out


def test_guide_tasks_checks_bonus_after_task_sections(monkeypatch) -> None:
    from bing_rewardd.rewards import guide_tasks

    page = FakePage()
    sidebar = FakeLocator()
    claimed_with: list[FakeLocator] = []

    monkeypatch.setattr("bing_rewardd.rewards.open_rewards_sidebar", lambda p: sidebar)
    monkeypatch.setattr("bing_rewardd.rewards.search_for_term", lambda p, term: None)
    monkeypatch.setattr("bing_rewardd.rewards.sleep", lambda seconds: None)
    monkeypatch.setattr(
        "bing_rewardd.rewards.list_visible_tasks",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "bing_rewardd.rewards.get_points",
        lambda page, sb: "100 points",
    )
    monkeypatch.setattr(
        "bing_rewardd.rewards.claim_bonus_points",
        lambda page, sb: claimed_with.append(sb) or True,
    )

    guide_tasks(page)

    assert claimed_with == [sidebar]


def test_find_dashboard_claim_control_requires_final_label() -> None:
    from bing_rewardd.rewards import (
        DASHBOARD_CLAIM_CONTROL_SELECTORS,
        _find_dashboard_claim_control,
    )

    ready_card = FakeLocator("Ready to claim | 6 | Claim")
    final_claim = FakeLocator("6 | Pending | Claim points")
    page = FakeLocatorPage(
        {
            DASHBOARD_CLAIM_CONTROL_SELECTORS[0]: [final_claim],
            DASHBOARD_CLAIM_CONTROL_SELECTORS[1]: [ready_card],
        }
    )

    assert _find_dashboard_claim_control(page) is final_claim  # type: ignore[arg-type]


def test_claim_dashboard_bonus_clicks_final_modal_button() -> None:
    from bing_rewardd.rewards import (
        DASHBOARD_CLAIM_CONTROL_SELECTORS,
        _claim_dashboard_bonus,
    )

    class ClaimingControl(FakeLocator):
        def click(self, timeout: int) -> None:
            super().click(timeout)
            self.visible = False

    final_claim = ClaimingControl("Claim points")
    dashboard = FakeLocatorPage(
        {DASHBOARD_CLAIM_CONTROL_SELECTORS[0]: [final_claim]}
    )

    assert _claim_dashboard_bonus(dashboard, FakePage(), FakeLocator(), "100 points") is True  # type: ignore[arg-type]
    assert final_claim.clicks == 1


def test_claim_bonus_points_completes_dashboard_popup(monkeypatch, capsys) -> None:
    from bing_rewardd.rewards import claim_bonus_points

    main_page = FakePage()
    dashboard_page = FakePage()
    sidebar = FakeLocator()
    outer_claim = FakeLocator("Claim")
    main_page.context = type("Context", (), {"pages": [main_page, dashboard_page]})()  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "bing_rewardd.rewards._find_bonus_claim_target",
        lambda page, sb: (outer_claim, "Claim your 6 bonus points before they expire Claim"),
    )
    monkeypatch.setattr(
        "bing_rewardd.rewards.get_points",
        lambda page, sb: "100 points",
    )
    monkeypatch.setattr(
        "bing_rewardd.rewards._wait_for_new_page",
        lambda page, existing: dashboard_page,
    )
    monkeypatch.setattr(
        "bing_rewardd.rewards._claim_dashboard_bonus",
        lambda dashboard, original, sb, points: True,
    )

    assert claim_bonus_points(main_page, sidebar) is True
    assert outer_claim.clicks == 1
    assert "Bonus points claimed (6 points)" in capsys.readouterr().out
