from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path


class ConfigError(Exception):
    """Raised when persisted config/state is unreadable (corrupted JSON, wrong shape)."""


def _config_dir() -> Path:
    # Read lazily (not frozen at import time) so tests can point MANUS_CONFIG_DIR
    # at a temp dir without ever touching the real ~/.config/manus.
    return Path(os.environ.get("MANUS_CONFIG_DIR", Path.home() / ".config" / "manus"))


def _credentials_file() -> Path:
    return _config_dir() / "credentials.json"


def _state_file() -> Path:
    return _config_dir() / "state.json"


# Kept as module attributes too (read-only convenience for callers like `manus doctor`
# that just want to display the path) — always resolved fresh, never cached.
class _ConfigDirProxy:
    def __fspath__(self) -> str:
        return str(_config_dir())

    def __str__(self) -> str:
        return str(_config_dir())


CONFIG_DIR = _ConfigDirProxy()


def _atomic_write(path: Path, data: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        raise ConfigError(f"{path} está corrompido ou ilegível: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path} tem formato inesperado (esperava um objeto JSON)")
    return data


def save_api_key(api_key: str) -> None:
    _atomic_write(_credentials_file(), {"api_key": api_key})


def load_api_key() -> str | None:
    env_key = os.environ.get("MANUS_API_KEY")
    if env_key:
        return env_key
    return _read_json(_credentials_file()).get("api_key")


def mask(api_key: str) -> str:
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:6]}...{api_key[-4:]}"


def _load_state() -> dict:
    return _read_json(_state_file())


def _save_state(state: dict) -> None:
    _atomic_write(_state_file(), state)


def save_last_task(task_id: str) -> None:
    state = _load_state()
    state["last_task_id"] = task_id
    _save_state(state)


def load_last_task() -> str | None:
    return _load_state().get("last_task_id")


def save_alias(name: str, task_id: str) -> None:
    state = _load_state()
    state.setdefault("aliases", {})[name] = task_id
    _save_state(state)


def load_aliases() -> dict:
    aliases = _load_state().get("aliases", {})
    return aliases if isinstance(aliases, dict) else {}


def resolve_alias(name: str) -> str | None:
    return load_aliases().get(name)


def save_connector_alias(name: str, connector_id: str) -> None:
    state = _load_state()
    state.setdefault("connector_aliases", {})[name] = connector_id
    _save_state(state)


def load_connector_aliases() -> dict:
    aliases = _load_state().get("connector_aliases", {})
    return aliases if isinstance(aliases, dict) else {}


_MANUSRC_KNOWN_KEYS = {"task_id", "connectors", "connector_names", "agent_profile"}


def load_project_rc(cwd: Path | None = None) -> dict:
    rc_path = (cwd or Path.cwd()) / ".manusrc"
    if not rc_path.exists():
        return {}
    try:
        data = json.loads(rc_path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        raise ConfigError(f"{rc_path} está corrompido ou ilegível: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{rc_path} deve conter um objeto JSON")
    unknown = set(data) - _MANUSRC_KNOWN_KEYS
    if unknown:
        raise ConfigError(f"{rc_path} tem campo(s) desconhecido(s): {', '.join(sorted(unknown))}")
    if "task_id" in data and not isinstance(data["task_id"], str):
        raise ConfigError(f"{rc_path}: 'task_id' deve ser string")
    if "connectors" in data and not (
        isinstance(data["connectors"], list) and all(isinstance(c, str) for c in data["connectors"])
    ):
        raise ConfigError(f"{rc_path}: 'connectors' deve ser uma lista de strings (UUIDs)")
    if "connector_names" in data and not (
        isinstance(data["connector_names"], list) and all(isinstance(c, str) for c in data["connector_names"])
    ):
        raise ConfigError(f"{rc_path}: 'connector_names' deve ser uma lista de strings")
    return data
