from pathlib import Path

import json

import pytest

from bing_rewardd.config import load_credentials


def test_load_credentials_returns_none_when_file_missing(tmp_path: Path) -> None:
    result = load_credentials(tmp_path)
    assert result is None


def test_load_credentials_returns_none_when_file_invalid_json(tmp_path: Path) -> None:
    (tmp_path / ".credentials.json").write_text("not json")
    result = load_credentials(tmp_path)
    assert result is None


def test_load_credentials_returns_none_when_no_email(tmp_path: Path) -> None:
    (tmp_path / ".credentials.json").write_text(json.dumps({"password": "secret"}))
    result = load_credentials(tmp_path)
    assert result is None


def test_load_credentials_returns_none_when_no_password(tmp_path: Path) -> None:
    (tmp_path / ".credentials.json").write_text(json.dumps({"email": "a@b.com"}))
    result = load_credentials(tmp_path)
    assert result is None


def test_load_credentials_returns_none_when_not_a_dict(tmp_path: Path) -> None:
    (tmp_path / ".credentials.json").write_text(json.dumps(["list"]))
    result = load_credentials(tmp_path)
    assert result is None


def test_load_credentials_returns_dict_when_valid(tmp_path: Path) -> None:
    data = {"email": "user@example.com", "password": "mypassword123"}
    (tmp_path / ".credentials.json").write_text(json.dumps(data))
    result = load_credentials(tmp_path)
    assert result == data
