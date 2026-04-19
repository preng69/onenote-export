from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable

import requests

from .auth import AuthError, AuthManager


GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class GraphError(RuntimeError):
    pass


@dataclass
class DownloadedResource:
    data: bytes
    mime_type: str | None


class GraphClient:
    def __init__(self, auth: AuthManager):
        self.auth = auth
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "onenote-export/0.1.0"})

    def close(self) -> None:
        self.session.close()

    def list_notebooks(self) -> list[dict[str, Any]]:
        return list(self._iter_collection("/me/onenote/notebooks", params={"$top": "100"}))

    def list_section_groups(self) -> list[dict[str, Any]]:
        return list(
            self._iter_collection(
                "/me/onenote/sectionGroups",
                params={"$expand": "parentNotebook,parentSectionGroup", "$top": "100"},
            )
        )

    def list_sections(self) -> list[dict[str, Any]]:
        return list(
            self._iter_collection(
                "/me/onenote/sections",
                params={"$expand": "parentNotebook,parentSectionGroup", "$top": "100"},
            )
        )

    def list_pages_in_section(self, section_id: str) -> list[dict[str, Any]]:
        return list(
            self._iter_collection(
                f"/me/onenote/sections/{section_id}/pages",
                params={"pagelevel": "true", "$top": "100"},
            )
        )

    def get_page_content(self, page_id: str) -> str:
        response = self._request(
            "GET",
            f"/me/onenote/pages/{page_id}/content",
            headers={"Accept": "text/html"},
        )
        return response.text

    def download_resource(self, url: str) -> DownloadedResource:
        response = self._request("GET", url, stream=False)
        return DownloadedResource(
            data=response.content,
            mime_type=response.headers.get("Content-Type"),
        )

    def _iter_collection(self, url: str, params: dict[str, str] | None = None) -> Iterable[dict[str, Any]]:
        next_url = url
        next_params = params
        while next_url:
            payload = self._request_json("GET", next_url, params=next_params)
            for item in payload.get("value", []):
                yield item
            next_url = payload.get("@odata.nextLink")
            next_params = None

    def _request_json(
        self, method: str, url: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        response = self._request(method, url, params=params, headers={"Accept": "application/json"})
        try:
            return response.json()
        except ValueError as exc:
            raise GraphError(f"Invalid JSON response from Microsoft Graph: {exc}") from exc

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        stream: bool = False,
    ) -> requests.Response:
        request_headers = dict(headers or {})
        request_headers["Authorization"] = f"Bearer {self.auth.get_access_token(force_refresh=False)}"

        target_url = url if url.startswith("https://") else GRAPH_BASE_URL + url
        attempts = 3
        retried_auth = False

        for attempt in range(1, attempts + 1):
            response = self.session.request(
                method=method,
                url=target_url,
                params=params,
                headers=request_headers,
                timeout=60,
                stream=stream,
            )
            if response.status_code == 401 and not retried_auth:
                retried_auth = True
                request_headers["Authorization"] = f"Bearer {self.auth.get_access_token(force_refresh=True)}"
                continue
            if response.status_code in {429, 503, 504} and attempt < attempts:
                delay = float(response.headers.get("Retry-After", "2"))
                time.sleep(delay)
                continue
            if response.ok:
                return response

            detail = _response_detail(response)
            if response.status_code == 401:
                raise AuthError(detail)
            raise GraphError(f"Microsoft Graph request failed ({response.status_code}): {detail}")

        raise GraphError("Microsoft Graph request exhausted retries without a response.")


def _response_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or response.reason
    error = payload.get("error") or {}
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or payload)
    return str(payload)

