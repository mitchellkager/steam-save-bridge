"""Tests for the sync engine.

Uses fake accounts/clouds (no network). The critical test here is
`test_failed_upload_is_retried_next_pass`, which pins the intended retry
behavior for failed uploads.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from steam_save_bridge.cloud import CloudSave
from steam_save_bridge.notify import NopNotifier
from steam_save_bridge.rules import Rules
from steam_save_bridge.sync import SyncEngine


def S(app_id: int, path: str, ts: int, size: int, checksum: str) -> CloudSave:
    return CloudSave(app_id=app_id, path=path, timestamp=ts, size=size,
                     checksum=checksum)


class FakeCloud:
    """In-memory stand-in for CloudClient."""

    def __init__(self, saves: list[CloudSave]):
        self._saves = saves
        self.uploads: list[tuple[int, str, bytes]] = []
        self.raise_on_upload = False

    def list_saves(self, app_id: int) -> list[CloudSave]:
        return [s for s in self._saves if s.app_id == app_id]

    def list_app_ids(self) -> list[int]:
        return sorted({s.app_id for s in self._saves})

    def get_save_file(self, save: CloudSave) -> bytes:
        return b"DATA:" + save.path.encode()

    def upload_save(self, app_id: int, path: str, data: bytes) -> dict:
        if self.raise_on_upload:
            raise RuntimeError("simulated upload failure")
        self.uploads.append((app_id, path, data))
        return {"ok": True}


class FakeAccount:
    """In-memory stand-in for SteamAccount (exposes a fake cloud_client)."""

    def __init__(self, name: str, saves: list[CloudSave]):
        self.name = name
        self.cloud = FakeCloud(saves)

    @property
    def cloud_client(self):
        return self.cloud


class RecordingNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def notify(self, kind: str, message: str) -> None:
        self.messages.append(f"{kind}: {message}")


def make_engine(source, target, rules=None, notifier=None, state_path=None):
    return SyncEngine(source, target, rules or Rules(),
                      notifier or NopNotifier(), state_path=state_path)


def test_new_save_is_synced_and_notified(tmp_path: Path):
    source = FakeAccount("alice", [S(7, "slot1.save", 100, 10, "abc")])
    target = FakeAccount("bob", [])
    notifier = RecordingNotifier()
    engine = make_engine(source, target, notifier=notifier,
                        state_path=tmp_path / "state.json")
    engine.run_sync()
    assert source.name == "alice"
    assert target.cloud.uploads == [(7, "slot1.save", b"DATA:slot1.save")]
    assert any("new cloud save" in m for m in notifier.messages)

    # Second pass: unchanged, so nothing new.
    engine.run_sync()
    assert len(target.cloud.uploads) == 1
    assert not any("new cloud save" in m for m in notifier.messages[1:])


def test_unchanged_save_is_not_resynced(tmp_path: Path):
    source = FakeAccount("alice", [S(7, "slot1.save", 100, 10, "abc")])
    target = FakeAccount("bob", [])
    engine = make_engine(source, target, state_path=tmp_path / "state.json")
    engine.run_sync()
    engine.run_sync()
    engine.run_sync()
    # Only ever synced once, despite three passes.
    assert len(target.cloud.uploads) == 1


def test_changed_save_resyncs(tmp_path: Path):
    save = S(7, "slot1.save", 100, 10, "abc")
    source = FakeAccount("alice", [save])
    target = FakeAccount("bob", [])
    engine = make_engine(source, target, state_path=tmp_path / "state.json")
    engine.run_sync()
    assert len(target.cloud.uploads) == 1

    # The source save changes (new timestamp + checksum) -> re-synced.
    source.cloud._saves[0] = S(7, "slot1.save", 200, 11, "xyz")
    engine.run_sync()
    assert len(target.cloud.uploads) == 2
    assert target.cloud.uploads[1] == (7, "slot1.save", b"DATA:slot1.save")


def test_failed_upload_is_retried_next_pass(tmp_path: Path):
    """A save whose upload fails must be retried on the next pass.

    This is the behavior the code comment promises. If `record_seen` records
    the fingerprint of a *failed* save, the next pass's `detect_new` skips it
    and the save is silently dropped — this test pins the correct behavior.
    """
    source = FakeAccount("alice", [S(7, "slot1.save", 100, 10, "abc")])
    target = FakeAccount("bob", [])
    target.cloud.raise_on_upload = True
    engine = make_engine(source, target, state_path=tmp_path / "state.json")
    engine.run_sync()  # upload fails
    assert target.cloud.uploads == []

    # Fix the failure; the same save must now be picked up and synced.
    target.cloud.raise_on_upload = False
    engine.run_sync()
    assert target.cloud.uploads == [(7, "slot1.save", b"DATA:slot1.save")]


def test_rules_skip_blocks_sync(tmp_path: Path):
    source = FakeAccount("alice", [S(7, "slot1.save", 100, 10, "abc")])
    target = FakeAccount("bob", [])
    notifier = RecordingNotifier()
    rules = Rules(default="skip")  # skip everything
    engine = make_engine(source, target, rules=rules, notifier=notifier,
                        state_path=tmp_path / "state.json")
    engine.run_sync()
    assert target.cloud.uploads == []
    assert any("skipped" in m for m in notifier.messages)


def test_rules_allow_with_default_skip(tmp_path: Path):
    source = FakeAccount("alice", [
        S(7, "a.save", 100, 10, "abc"),
        S(8, "b.save", 100, 10, "def"),
    ])
    target = FakeAccount("bob", [])
    rules = Rules(default="skip", allow={7})
    engine = make_engine(source, target, rules=rules,
                        state_path=tmp_path / "state.json")
    engine.run_sync()
    # Only app 7 is synced; app 8 is skipped.
    assert [u[0] for u in target.cloud.uploads] == [7]


def test_multiple_saves_synced(tmp_path: Path):
    source = FakeAccount("alice", [
        S(7, "a.save", 100, 10, "abc"),
        S(7, "b.save", 100, 10, "def"),
        S(8, "c.save", 100, 10, "ghi"),
    ])
    target = FakeAccount("bob", [])
    engine = make_engine(source, target, state_path=tmp_path / "state.json")
    engine.run_sync()
    assert len(target.cloud.uploads) == 3


def test_state_persistence_across_engines(tmp_path: Path):
    state = tmp_path / "state.json"
    source = FakeAccount("alice", [S(7, "slot1.save", 100, 10, "abc")])
    target = FakeAccount("bob", [])
    engine1 = make_engine(source, target, state_path=state)
    engine1.run_sync()
    assert len(target.cloud.uploads) == 1

    # A fresh engine (e.g. after a daemon restart) must not re-sync an
    # unchanged save: it reads the persisted state.
    engine2 = make_engine(source, target, state_path=state)
    engine2.run_sync()
    assert len(target.cloud.uploads) == 1
    assert state.exists()


def test_notification_failure_does_not_break_sync(tmp_path: Path):
    class BoomNotifier:
        def notify(self, kind: str, message: str) -> None:
            raise RuntimeError("notifier exploded")

    source = FakeAccount("alice", [S(7, "slot1.save", 100, 10, "abc")])
    target = FakeAccount("bob", [])
    engine = make_engine(source, target, notifier=BoomNotifier(),
                        state_path=tmp_path / "state.json")
    # Must not raise, even though the notifier does.
    engine.run_sync()
    assert target.cloud.uploads == [(7, "slot1.save", b"DATA:slot1.save")]
