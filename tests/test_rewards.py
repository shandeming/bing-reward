import pytest

from bing_rewardd.rewards import (
    REWARDS_ICON_SELECTORS,
    RewardsSidebarError,
    _infer_status,
    click_rewards_icon,
    list_visible_tasks,
)


class FakePage:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def goto(self, url: str, wait_until: str) -> None:
        self.urls.append(url)


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

    def is_visible(self, timeout: int) -> bool:
        return self.visible

    def inner_text(self, timeout: int) -> str:
        return self.text

    def click(self, timeout: int) -> None:
        self.clicks += 1


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


def test_list_visible_tasks_reads_only_sidebar_scope() -> None:
    page = FakeLocatorPage(
        {
            "a[href]": [FakeLocator("Outside homepage reward text +5 points")],
        }
    )
    sidebar = FakeLocator(
        children={
            "a[href]": [FakeLocator("Daily poll +10 points")],
            "button": [FakeLocator("Completed offer")],
        }
    )

    tasks = list_visible_tasks(page, sidebar=sidebar)

    assert [task.title for task in tasks] == ["Daily poll +10 points", "Completed offer"]


def test_list_visible_tasks_reads_rewards_flyout_iframe() -> None:
    sidebar = FakeLocator(children={"iframe": [FakeLocator()]})
    frame_scope = FakeLocator(
        children={
            "a[href]": [FakeLocator("Iframe reward task +10 points")],
        }
    )
    page = FakeFramePage({}, frame_scope)

    tasks = list_visible_tasks(page, sidebar=sidebar)

    assert [task.title for task in tasks] == ["Iframe reward task +10 points"]
