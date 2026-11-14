"""Command-line interface.

Subcommands
-----------
* ``login``  — authenticate one account (interactive Steam Guard), persist it.
* ``list``   — list cloud saves for an app id on one account.
* ``sync``   — run one sync pass (source → target) for the given app ids.
* ``daemon`` — poll on an interval, forever (the P0 loop as a service).

A tiny JSON config file ties the pieces together (see ``--config``).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import List, Optional

from . import __version__
from .auth import SteamAccount
from .cloud import CloudClient
from .notify import make_notifier
from .rules import Rules
from .sync import SyncEngine

logger = logging.getLogger(__name__)


def _load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _account_from_cfg(cfg: dict, which: str) -> SteamAccount:
    """Build a :class:`SteamAccount` from a config block (``source``/``target``)."""
    block = cfg[which]
    acct = SteamAccount(
        name=block.get("name", which),
        username=block["username"],
        password=block["password"],
        session_dir=block.get("session_dir", "."),
    )
    acct.load()
    return acct


def _rules_from_cfg(cfg: dict) -> Rules:
    return Rules.from_dict(cfg.get("rules", {}))


def _engine_from_cfg(cfg: dict) -> SyncEngine:
    source = _account_from_cfg(cfg, "source")
    target = _account_from_cfg(cfg, "target")
    notifier_cfg = cfg.get("notify", {})
    notifier = make_notifier(
        kind=notifier_cfg.get("kind", "nop"),
        webhook_url=notifier_cfg.get("webhook_url"),
        command=notifier_cfg.get("command"),
    )
    return SyncEngine(
        source=source,
        target=target,
        rules=_rules_from_cfg(cfg),
        notifier=notifier,
        state_path=cfg.get("state_path", "sync_state.json"),
    )


# ---------------------------------------------------------------------- #
# subcommand handlers
# ---------------------------------------------------------------------- #
def cmd_login(args: argparse.Namespace) -> int:
    if args.qr:
        acct = SteamAccount(
            name=args.name or "qr-account",
            username="",
            password="",
            session_dir=args.session_dir,
        )
        acct.login_qr(device_name=args.name or "steam-save-bridge")
        acct.save()
        print(f"logged in via QR and saved session: {acct.name}")
        return 0
    if not args.username or not args.password:
        print("error: --username and --password are required (or use --qr)", file=sys.stderr)
        return 2
    acct = SteamAccount(
        name=args.name or args.username,
        username=args.username,
        password=args.password,
        session_dir=args.session_dir,
    )
    if args.two_factor:
        acct.login_two_factor(args.two_factor)
    else:
        acct.login()
    acct.save()
    print(f"logged in and saved session: {acct.name}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    acct = SteamAccount(
        name=args.name or args.username,
        username=args.username,
        password=args.password,
        session_dir=args.session_dir,
    )
    acct.load()
    client = CloudClient(acct)
    for save in client.list_saves(args.app_id):
        print(f"{save.path}  ({save.size} bytes, {save.timestamp})")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)
    engine = _engine_from_cfg(cfg)
    app_ids: List[int] = args.app_id if args.app_id else cfg.get("app_ids", [])
    results = engine.sync_all(app_ids)
    for r in results:
        status = "OK" if r.ok else "ERROR"
        print(
            f"[{status}] app {r.app_id}: "
            f"detected={len(r.detected)} synced={len(r.synced)} errors={len(r.errors)}"
        )
    return 0 if all(r.ok for r in results) else 1


def cmd_daemon(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)
    engine = _engine_from_cfg(cfg)
    app_ids: List[int] = cfg.get("app_ids", [])
    interval = cfg.get("poll_interval_seconds", 300)
    logger.info(
        "daemon: polling %s app id(s) every %ss (source=%s -> target=%s)",
        len(app_ids),
        interval,
        engine.source.name,
        engine.target.name,
    )
    while True:
        try:
            engine.sync_all(app_ids)
        except Exception as exc:  # noqa: BLE001 - the daemon must survive one bad pass
            logger.error("sync pass failed: %s", exc)
        time.sleep(interval)
    return 0


# ---------------------------------------------------------------------- #
# argument parsing
# ---------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="steam-save-bridge",
        description="Cross-account Steam Cloud save bridge.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="authenticate and persist one account")
    p.add_argument("--username", default="")
    p.add_argument("--password", default="")
    p.add_argument("--name", default=None, help="friendly account name")
    p.add_argument("--two-factor", default=None, help="manual Steam Guard code")
    p.add_argument("--qr", action="store_true",
                   help="login via Steam Guard QR code (no password required)")
    p.add_argument("--session-dir", default=".")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("list", help="list cloud saves for an app id")
    p.add_argument("--username", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--name", default=None)
    p.add_argument("--session-dir", default=".")
    p.add_argument("--app-id", type=int, required=True)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("sync", help="run one sync pass from config")
    p.add_argument("--config", default="steam-save-bridge.json")
    p.add_argument("--app-id", type=int, action="append", default=None,
                   help="override config app_ids (repeatable)")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("daemon", help="poll forever from config")
    p.add_argument("--config", default="steam-save-bridge.json")
    p.set_defaults(func=cmd_daemon)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
