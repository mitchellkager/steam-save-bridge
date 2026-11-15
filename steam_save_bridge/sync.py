"""The sync loop: poll source, detect new saves, duplicate to target, notify.

P0 requirement: "automate cross-account sync for each new cloud save". This
module is the heart of the bridge. For each watched app it:

1. lists the source account's cloud saves (``list_saves``),
2. detects saves whose checksum is new (not in the persisted state),
3. downloads each new save from the source and uploads it to the target,
4. records the checksum in state so it is not re-synced,
5. notifies on new cloud saves (and on skips / failures).

The "new" decision is the save's checksum, so a save the source account
updates (new checksum) is re-synced. The state is persisted to JSON so the
daemon does not re-sync everything on every restart.

A save is only marked ``synced`` once its upload has succeeded. A failed
upload is therefore retried on the next pass (its checksum is not recorded as
synced), but it is not re-notified (the checksum is recorded as ``notified``
the moment we first saw it). This separates "did the upload work" from "did we
already tell the user about it".
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .cloud import CloudSave
from .notify import NopNotifier, Notifier
from .rules import Rules

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Outcome of syncing one app in one pass."""

    app_id: int
    status: str = "ok"  # "ok" | "skipped" | "error"
    detected: List[str] = field(default_factory=list)  # paths detected as new
    synced: List[str] = field(default_factory=list)  # paths uploaded ok
    errors: List[str] = field(default_factory=list)  # per-file failure messages

    @property
    def ok(self) -> bool:
        return self.status == "ok" and not self.errors


class SyncEngine:
    """Polls the source account and duplicates new saves to the target account."""

    def __init__(
        self,
        source,
        target,
        rules: Rules,
        notifier: Optional[Notifier] = None,
        state_path: "str | Path | None" = None,
        app_ids: Sequence[int] = (),
    ) -> None:
        self.source = source
        self.target = target
        self.rules = rules
        self.notifier = notifier or NopNotifier()
        self.state_path = state_path
        self.app_ids = list(app_ids)
        self._state: Dict[str, Dict[str, Dict[str, str]]] = {"apps": {}}
        self._load_state()

    # -- app discovery ------------------------------------------------- #
    def _watched_app_ids(self) -> List[int]:
        """Apps to check: configured app_ids, else the source's known apps."""
        if self.app_ids:
            return list(self.app_ids)
        client = getattr(self.source, "cloud_client", None)
        if client is not None and hasattr(client, "list_app_ids"):
            return client.list_app_ids()
        return []

    def run_sync(
        self, app_ids: Optional[Sequence[int]] = None
    ) -> List[SyncResult]:
        """Run one pass over the given apps (or the watched apps)."""
        if app_ids is None:
            app_ids = self._watched_app_ids()
        return self.sync_all(app_ids)

    def sync_all(self, app_ids: Sequence[int]) -> List[SyncResult]:
        return [self.sync_app(app_id) for app_id in app_ids]

    # -- one app ------------------------------------------------------ #
    def sync_app(self, app_id: int) -> SyncResult:
        res = SyncResult(app_id=app_id)
        if not self.rules.should_sync(app_id):
            res.status = "skipped"
            self._notify(
                "skipped", {"app_id": app_id, "name": self.rules.label(app_id)}
            )
            return res

        src_client = self.source.cloud_client
        tgt_client = self.target.cloud_client
        try:
            for save in src_client.list_saves(app_id):
                if save.checksum in self._synced(app_id):
                    continue  # already synced
                res.detected.append(save.path)
                if save.checksum not in self._notified(app_id):
                    self._notify(
                        "new cloud save",
                        {
                            "app_id": app_id,
                            "name": self.rules.label(app_id),
                            "path": save.path,
                            "size": save.size,
                        },
                    )
                    self._mark_notified(app_id, save.checksum, save.path)
                try:
                    self._upload(src_client, tgt_client, save)
                    self._mark_synced(app_id, save.checksum, save.path)
                    res.synced.append(save.path)
                except Exception as exc:  # noqa: BLE001 - retry next pass
                    logger.warning("sync %s failed: %s", save.path, exc)
                    res.errors.append(f"{save.path}: {exc}")
        except Exception as exc:  # noqa: BLE001 - list/download failure
            res.status = "error"
            res.errors.append(str(exc))
            self._notify("sync error", {"app_id": app_id, "error": str(exc)})
        return res

    # -- upload ------------------------------------------------------- #
    def _upload(self, src_client, tgt_client, save: CloudSave) -> None:
        data = src_client.get_save_file(save)
        tgt_client.upload_save(save.app_id, save.path, data)

    # -- notify (never raises) ----------------------------------------- #
    def _notify(self, event: str, payload: Dict[str, Any]) -> None:
        try:
            self.notifier.notify(event, payload)
        except Exception as exc:  # noqa: BLE001 - notifications never take down the loop
            logger.warning("notifier error for %s: %s", event, exc)

    # -- state -------------------------------------------------------- #
    def _synced(self, app_id: int) -> Dict[str, str]:
        return self._state["apps"].setdefault(str(app_id), {}).setdefault("synced", {})

    def _notified(self, app_id: int) -> Dict[str, str]:
        return self._state["apps"].setdefault(str(app_id), {}).setdefault(
            "notified", {}
        )

    def _mark_synced(self, app_id: int, checksum: str, path: str) -> None:
        self._synced(app_id)[checksum] = path
        self._save_state()

    def _mark_notified(self, app_id: int, checksum: str, path: str) -> None:
        self._notified(app_id)[checksum] = path
        self._save_state()

    def _load_state(self) -> None:
        if not self.state_path or not os.path.exists(str(self.state_path)):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            logger.warning("could not load state %s: %s", self.state_path, exc)
            return
        if isinstance(data, dict) and "apps" in data:
            self._state = data
            return
        # Old flat format: {path: checksum}. Migrate into the new shape.
        migrated: Dict[str, Dict[str, Dict[str, str]]] = {"apps": {}}
        for path, checksum in (data or {}).items():
            if not isinstance(checksum, str):
                continue
            app = path.split("/")[0] if "/" in path else "unknown"
            app_entry = migrated["apps"].setdefault(app, {})
            app_entry.setdefault("synced", {})[checksum] = path
            app_entry.setdefault("notified", {})[checksum] = path
        self._state = migrated
        self._save_state()

    def _save_state(self) -> None:
        if not self.state_path:
            return
        directory = os.path.dirname(str(self.state_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = str(self.state_path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._state, fh, indent=2, sort_keys=True)
        os.replace(tmp, str(self.state_path))
