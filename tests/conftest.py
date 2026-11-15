"""Shared fixtures and lightweight fakes.

The fakes let the sync/rule/notify tests run with no network and no real
Steam session, so the P0 loop is testable in CI and locally.
"""
from __future__ import annotations

import requests

import pytest

from steam_save_bridge.cloud import CloudSave


class FakeAccount:
    """Duck-typed stand-in for ``SteamAccount``.

    Provides the three attributes ``CloudClient`` reads: ``name``,
    ``steam_id`` and a ``requests.Session`` (so the real HTTP code path can be
    exercised under ``responses`` in the cloud tests).
    """

    def __init__(self, name: str = "acct", steam_id: str = "76563000000000000",
                 api_base: str = "https://api.test"):
        self.name = name
        self._steam_id = steam_id
        self.api_base = api_base
        self.session = requests.Session()

    @property
    def steam_id(self) -> str:
        return self._steam_id


class FakeCloud:
    """Controllable stand-in for ``CloudClient`` (injected into SyncEngine)."""

    def __init__(self, saves_by_app: dict | None = None,
                 upload_side_effect=None, file_bytes: bytes = b"SAVE-DATA"):
        self.saves_by_app = saves_by_app or {}
        self.upload_side_effect = upload_side_effect
        self.file_bytes = file_bytes
        self.uploads: list[tuple] = []  # (app_id, path, data)

    def list_saves(self, app_id: int) -> list[CloudSave]:
        return list(self.saves_by_app.get(int(app_id), []))

    def get_save_file(self, save: CloudSave) -> bytes:
        return self.file_bytes

    def upload_save(self, app_id: int, path: str, data: bytes) -> dict:
        self.uploads.append((int(app_id), path, data))
        if self.upload_side_effect is not None:
            self.upload_side_effect()
        return {"ok": True}


def make_save(app_id: int, path: str = "slot1.save", *,
              checksum: str = "abc123", timestamp: int = 1000,
              size: int = 5) -> CloudSave:
    return CloudSave(app_id=app_id, name=path, path=path,
                     checksum=checksum, timestamp=timestamp, size=size)


@pytest.fixture
def source_account() -> FakeAccount:
    return FakeAccount(name="source", steam_id="76563000000000001")


@pytest.fixture
def target_account() -> FakeAccount:
    return FakeAccount(name="target", steam_id="76563000000000002")
