"""Tests for the notification backends.

The core contract: a notification failure must NEVER raise, so a broken
webhook or script cannot take down the sync loop.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest
import responses

from steam_save_bridge.notify import DiscordNotifier, NopNotifier, ScriptNotifier


def test_nop_notifier_swallows():
    NopNotifier().notify("info", {"message": "hello"})  # must not raise


def test_discord_notifier_posts_payload():
    n = DiscordNotifier("https://discord.example/webhook")
    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, "https://discord.example/webhook",
                 json={"ok": True}, status=204)
        n.notify("synced", {"game": "Game A", "path": "7/save"})
        assert len(rsps.calls) == 1
        body = json.loads(rsps.calls[0].request.body)
        assert body["content"].startswith("**synced**")
        assert "game: Game A" in body["content"]
        assert "path: 7/save" in body["content"]


def test_discord_notifier_swallows_network_errors():
    n = DiscordNotifier("https://discord.example/webhook")
    with mock.patch("steam_save_bridge.notify.requests.post",
                   side_effect=ConnectionError("network down")):
        n.notify("info", {"message": "hello"})  # must not raise


def test_discord_notifier_swallows_http_errors():
    n = DiscordNotifier("https://discord.example/webhook")
    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, "https://discord.example/webhook",
                 status=500)
        n.notify("info", {"message": "hello"})  # must not raise


def test_script_notifier_runs_script(tmp_path):
    log = tmp_path / "out.txt"
    script = tmp_path / "notify.sh"
    script.write_text(f"#!/bin/sh\ncat > '{log}'\n")
    script.chmod(0o755)
    ScriptNotifier(str(script)).notify("synced", {"game": "Game A"})
    body = json.loads(log.read_text())
    assert body["event"] == "synced"
    assert body["game"] == "Game A"


def test_script_notifier_swallows_errors(tmp_path):
    script = tmp_path / "fail.sh"
    script.write_text("#!/bin/sh\nexit 1\n")
    script.chmod(0o755)
    ScriptNotifier(str(script)).notify("info", {"message": "hello"})  # must not raise
