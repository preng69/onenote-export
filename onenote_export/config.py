from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


APP_NAME = "onenote-export"
DEFAULT_AUTHORITY = "common"
DEFAULT_SCOPES = ["Notes.Read"]


def _default_config_dir() -> Path:
    override = os.environ.get("ONENOTE_EXPORT_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / APP_NAME


CONFIG_DIR = _default_config_dir()
CONFIG_PATH = CONFIG_DIR / "config.json"
TOKEN_CACHE_PATH = CONFIG_DIR / "token_cache.bin"
AUTH_STATE_PATH = CONFIG_DIR / "auth_state.json"


@dataclass
class AppConfig:
    client_id: str
    authority: str = DEFAULT_AUTHORITY
    scopes: list[str] = field(default_factory=lambda: DEFAULT_SCOPES.copy())
    port: int = 8400

    @property
    def authority_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.authority}"


@dataclass
class AuthState:
    username: str | None = None


def ensure_config_dir() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def _atomic_write_text(path: Path, content: str) -> None:
    ensure_config_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)
    path.chmod(0o600)


def save_config(config: AppConfig) -> Path:
    payload = json.dumps(asdict(config), indent=2, sort_keys=True)
    _atomic_write_text(CONFIG_PATH, payload + "\n")
    return CONFIG_PATH


def load_config() -> AppConfig | None:
    if not CONFIG_PATH.exists():
        return None
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return AppConfig(
        client_id=data["client_id"],
        authority=data.get("authority", DEFAULT_AUTHORITY),
        scopes=list(data.get("scopes", DEFAULT_SCOPES)),
        port=int(data.get("port", 8400)),
    )


def require_config() -> AppConfig:
    config = load_config()
    if not config:
        raise RuntimeError(
            "No saved configuration found. Run `onenote-export auth login --client-id YOUR_APP_ID` first."
        )
    return config


def save_auth_state(state: AuthState) -> Path:
    payload = json.dumps(asdict(state), indent=2, sort_keys=True)
    _atomic_write_text(AUTH_STATE_PATH, payload + "\n")
    return AUTH_STATE_PATH


def load_auth_state() -> AuthState:
    if not AUTH_STATE_PATH.exists():
        return AuthState()
    data: dict[str, Any] = json.loads(AUTH_STATE_PATH.read_text(encoding="utf-8"))
    return AuthState(username=data.get("username"))


def clear_auth_state() -> None:
    if AUTH_STATE_PATH.exists():
        AUTH_STATE_PATH.unlink()

