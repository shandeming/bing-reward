from pathlib import Path
from contextlib import contextmanager


import bing_rewardd.cli as cli
from bing_rewardd.browser import default_profile_dir
from bing_rewardd.cli import build_parser


def test_parser_accepts_no_wait() -> None:
    args = build_parser().parse_args(["--no-wait", "tasks"])

    assert args.command == "tasks"
    assert args.no_wait is True


def test_parser_accepts_screenshot_dir() -> None:
    args = build_parser().parse_args(["--screenshot-dir", "shots", "tasks"])

    assert args.command == "tasks"
    assert args.screenshot_dir == Path("shots")


def test_default_profile_dir_uses_project_local_directory(tmp_path: Path) -> None:
    assert default_profile_dir(tmp_path) == tmp_path / ".browser-profile"


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
    assert cli.main(["tasks"]) == 0
    assert calls == [BING_URL, "tasks"]
