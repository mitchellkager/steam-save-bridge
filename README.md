# steam-save-bridge

Self-hosted cross-account Steam Cloud save sync.

Steam has no way to share game saves between accounts. `steam-save-bridge` is a
self-hosted service that polls each account's Steam Cloud saves and duplicates new
or updated saves to the other account's cloud — so two people (e.g. a Steam Family
pair) can play the same game on separate accounts and share progress without
tedious download/upload.

## Status

**Early development.** See [docs/PROBLEM.md](docs/PROBLEM.md) for the problem
statement, [docs/ALTERNATIVES.md](docs/ALTERNATIVES.md) for the landscape research,
and [docs/FIT_REPORT.md](docs/FIT_REPORT.md) for why this is a standalone project
rather than a contribution to an existing tool.

## Design (planned)

- **Per-account auth** via Steam web sessions (the proven non-Valve-app path;
  see scsd), with a refresh-token vault for token lifetime.
- **Polling** of `ICloudService.GetCloudSaveList` per account — Steam Cloud has no
  push/webhook mechanism, so polling is the only option.
- **Rule engine**: global default + per-game (AppID) allow/deny.
- **One-directional duplication**: new/updated save in account A → uploaded to
  account B's cloud via `BeginAppUploadBatch` / `UploadAppFile` /
  `FinishAppUploadBatch`. Each client keeps syncing its own account's saves to its
  own cloud; the bridge only duplicates.
- **Notifications** on new cloud saves.
- **Interfaces** (not all P0): self-hosted backend, SPA, CLI, GUI desktop app,
  OS-level tray/background service.

## Interfaces

| Interface | Priority |
|---|---|
| Self-hosted backend (poller + rule engine + API) | P0 |
| CLI | P0 |
| SPA (web UI) | P1 |
| GUI desktop app | P1 |
| OS service (tray / background) | P1 |

## License

MIT
