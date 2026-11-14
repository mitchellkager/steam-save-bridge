# Cross-Account Steam Save Sync — Candidate Project Fit Report

**Date:** 2026-11-14
**Method:** One read-only subagent per candidate repo (7 total), each given `PROBLEM.md` and
instructed to inspect the cloned working tree and score fit for contributing the P0 feature
(automated cross-account cloud-save duplication, per-game rules, notifications) plus the
desired interfaces (self-hosted backend, SPA, CLI, GUI desktop, OS service).
Web research on alternative projects is in `ALTERNATIVES.md`.

## TL;DR

**No candidate repo is a good home for the feature.** All 7 are account *switchers/vaults*
(or one save-slot tool) with zero cloud-save code, and 4 of 7 are architecturally
incompatible with a self-hosted polling service (Windows-only GUIs, frozen projects,
design invariants that forbid a server holding credentials).

**Recommendation: build a standalone service.** The candidate set is still valuable as a
donor of specific mechanisms (see "Stealable assets"). The single most important finding
across all research: **no existing project does cross-account Steam Cloud save sync —
this is a genuine gap.**

## Verdicts

| Repo | Verdict | One-line why |
|---|---|---|
| RainOrigami/SteamAccountManager | **POOR** | ~200-line Windows registry switcher; explicit minimalism ("no parsing of third-party files"); no network code, tests, or CI |
| wiseLlama0/save-manager | **POOR** | 683-line single-file DD2 save-slot tool; explicitly requires Steam Cloud **disabled** — antithetical premise |
| AetherCollective/AltLauncher | **POOR** | Windows AutoIt per-person save-profile swapper; no auth/cloud/network; wrong problem framing (same-machine profiles vs two accounts) |
| rex706/sam | **POOR** (weak) | WPF UI-automation login switcher, .NET 4.7.2; no cloud code; **no LICENSE file** (all-rights-reserved — can't legally contribute/fork); no tests/CI; a `ClearUserData` toggle deletes `userdata/` after login |
| velyndev/steam-account-manager | **WEAK** (partial) | MIT C++ Windows vault; **best donor of mechanisms** (per-account auth, userdata deploy, remotecache-staleness push trick) but no cloud code, Windows-only GUI, CS2/alt persona, no tests |
| k1tbyte/Steam-Organizer | **WEAK** (poor for daemon) | PWA vault + stateless CORS proxy; **author-frozen**; zero save code; core invariant "server never sees credentials" is the inverse of what a sync daemon requires |
| TCNOco/TcNo-Acc-Switcher | **MEDIUM-LOW** (best in set) | Multi-account refresh-token vault + Steam-only HTTP/protobuf boundary + per-account sweep; but GPL-3.0, single-user desktop, zero cloud code, server mode unimplemented |

## Stealable assets (ranked)

1. **`pyscsd/steamCloudSaveDownloader` (scsd)** — the proven auth + cloud I/O path:
   per-account **web-session** (cookie) auth, `ICloudService` read *and* write
   (`GetCloudSaveList` / `GetCloudSaveFile` / `BeginAppUploadBatch` / `UploadAppFile` /
   `FinishAppUploadBatch`), version rotation, notifier, CLI+GUI+Docker. This is the
   backbone of the hard part. (MIT, PyPI.)
2. **`velyndev/steam-account-manager` `core/`** (MIT) — the **remotecache-staleness
   push trick**: edit `userdata/<id>/<appid>/remote/`, leave `remotecache.vdf` stale,
   Steam uploads it as a pending local change on next sync. Plus
   `deploy_userdata_folder()` (per-account userdata copy with merge semantics + the
   `localconfig.vdf` sibling trap they documented), VDF read/write, per-account
   web-session minting, CM session + `send_service_method`, in-process SOCKS5/HTTP proxy.
3. **`AetherCollective/AltLauncher-Templates`** — crowd-sourced, **Steam-App-ID-keyed
   database of per-game save paths** (Directories/Files/Registry). Directly answers the
   "per-game save location discovery" open question. (Not yet cloned; license unverified.)
4. **`TCNOco/TcNo-Acc-Switcher`** — the refresh-token vault pattern + Steam
   HTTP/protobuf boundary for a self-hosted poller (GPL-3.0 — read the design, don't
   link the code).
5. **`rex706/sam`** — SteamID64→SteamID3 mapping + per-user `userdata/` path resolution
   (reference only — no license).

## Open questions — status after research

| Question | Status |
|---|---|
| Push mechanism for Steam Cloud changes? | **Closed: none exists.** No webhook/push API; polling `GetCloudSaveList` is the only option — the proposed design is confirmed as the right shape. |
| Per-account auth from a self-hosted service? | **Solved by prior art:** scsd's web-session (cookie) flow works for non-Valve apps; the OAuth access-token flow is awkward. TcNo's refresh-token vault is the pattern for token lifetime. |
| Per-game save location discovery? | **Mostly answered:** AltLauncher-Templates App-ID-keyed path DB + `libraryfolders.vdf` parsing (Gameloop.Vdf / velyndev's `vdf_file`). |
| Conflict handling? | Still open. scsd's version rotation is a template; velyndev's "stale remotecache = pending local change" semantics give a clean last-writer-wins per-account model (each account owns its own cloud copy, duplication is one-directional per game). |

## Recommendation

1. **Build `steam_multiacc_saves` as a standalone service** (Python, matching scsd's
   stack): poller per account (web-session auth, token refresh vault), rule engine
   (global + per-appid allow/deny), one-directional duplicate to the other account's
   cloud via `ICloudService` upload batch, notifier, CLI + SPA + tray.
2. **Fork/extend scsd** for the cloud I/O layer rather than reinventing auth.
3. **Vendor the velyndev mechanisms** (MIT, attribution) for the local-placement half
   (tray app / "copy to this account's local path" convenience) and the
   remotecache-staleness trick as a fallback push path when the Steam client is on the
   same machine.
4. **Clone AltLauncher-Templates** and check its license; if permissive, import the
   App-ID→save-path table as the default discovery source.
5. **Do not contribute the feature to any of the 7 candidate repos.** The only
   upstream-worthy side patch identified: generalize velyndev's
   `deploy_userdata_folder` from CS2-only to arbitrary appids with allow/deny — small,
   reviewable, persona-neutral, and it fixes the piece our own tool would want upstream.
