"""ICloudService JSON API client (read + write) over the web session cookie.

This is the hard part of the bridge: talking to Steam's cloud-save API for an
account whose session was established by the web login (see :mod:`auth`), not
the awkward OAuth ``access_token`` flow.

The client is deliberately thin and endpoint-configurable. The exact endpoint
paths/parameters for the write path (``BeginAppUploadBatch`` / ``UploadAppFile``
/ ``FinishAppUploadBatch``) are the part that needs live verification against a
real account — see ``CloudClient.probe()`` and the ``--probe`` CLI command,
which prints exactly what Steam returns so the parameters can be pinned down
without guesswork.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import requests

from .auth import SteamAccount

logger = logging.getLogger(__name__)

# Default ICloudService JSON API base. Overridable per client so the exact
# host/path can be pinned during live verification without code changes.
DEFAULT_API_BASE = "https://api.steampowered.com"


class CloudError(Exception):
    """Raised when a cloud API call fails or returns an error envelope."""


@dataclass
class CloudSave:
    """One cloud save file for an app."""

    app_id: int
    path: str
    size: int
    timestamp: int
    checksum: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, app_id: int, raw: Dict[str, Any]) -> "CloudSave":
        """Build a CloudSave from a raw API list entry (shape-agnostic)."""
        return cls(
            app_id=app_id,
            path=raw.get("path", raw.get("filename", "")),
            size=int(raw.get("size", raw.get("file_size", 0)) or 0),
            timestamp=int(raw.get("timestamp", raw.get("time_updated", 0)) or 0),
            checksum=raw.get("checksum", raw.get("sha", "")),
            metadata=raw,
        )


class CloudClient:
    """Read/write Steam Cloud saves for one account via the web session cookie."""

    def __init__(
        self,
        account: SteamAccount,
        api_base: str = DEFAULT_API_BASE,
        timeout: float = 30.0,
    ) -> None:
        self.account = account
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self._app_ids: Set[int] = set()

    # ------------------------------------------------------------------ #
    # low-level
    # ------------------------------------------------------------------ #
    def _headers(self) -> Dict[str, str]:
        return {
            "User-Agent": "steam-save-bridge/0.1 (+self-hosted)",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.api_base}{path}"
        resp = self.account.session.get(
            url, params=params, headers=self._headers(), timeout=self.timeout
        )
        return self._check(resp)

    def _post(self, path: str, json_body: Optional[Dict[str, Any]] = None,
              data: Optional[Dict[str, Any]] = None,
              files: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.api_base}{path}"
        resp = self.account.session.post(
            url,
            json=json_body,
            data=data,
            files=files,
            headers=self._headers(),
            timeout=self.timeout,
        )
        return self._check(resp)

    @staticmethod
    def _check(resp: requests.Response) -> Any:
        if resp.status_code in (401, 403):
            raise CloudError(
                f"auth rejected (HTTP {resp.status_code}); session may be stale — "
                "call account.refresh()"
            )
        if resp.status_code >= 400:
            raise CloudError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise CloudError(
                f"non-JSON response (first 200 chars): {resp.text[:200]}"
            ) from exc

    # ------------------------------------------------------------------ #
    # read path
    # ------------------------------------------------------------------ #
    def list_saves(self, app_id: int) -> List[CloudSave]:
        """List cloud saves for ``app_id``.

        Uses ``GetCloudSaveList``. The exact parameter name for the app id is
        pinned during live verification; both common spellings are tried.
        """
        for params in ({"appid": app_id}, {"app_id": app_id}):
            try:
                data = self._get(
                    "/ICloudService/GetCloudSaveList/v1/", params=params
                )
                break
            except CloudError as exc:
                logger.debug("GetCloudSaveList with %s failed: %s", params, exc)
                data = None

        if data is None:
            raise CloudError(
                "GetCloudSaveList failed for all parameter spellings; "
                "run --probe to inspect the raw response"
            )

        self._app_ids.add(app_id)
        return self._parse_save_list(app_id, data)

    @staticmethod
    def _parse_save_list(app_id: int, data: Any) -> List[CloudSave]:
        """Extract a list of CloudSave from a shape-agnostic API response."""
        raw_list: List[Dict[str, Any]] = []
        if isinstance(data, dict):
            # Common envelopes: {"response": {"files": [...]}} or
            # {"response": {"cloud_save_list": [...]}} or {"files": [...]}
            for key in ("files", "cloud_save_list", "saves", "list"):
                node = data.get("response", data).get(key) if isinstance(
                    data.get("response", data), dict
                ) else None
                if isinstance(node, list):
                    raw_list = node
                    break
            if not raw_list:
                # Fall back to any list-of-dicts value we can find.
                for value in _walk(data):
                    if isinstance(value, list) and value and all(
                        isinstance(v, dict) for v in value
                    ):
                        raw_list = value
                        break
        elif isinstance(data, list):
            raw_list = data

        return [CloudSave.from_api(app_id, r) for r in raw_list]

    def list_app_ids(self) -> List[int]:
        """App IDs seen so far (best-effort; configure explicitly for the daemon)."""
        return sorted(self._app_ids)

    def get_save_file(self, save: CloudSave) -> bytes:
        """Download the bytes of ``save`` via ``GetCloudSaveFile``."""
        params = {
            "appid": save.app_id,
            "path": save.path,
            "size": save.size,
        }
        resp = self.account.session.get(
            f"{self.api_base}/ICloudService/GetCloudSaveFile/v1/",
            params=params,
            headers=self._headers(),
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise CloudError(
                f"GetCloudSaveFile HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return resp.content

    # ------------------------------------------------------------------ #
    # write path
    # ------------------------------------------------------------------ #
    def upload_save(self, app_id: int, path: str, data: bytes) -> Dict[str, Any]:
        """Upload ``data`` to ``path`` for ``app_id``.

        Uses the three-step ``BeginAppUploadBatch`` / ``UploadAppFile`` /
        ``FinishAppUploadBatch`` flow. The exact request shapes need live
        verification; this implements the documented shape and surfaces the raw
        response so ``probe()`` / logs make any mismatch obvious.
        """
        batch = self._post(
            "/ICloudService/BeginAppUploadBatch/v1/",
            json_body={"appid": app_id, "files": [path]},
        )
        if not isinstance(batch, dict):
            raise CloudError(f"BeginAppUploadBatch unexpected: {batch!r}")

        batch_token = (
            batch.get("response", {}).get("upload_batch_token")
            or batch.get("upload_batch_token")
            or ""
        )

        files = {
            "file": (path.split("/")[-1], data, "application/octet-stream"),
        }
        self._post(
            "/ICloudService/UploadAppFile/v1/",
            data={
                "appid": app_id,
                "path": path,
                "upload_batch_token": batch_token,
            },
            files=files,
        )

        result = self._post(
            "/ICloudService/FinishAppUploadBatch/v1/",
            json_body={
                "appid": app_id,
                "upload_batch_token": batch_token,
            },
        )
        return result if isinstance(result, dict) else {"raw": result}

    # ------------------------------------------------------------------ #
    # verification helper
    # ------------------------------------------------------------------ #
    def probe(self, app_id: int = 7) -> Dict[str, Any]:
        """Probe the API and return a diagnostic dict.

        ``app_id=7`` is the app-id of the Steam client's own remote storage
        (the "remote" app), which every account has — a safe, always-present
        target to confirm the session cookie is accepted by the API.
        """
        out: Dict[str, Any] = {
            "account": self.account.name,
            "steam_id": self.account.steam_id,
            "api_base": self.api_base,
            "cookie_names": sorted(self.account.session.cookies.keys()),
        }
        try:
            out["list_saves"] = [
                {"path": s.path, "size": s.size} for s in self.list_saves(app_id)
            ]
        except CloudError as exc:
            out["list_saves_error"] = str(exc)
        return out


def _walk(node: Any):
    """Yield every list/dict value nested anywhere in ``node``."""
    if isinstance(node, dict):
        for value in node.values():
            yield value
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield item
            yield from _walk(item)


__all__ = [
    "CloudClient",
    "CloudSave",
    "CloudError",
    "DEFAULT_API_BASE",
]
