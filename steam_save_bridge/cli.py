"""Command-line interface for steam-save-bridge.

Subcommands:
  login   Authenticate one Steam account (password or QR) and save the session.
  list    List cloud saves for an app (verifies the cloud client works).
  sync    Run one sync pass from the configured source to the target.
  daemon  Run the sync loop forever (for systemd / a background service).

The password is entered once, interactively (hidden), and is never stored in the
config or on disk — the pickled session is a refresh-token vault only.
"""

from __future__ import annotations

import argparse
import getpass
import os
import time

from .auth import SteamAccount
from .notify import DiscordNotifier, NopNotifier, Notifier, ScriptNotifier
from .rules import Rules
from .sync import SyncEngine


def _session_file(session_dir: str, name: str | None, username: str) -> str:
    """Derive the session file path from the session dir and an account label."""
    label = name or username or "account"
    return os.path.join(session_dir, f"{label}.pkl")


def _account_from_cfg(cfg: dict, which: str) -> SteamAccount:
    block = cfg[which]
    name = block.get("name", which)
    session_file = block.get("session_file") or _session_file(
        block.get("session_dir", "."), name, block.get("username", "")
    )
    acct = SteamAccount.load(name, session_file)
    acct.refresh()
    return acct


def _engine_from_cfg(cfg: dict) -> SyncEngine:
    source = _account_from_cfg(cfg, "source")
    target = _account_from_cfg(cfg, "target")
    rules = Rules.from_dict(cfg.get("rules", {}))
    ncfg = cfg.get("notify", {})
    kind = ncfg.get("kind", "nop")
    if kind == "discord":
        notifier: Notifier = DiscordNotifier(ncfg["webhook_url"])
    elif kind == "script":
        notifier = ScriptNotifier(ncfg["command"])
    else:
        notifier = NopNotifier()
    return SyncEngine(
        source=source,
        target=target,
        rules=rules,
        notifier=notifier,
        app_ids=cfg.get("app_ids"),
        state_path=cfg.get("state_path", "./sync_state.json"),
    )


def cmd_login(args: argparse.Namespace) -> None:
    if args.qr:
        session_file = _session_file(args.session_dir, args.name, "")
        print("Opening Steam Guard QR login. Scan the QR with the Steam app on your phone.")
        acct = SteamAccount(name=args.name or "qr-account", username="", password="", session_file=session_file)
        acct.login_qr()
        print(f"Logged in as {acct.steam_id or 'unknown'} (steamID). Session saved to {session_file}.")
        return

    if not args.username:
        raise SystemExit("--username is required for password login")
    if not args.password:
        args.password = getpass("Steam password: ")
    session_file = _session_file(args.session_dir, args.name, args.username)
    acct = SteamAccount(
        name=args.name or args.username,
        username=args.username,
        password=args.password,
        session_file=session_file,
    )
    if args.two_factor:
        print("Steam Guard code was provided; using it for this login.")
    else:
        print("No --two-factor code provided. If Steam asks for a code, it will be printed for you to type.")
    acct.login(args.username, args.password, args.two_factor)
    print(f"Logged in as {acct.steam_id or 'unknown'} (steamID). Session saved to {session_file}.")


def cmd_list(args: argparse.Namespace) -> None:
    name = args.name
    session_file = _session_file(args.session_dir, args.name, "")
    acct = SteamAccount.load(name, session_file)
    acct.refresh()
    client = acct.cloud_client
    print(f"probe: {client.probe()}")
    print(f"--- saves for app {args.app_id} ---")
    for save in client.list_saves():
        if save.app_id == args.app_id:
            print(f"  {save.path}  ({save.size} bytes, updated {save.updated_at})")


def cmd_sync(args: argparse.Namespace) -> None:
    cfg = _load_cfg(args.config)
    engine = _engine_from_cfg(cfg)
    app_ids = cfg.get("app_ids") or None
    for r in engine.run_sync(app_ids):
        if not r.ok:
            print(f"  app {r.app_id}: {r.status} — {len(r.detected)} detected, {len(r.synced)} synced")
            for e in r.errors:
                print(f"    ! {e}")
    print("sync pass complete.")


def cmd_daemon(args: argparse.Namespace) -> None:
    cfg = _load_cfg(args.config)
    engine = _engine_from_cfg(cfg)
    app_ids = cfg.get("app_ids") or None
    interval = int(cfg.get("poll_interval_seconds", 300))
    print(f"daemon: polling every {interval}s (Ctrl-C to stop)")
    while True:
        for _ in engine.run_sync(app_ids):
            pass
        time.sleep(interval)


def _load_cfg(path: str) -> dict:
    import json

    with open(path) as f:
        return json.load(f)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="steam-save-bridge", description="Cross-account Steam Cloud save bridge.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="Authenticate one Steam account and save the session.")
    p.add_argument("--username")
    p.add_argument("--password", help="Omit to be prompted (hidden). Never stored.")
    p.add_argument("--two-factor", help="Steam Guard code, if prompted.")
    p.add_argument("--qr", action="store_true", help="Use Steam Guard QR login (no password).")
    p.add_argument("--name", help="Friendly name for this account (default: the username).")
    p.add_argument("--session-dir", default=".", help="Directory to store the session file.")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("list", help="List cloud saves for an app (verifies the cloud client works).")
    p.add_argument("--name", required=True, help="Friendly name of the account (matches the config).")
    p.add_argument("--session-dir", default=".")
    p.add_argument("--app-id", type=int, required=True)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("sync", help="Run one sync pass from the configured source to the target.")
    p.add_argument("--config", default="steam-save-bridge.json")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("daemon", help="Run the sync loop forever.")
    p.add_argument("--config", default="steam-save-bridge.json")
    p.set_defaults(func=cmd_daemon)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
