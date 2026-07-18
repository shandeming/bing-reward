from __future__ import annotations

from pathlib import Path
from typing import Any

import json


def load_credentials(base_dir: Path | None = None) -> dict[str, str] | None:
    """Load email/password from project-local .credentials.json.

    Returns None if the file does not exist or is invalid.
    """
    root = base_dir or Path.cwd()
    credentials_path = root / ".credentials.json"

    if not credentials_path.exists():
        return None

    try:
        with open(credentials_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(data, dict):
        return None

    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return None

    return {"email": email, "password": password}
