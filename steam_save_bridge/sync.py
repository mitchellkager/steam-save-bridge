"""The P0 sync loop: poll → detect new save → duplicate to other account → notify.

This is the heart of the bridge. It runs a *one-way* duplicate from a
``source`` account to a ``target`` account for each app id the rules allow,
using the per-account :class:`~steam_save_bridge.cloud.CloudClient`s.

"New" detection
---------------
Each account keeps a small on-disk state file mapping
``(app_id, path) -> (checksum, timestamp, size)``. A save is "new" (or changed)
when its checksum is not in the last-seen state for that account. The state
file persists across daemon restarts so a restart does not re-copy everything.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .auth import SteamAccount
from .cloud import CloudClient, CloudSave
from .notify import Notifier, NopNotifier
from .rules import Rules

logger = logging.getLogger(__name__)

StateKey = Tuple[int, str]  # (app_id, path)


@dataclass
class SyncResult:
    """Outcome of one :meth:`SyncEngine.sync` pass."""

    app_id: int
    detected: List[CloudSave] = field(default_factory=list)
    synced: List[CloudSave] = field(default_factory=list)
    skipped: List[CloudSave] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class SyncEngine:
    """One-way cross-account cloud-save duplicator."""

    def __init__(
        self,
        source: SteamAccount,
        target: SteamAccount,
        rules: Rules,
        notifier: Optional[Notifier] = None,
        state_path: str = "sync_state.json",
    ) -> None:
        self.source = source
        self.target = target
        self.source_cloud = CloudClient(source)
        self.target_cloud = CloudClient(target)
        self.rules = rules
        self.notifier = notifier or NopNotifier()
        self.state_path = state_path
        self.state = self._load_state()

    # ------------------------------------------------------------------ #
    # state persistence
    # ------------------------------------------------------------------ #
    def _load_state(self) -> Dict[str, Dict]:
        if not os.path.exists(self.state_path):
            return {}
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (ValueError, OSError) as exc:
            logger.warning("could not load state %s: %s", self.state_path, exc)
            return {}

    def _save_state(self) -> None:
        try:
            with open(self.state_path, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh, indent=2, sort_keys=True)
        except OSError as exc:
            logger.warning("could not save state %s: %s", self.state_path, exc)

    def _state_key(self, app_id: int, path: str) -> str:
        return f"{int(app_id)}:{path}"

    # ------------------------------------------------------------------ #
    # detection
    # ------------------------------------------------------------------ #
    def detect_new(self, app_id: int) -> Tuple[List[CloudSave], List[CloudSave]]:
        """Return ``(all_saves, new_saves)`` for ``app_id`` on the *source*.

        ``new_saves`` are those whose fingerprint differs from the last-seen
        state. A single API call is made; the caller records state for
        ``all_saves`` so a failed upload is retried (not re-notified) next pass.
        """
        seen = self.state.setdefault(f"source:{app_id}", {})
        all_saves = self.source_cloud.list_saves(app_id)
        new: List[CloudSave] = []
        for save in all_saves:
            key = self._state_key(app_id, save.path)
            fingerprint = (save.checksum, save.timestamp, save.size)
            if seen.get(key) != fingerprint:
                new.append(save)
        return all_saves, new

    def record_seen(self, app_id: int, saves: List[CloudSave]) -> None:
        """Record the fingerprint of every save we saw this pass."""
        seen = self.state.setdefault(f"source:{app_id}", {})
        for save in saves:
            seen[self._state_key(app_id, save.path)] = (
                save.checksum,
                save.timestamp,
                save.size,
            )
        self._save_state()

    # ------------------------------------------------------------------ #
    # the P0 loop
    # ------------------------------------------------------------------ #
    def sync_app(self, app_id: int) -> SyncResult:
        """Run one full pass for ``app_id``: detect → duplicate → notify."""
        result = SyncResult(app_id=app_id)

        if not self.rules.should_sync(app_id):
            logger.info("app %s (%s) skipped by rules", app_id, self.rules.label(app_id))
            return result

        label = self.rules.label(app_id)
        all_saves, detected = self.detect_new(app_id)
        result.detected = detected

        for save in detected:
            # Notify on "new cloud save" (P0), independent of sync success.
            self.notifier.notify(
                "new_cloud_save",
                {
                    "account": self.source.name,
                    "app_id": app_id,
                    "game": label,
                    "path": save.path,
                    "size": save.size,
                },
            )
            try:
                data = self.source_cloud.get_save_file(save)
                self.target_cloud.upload_save(app_id, save.path, data)
                result.synced.append(save)
                self.notifier.notify(
                    "save_synced",
                    {
                        "from": self.source.name,
                        "to": self.target.name,
                        "app_id": app_id,
                        "game": label,
                        "path": save.path,
                        "size": save.size,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - one save failing must not kill the pass
                logger.error("sync failed for %s: %s", save.path, exc)
                result.errors.append(f"{save.path}: {exc}")

        # Record everything we *saw* (not just what synced) so a failed upload
        # is retried next pass rather than re-notified as "new" forever.
        self.record_seen(app_id, all_saves)
        return result

    def sync_all(self, app_ids: List[int]) -> List[SyncResult]:
        """Run :meth:`sync_app` for each app id in order."""
        return [self.sync_app(app_id) for app_id in app_ids]


__all__ = ["SyncEngine", "SyncResult"]
