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
