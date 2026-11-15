"""Notification backends for "new cloud save" and sync events.

P0 requirement: "get notifications on new cloud save". This module provides a
small, pluggable notifier set in the same spirit as scsd's ``notifier.py``:

* :class:`NopNotifier`    — log-only (default, always safe).
* :class:`DiscordNotifier` — POST a message to a Discord webhook URL.
* :class:`ScriptNotifier`  — run a user-supplied command with a JSON payload on
  stdin (escape hatch for any other channel: email, push, home-assistant, ...).

All notifiers implement :meth:`Notifier.notify`, which is a no-op on failure
(notifications must never take down the sync loop).
"""

from __future__ import annotations

import json
import logging
import subprocess
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class Notifier(ABC):
    """Base notifier. Subclasses implement :meth:`notify`."""

    @abstractmethod
    def notify(self, event: str, payload: Dict[str, Any]) -> None:
        """Deliver ``event`` (a short label) with ``payload`` (JSON-serialisable)."""


class NopNotifier(Notifier):
    """Log-only notifier (the default)."""

    def notify(self, event: str, payload: Dict[str, Any]) -> None:
        logger.info("notify[%s] %s", event, json.dumps(payload, default=str))


class DiscordNotifier(Notifier):
    """POST a message to a Discord webhook URL."""

    def __init__(self, webhook_url: str, timeout: float = 10.0) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def notify(self, event: str, payload: Dict[str, Any]) -> None:
        try:
            content = f"**{event}**\n" + "\n".join(
                f"- {k}: {v}" for k, v in payload.items()
            )
            resp = requests.post(
                self.webhook_url,
                json={"content": content},
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                logger.warning(
                    "discord webhook failed (HTTP %s): %s",
                    resp.status_code,
                    resp.text[:200],
                )
        except Exception as exc:  # noqa: BLE001 - never raise from notify()
            logger.warning("discord webhook error: %s", exc)


class ScriptNotifier(Notifier):
    """Run ``command`` with the JSON payload on stdin."""

    def __init__(self, command: str, timeout: float = 30.0) -> None:
        self.command = command
        self.timeout = timeout

    def notify(self, event: str, payload: Dict[str, Any]) -> None:
        try:
            body = json.dumps({"event": event, **payload}, default=str)
            subprocess.run(
                self.command,
                input=body.encode("utf-8"),
                timeout=self.timeout,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001 - never raise from notify()
            logger.warning("script notifier error: %s", exc)


def make_notifier(
    kind: str = "nop",
    webhook_url: Optional[str] = None,
    command: Optional[str] = None,
) -> Notifier:
    """Factory for notifiers from a config ``kind``."""
    kind = (kind or "nop").lower()
    if kind in ("", "nop", "log", "none"):
        return NopNotifier()
    if kind == "discord":
        if not webhook_url:
            raise ValueError("discord notifier requires webhook_url")
        return DiscordNotifier(webhook_url)
    if kind == "script":
        if not command:
            raise ValueError("script notifier requires command")
        return ScriptNotifier(command)
    raise ValueError(f"unknown notifier kind: {kind!r}")


__all__ = [
    "Notifier",
    "NopNotifier",
    "DiscordNotifier",
    "ScriptNotifier",
    "make_notifier",
]
