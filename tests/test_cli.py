from pathlib import Path
from contextlib import contextmanager


import bing_rewardd.cli as cli
from bing_rewardd.browser import BING_URL, default_storage_state_path
from bing_rewardd.cli import build_parser


def test_parser_accepts_no_wait() -> None:
    args = build_parser().parse_args(["--no-wait", "tasks"])

    assert args.command == "tasks"
    assert args.no_wait is True


def test_parser_accepts_screenshot_dir() -> None:
    args = build_parser().parse_args(["--screenshot-dir", "shots", "tasks"])

    assert args.command == "tasks"
    assert args.screenshot_dir == Path("shots")


def test_parser_accepts_storage_state_options() -> None:
    args = build_parser().parse_args(
        ["--storage-state", "in.json", "--save-storage-state", "out.json", "tasks"]
    )

    assert args.storage_state == Path("in.json")
    assert args.save_storage_state == Path("out.json")


def test_default_storage_state_path_uses_project_local_file(tmp_path: Path) -> None:
    assert default_storage_state_path(tmp_path) == tmp_path / "storage_state.json"


def test_tasks_command_opens_bing_before_guiding_tasks(monkeypatch) -> None:
    calls: list[str] = []

    class FakeContext:
        def storage_state(self, *, path: str) -> None:
            calls.append(f"save:{path}")

    @contextmanager
    def fake_launch_persistent_browser(config):
        yield FakeContext()

    def fake_open_url(context, url: str):
        calls.append(url)
        return object()

    def fake_guide_tasks(page):
        calls.append("tasks")

    monkeypatch.setattr(cli, "launch_browser", fake_launch_persistent_browser)
    monkeypatch.setattr(cli, "open_url", fake_open_url)
    monkeypatch.setattr(cli, "guide_tasks", fake_guide_tasks)
    assert cli.main(["tasks"]) == 0
    assert calls == [BING_URL, "tasks", f"save:{default_storage_state_path()}"]
