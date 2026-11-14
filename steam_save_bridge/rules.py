"""Sync rules: a global default plus per-app allow/deny overrides.

Mirrors scsd's ``mode = exclude/allow`` idea but expressed as a small, explicit
rule set so the P0 "per-game opt-in / opt-out with global rules" requirement is
satisfied in one place.

Semantics
---------
* ``default`` is applied to every app id that has no explicit rule.
  * ``"sync"``  → app is synced unless explicitly excluded.
  * ``"skip"``  → app is skipped unless explicitly included.
* ``allow`` / ``deny`` are explicit per-app-id overrides that win over the
  default. An app id may appear in at most one of the two (if it appears in
  both, ``deny`` wins — the safe choice).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

# A rule can be written as a plain list of app ids, or as a dict with
# human-readable names: {"760": "Steam", "1203450": "Baldur's Gate 3"}.
AppRule = Union[int, str, Dict[str, Union[int, str]]]


@dataclass
class Rules:
    """A set of sync rules."""

    default: str = "sync"
    allow: List[int] = field(default_factory=list)
    deny: List[int] = field(default_factory=list)
    names: Dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.default not in ("sync", "skip"):
            raise ValueError(
                f"default must be 'sync' or 'skip', got {self.default!r}"
            )
        self.allow = sorted(set(self.allow))
        self.deny = sorted(set(self.deny))

    # ------------------------------------------------------------------ #
    # construction
    # ------------------------------------------------------------------ #
    @classmethod
    def from_dict(cls, data: Dict) -> "Rules":
        """Build a Rules from a config dict.

        Accepts::

            {"default": "sync", "allow": [760, "1203450"],
             "deny": [480], "names": {"760": "Steam"}}

        App ids may be ints or numeric strings.
        """
        names: Dict[int, str] = {}
        for key, value in (data.get("names") or {}).items():
            names[int(key)] = str(value)

        def _ids(entries: Optional[List]) -> List[int]:
            out: List[int] = []
            for entry in entries or []:
                if isinstance(entry, dict):
                    for k, v in entry.items():
                        out.append(int(k))
                        names.setdefault(int(k), str(v))
                else:
                    out.append(int(entry))
            return out

        return cls(
            default=str(data.get("default", "sync")),
            allow=_ids(data.get("allow")),
            deny=_ids(data.get("deny")),
            names=names,
        )

    # ------------------------------------------------------------------ #
    # decision
    # ------------------------------------------------------------------ #
    def should_sync(self, app_id: int) -> bool:
        """Decide whether ``app_id`` should be synced."""
        app_id = int(app_id)
        if app_id in self.deny:
            return False
        if app_id in self.allow:
            return True
        return self.default == "sync"

    def label(self, app_id: int) -> str:
        """Human-readable name for ``app_id`` (falls back to the id)."""
        return self.names.get(int(app_id), str(app_id))

    def describe(self) -> str:
        """One-line human-readable summary of the rule set."""
        allow = ", ".join(
            self.label(a) for a in self.allow
        ) or "none"
        deny = ", ".join(self.label(a) for a in self.deny) or "none"
        return f"default={self.default} allow=[{allow}] deny=[{deny}]"


__all__ = ["Rules"]
