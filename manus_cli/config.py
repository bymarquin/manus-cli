from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("MANUS_CONFIG_DIR", Path.home() / ".config" / "manus"))
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
STATE_FILE = CONFIG_DIR / "state.json"


def save_api_key(api_key: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(json.dumps({"api_key": api_key}))
    CREDENTIALS_FILE.chmod(0o600)


def load_api_key() -> str | None:
    if not CREDENTIALS_FILE.exists():
        return None
    return json.loads(CREDENTIALS_FILE.read_text()).get("api_key")


def mask(api_key: str) -> str:
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:6]}...{api_key[-4:]}"


def save_last_task(task_id: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"last_task_id": task_id}))


def load_last_task() -> str | None:
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text()).get("last_task_id")


def load_project_rc(cwd: Path | None = None) -> dict:
    rc_path = (cwd or Path.cwd()) / ".manusrc"
    if not rc_path.exists():
        return {}
    return json.loads(rc_path.read_text())
