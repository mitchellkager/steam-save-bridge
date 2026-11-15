"""Tests for the per-game rule engine (P0: opt-in/opt-out with global rules)."""
from __future__ import annotations

from steam_save_bridge.rules import Rules


def test_default_sync_covers_unlisted_games():
    rules = Rules(default="sync")
    assert rules.should_sync(12345)


def test_default_skip_blocks_unlisted_games():
    rules = Rules(default="skip")
    assert not rules.should_sync(12345)


def test_allow_overrides_default_skip():
    rules = Rules(default="skip", allow=[12345])
    assert rules.should_sync(12345)
    assert not rules.should_sync(99999)


def test_deny_overrides_default_sync():
    rules = Rules(default="sync", deny=[12345])
    assert not rules.should_sync(12345)
    assert rules.should_sync(99999)


def test_deny_wins_over_allow():
    rules = Rules(default="sync", allow=[12345], deny=[12345])
    assert not rules.should_sync(12345)


def test_from_dict_round_trips():
    d = {"default": "skip", "allow": [1, 2], "deny": [3],
         "names": {"1": "Game A", "3": "Game C"}}
    rules = Rules.from_dict(d)
    assert rules.default == "skip"
    assert rules.allow == [1, 2]
    assert rules.deny == [3]
    assert rules.label(1) == "Game A"
    # label falls back to the app id for unnamed games
    assert rules.label(99999) == "99999"


def test_from_dict_defaults_to_sync_when_absent():
    rules = Rules.from_dict({})
    assert rules.default == "sync"
    assert rules.allow == []


def test_from_dict_rejects_bad_default():
    import pytest
    with pytest.raises(ValueError):
        Rules.from_dict({"default": "bogus"})
