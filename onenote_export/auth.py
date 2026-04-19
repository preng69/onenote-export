from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msal

from .config import (
    AUTH_STATE_PATH,
    TOKEN_CACHE_PATH,
    AppConfig,
    AuthState,
    clear_auth_state,
    ensure_config_dir,
    load_auth_state,
    save_auth_state,
)


class AuthError(RuntimeError):
    pass


@dataclass
class LoginResult:
    username: str | None
    scopes: list[str]


class AuthManager:
    def __init__(self, config: AppConfig):
        self.config = config
        self.cache = msal.SerializableTokenCache()
        self.state = load_auth_state()
        self._load_cache(TOKEN_CACHE_PATH)
        self.app = msal.PublicClientApplication(
            client_id=config.client_id,
            authority=config.authority_url,
            token_cache=self.cache,
        )

    def _load_cache(self, path: Path) -> None:
        if path.exists():
            self.cache.deserialize(path.read_text(encoding="utf-8"))

    def _save_cache(self) -> None:
        if not self.cache.has_state_changed:
            return
        ensure_config_dir()
        TOKEN_CACHE_PATH.write_text(self.cache.serialize(), encoding="utf-8")
        TOKEN_CACHE_PATH.chmod(0o600)

    def _pick_accounts(self) -> list[dict[str, Any]]:
        if self.state.username:
            matching = self.app.get_accounts(username=self.state.username)
            if matching:
                return matching
        return self.app.get_accounts()

    def login_interactive(self, login_hint: str | None = None, timeout: int = 300) -> LoginResult:
        def on_before_launching_ui(ui: str = "browser", **_: Any) -> None:
            print("Opening the browser for Microsoft sign-in...")

        result = self.app.acquire_token_interactive(
            scopes=self.config.scopes,
            port=self.config.port,
            timeout=timeout,
            prompt="select_account",
            login_hint=login_hint,
            on_before_launching_ui=on_before_launching_ui,
        )
        if "access_token" not in result:
            details = result.get("error_description") or result.get("error") or json.dumps(result)
            raise AuthError(f"Interactive login failed: {details}")

        username = _extract_username(result) or login_hint
        self.state = AuthState(username=username)
        save_auth_state(self.state)
        self._save_cache()
        return LoginResult(username=username, scopes=result.get("scope", "").split())

    def get_access_token(self, force_refresh: bool = False) -> str:
        accounts = self._pick_accounts()
        if not accounts:
            raise AuthError("No cached Microsoft account found. Run `onenote-export auth login` first.")

        errors: list[str] = []
        for account in accounts:
            result = self.app.acquire_token_silent(
                scopes=self.config.scopes,
                account=account,
                force_refresh=force_refresh,
            )
            if result and "access_token" in result:
                self._save_cache()
                username = account.get("username") or self.state.username
                if username and username != self.state.username:
                    self.state = AuthState(username=username)
                    save_auth_state(self.state)
                return result["access_token"]
            if result and "error" in result:
                errors.append(result.get("error_description") or result["error"])

        if force_refresh:
            message = "; ".join(errors) if errors else "token refresh failed"
            raise AuthError(f"Unable to refresh the cached token: {message}")
        raise AuthError("No valid cached token found. Run `onenote-export auth login` again.")

    def status(self) -> dict[str, Any]:
        accounts = self._pick_accounts()
        return {
            "configured_client_id": self.config.client_id,
            "authority": self.config.authority,
            "scopes": self.config.scopes,
            "port": self.config.port,
            "username": self.state.username,
            "cached_accounts": [account.get("username") for account in accounts],
            "cache_path": str(TOKEN_CACHE_PATH),
            "state_path": str(AUTH_STATE_PATH),
        }

    def logout(self) -> None:
        if TOKEN_CACHE_PATH.exists():
            TOKEN_CACHE_PATH.unlink()
        clear_auth_state()
        self.cache = msal.SerializableTokenCache()
        self.state = AuthState()


def _extract_username(result: dict[str, Any]) -> str | None:
    claims = result.get("id_token_claims") or {}
    for key in ("preferred_username", "email", "upn", "unique_name"):
        value = claims.get(key)
        if value:
            return str(value)
    return None

