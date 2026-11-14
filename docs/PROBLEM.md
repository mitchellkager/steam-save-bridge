# Problem Statement: Cross-Account Steam Save Management for a Steam Family

## The situation

Two people (a couple) each own a separate Steam account. Their accounts are
united as a **Steam Family** (Steam Family Sharing), so each can play the
other's purchases. For a given game, either account may be used to play,
depending on a variety of factors (which device, which account is logged in,
what's available on that device, etc.).

Steam has no convenient mechanism for managing game saves **between accounts**.

- **On a single device:** saves live in per-user install paths (e.g.
  `%USERPROFILE%` / `$HOME`-scoped game data dirs). Transferring a save to the
  other account means manually copying files between the two users' game data
  directories. Error-prone, tedious, and game-specific.
- **Across devices:** the process is very tedious — some combination of
  downloading save files from one device, moving them, uploading them to
  another, then placing them in the right per-user game directory.

Steam Cloud syncs saves per-account, but there is no way to move or share a
cloud save from one account to the other.

## The proposed approach

- Each Steam client (per user, per device) remains responsible for syncing
  **only its own user's saves** to **that user's** Steam Cloud.
- A **separate service** (self-hosted) continuously **polls each user's cloud
  saves** and makes it very easy to **duplicate a cloud save across to the
  other user's** account (i.e. push the save into the other account's cloud,
  or otherwise make it available to the other account on the devices it runs
  on).

## Priority features

- **[P0] Automated cross-account sync** for each new cloud save (when account A
  uploads a new save for game G, account B's cloud gets the same save —
  automatically, or with a single confirm).
- **[P0] Opt-in / opt-out per game**, plus the ability to set **global rules**
  (e.g. "sync everything", "never sync game X", "only sync games in list Y").
- **[P0] Notifications** on new cloud saves (so a user knows a save is
  available / has been synced to their account).
- **Multiple interfaces** (all desirable, not all P0):
  - Self-hosted backend (the polling + sync service)
  - SPA (web frontend for browsing saves, toggling per-game rules, viewing
    sync status, receiving notifications)
  - CLI (for power users / scripting)
  - GUI desktop app
  - OS-level service (tray / background)

## Open questions (for fit evaluation)

- What is the mechanism for "pushing" a save into another account's cloud
  (Steam Web API capabilities, local save-file placement + Steam client sync,
  etc.)?
- How to authenticate as each Steam account (per-account cookies / access
  tokens) from a self-hosted service?
- Per-game save location discovery (Steam Cloud vs. local-only saves).
- Conflict handling when both accounts have diverged saves for the same game.

## What we want to know (for each candidate project)

Given this problem, evaluate the fit of contributing this feature (or a
sub-feature) to the candidate project:

1. **What is the project?** (purpose, language, architecture, maintenance
   status, activity, community size, license)
2. **Does it already touch anything relevant?** (Steam auth/session handling,
   multi-account management, Steam Web API, save handling, cloud saves,
   notifications)
3. **Fit verdict:** is this a natural extension of the project, a stretch, or
   out of scope? Would maintainers plausibly accept a PR adding
   cross-account save sync?
4. **Concrete integration sketch:** which modules would the feature touch,
   what would need to be added, what's the rough size of the change?
5. **Risks:** architectural mismatch, scope bloat, maintenance burden, license
   issues, Steam ToS considerations.
