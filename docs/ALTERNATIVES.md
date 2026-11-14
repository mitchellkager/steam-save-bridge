# Alternative Projects — Web Research (2026-11-14)

Searches: "github steam cloud save sync between accounts tool", "steam cloud saves transfer between accounts automation github", "steam family shared saves cross account sync self-hosted", "ISteamCloud API", "steam cloud save downloader uploader github tool", "steam family sharing save sharing 2025".

**Key finding: no existing project does cross-account Steam Cloud save sync.** The closest tools are single-account backup/downloaders, same-machine local copiers, or cross-device (not cross-account) self-hosted syncs. This is a genuine gap.

## Closest matches

| Project | What it is | Relevance |
|---|---|---|
| **pyscsd/steamCloudSaveDownloader** (`scsd`) | Python CLI+GUI+Docker; downloads Steam Cloud saves per-account via web session auth (not OAuth), version rotation, notifier on exit, config-file driven, PyPI | **Closest building block.** Solves per-account auth + cloud read (and likely write via ICloudService). Does NOT do cross-account duplication. Could be a dependency or code source. |
| **Ritze03/SteamAlt** | GUI (Win/Linux) copying *local* Steam userdata between accounts on the same machine; per-game selection, safe copy | Solves the single-device half of the problem (local copy), not cross-device/cloud. Small, clean codebase. |
| **constancebello/airship** | Python 2-era tool syncing saves between clouds (iCloud ↔ Steam Cloud) | Conceptually adjacent (cloud↔cloud sync) but stale (Py2.6+), single-user. |
| **unknownv2/CloudKit** (2018) | SteamKit toolkit for Steam Cloud file access without the client | Pre-OAuth era; likely broken, but useful as reference for protocol. |

## Self-hosted save-sync (cross-device, NOT cross-account)

| Project | Notes |
|---|---|
| **firsttris/spielstand** | Self-hosted sync/backup for Linux + Steam Deck, web UI, per-PC agent, Google Drive offsite. Architecture (hub + agent) matches our desired shape; scope is device-to-device. |
| **sliky1992/gamesync** | Self-hosted hub + Windows client, WebSocket push, versioned vault, SteamGridDB covers. Same shape, different scope. |
| **SaveLocker** (savelocker.com) | Self-hosted container + clients (Win/Deck), no accounts, LAN/internet sync. Closed source. |
| **OpenSave** (opensave.org) | P2P save sync for non-Steam-cloud games. Closed source. |
| **SaveSync** (Steam app 3832010) | Commercial co-op save sharing; stores in Steam Workshop, encrypted/unlisted. Commercial, not self-hosted, co-op-focused. |
| **dyang886/Game-Save-Manager** | Local save manager with per-account awareness (Steam/Epic/Xbox/RG). Backup-focused, no cloud sync. |
| **Checkpoint64** | Cloud save backup with co-op sharing + locks. Commercial, closed. |

## Steam API surface (for the service)

- **ICloudService Web API** (official): `GetCloudSaveList`, `GetCloudSaveFile`, `BeginAppUploadBatch` / `UploadAppFile` / `FinishAppUploadBatch` — read + write of cloud saves with an OAuth access token. Docs: partner.steamgames.com/doc/webapi/ICloudService, steamapi.xpaw.me/ICloudService.
  - **Caveat** (McKay dev blog, 2023): the OAuth access_token flow is awkward for non-Valve apps; `scsd` sidesteps this by using the **web session cookie** (Steam login → web API) instead. This is the proven, working auth path for a self-hosted poller.
- **No push/webhook exists** for Steam Cloud changes — polling `GetCloudSaveList` (or the web equivalent) is the only mechanism, confirming the user's proposed design.
- Steam Families (2024+) share *games*, not *saves*: multiple 2025 community sources confirm cloud saves do not sync between family accounts and manual file transfer is the only official route.

## Implications

1. **Greenfield gap**: nothing out there does exactly "poll N accounts' clouds and mirror saves across accounts with per-game rules + notifications."
2. **scsd is the key prior art** for the hard part (per-account auth + cloud read/write via web session). A contribution there (or forking it) may beat contributing to an account-switcher.
3. The self-hosted sync tools (spielstand/gamesync/SaveLocker) validate the hub+agent architecture but none address the cross-account axis — that axis is what makes this problem unique.
4. If contributing to an existing repo, the account-switchers (SAM etc.) are the natural host for a *client-side* feature (per-user local sync), while the *server-side* poller/duplicator is new and fits best as its own small service, possibly built on scsd.
