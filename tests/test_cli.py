from pathlib import Path
from contextlib import contextmanager

from bing_rewardd.browser import BING_URL
import bing_rewardd.cli as cli
from bing_rewardd.browser import default_profile_dir
from bing_rewardd.cli import build_parser


def test_parser_accepts_start_command() -> None:
    args = build_parser().parse_args(["start"])

    assert args.command == "start"


def test_parser_accepts_custom_profile_dir() -> None:
    args = build_parser().parse_args(["--profile-dir", "custom-profile", "rewards"])

    assert args.command == "rewards"
    assert args.profile_dir == Path("custom-profile")


def test_parser_accepts_no_wait() -> None:
    args = build_parser().parse_args(["--no-wait", "tasks"])

    assert args.command == "tasks"
    assert args.no_wait is True


def test_default_profile_dir_uses_project_local_directory(tmp_path: Path) -> None:
    assert default_profile_dir(tmp_path) == tmp_path / ".browser-profile"


def test_rewards_command_opens_bing_before_sidebar(monkeypatch) -> None:
    calls: list[str] = []

    @contextmanager
    def fake_launch_persistent_browser(config):
        yield object()

    def fake_open_url(context, url: str):
        calls.append(url)
        return object()

    def fake_open_rewards_sidebar(page):
        calls.append("sidebar")

    monkeypatch.setattr(cli, "launch_persistent_browser", fake_launch_persistent_browser)
    monkeypatch.setattr(cli, "open_url", fake_open_url)
    monkeypatch.setattr(cli, "open_rewards_sidebar", fake_open_rewards_sidebar)
    monkeypatch.setattr(cli, "_wait_for_exit", lambda skip=False: None)

    assert cli.main(["rewards"]) == 0
    assert calls == [BING_URL, "sidebar"]


def test_tasks_command_opens_bing_before_guiding_tasks(monkeypatch) -> None:
    calls: list[str] = []

    @contextmanager
    def fake_launch_persistent_browser(config):
        yield object()

    def fake_open_url(context, url: str):
        calls.append(url)
        return object()

    def fake_guide_tasks(page):
        calls.append("tasks")

    monkeypatch.setattr(cli, "launch_persistent_browser", fake_launch_persistent_browser)
    monkeypatch.setattr(cli, "open_url", fake_open_url)
    monkeypatch.setattr(cli, "guide_tasks", fake_guide_tasks)
    monkeypatch.setattr(cli, "_wait_for_exit", lambda skip=False: None)

    assert cli.main(["tasks"]) == 0
    assert calls == [BING_URL, "tasks"]
