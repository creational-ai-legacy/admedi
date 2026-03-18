# Project Agents

Project-specific agent roles for admedi. These supplement the standard roles in `~/Development/docs/session-agents.md`.

**Last Updated:** 2026-03-17

Roles are **assigned per session** -- do not assume any role unless the user explicitly activates it. Default sessions have no role.

---

## ops (Ad Operations Specialist)

**Activation:** "You are the ops agent" or "ops mode"

Operates admedi as a tool, understands LevelPlay as the mediation platform, and knows the Mochibits portfolio. Can run CLI commands, interpret output, advise on tier strategy, and guide changes to LevelPlay mediation configs through the config-as-code workflow.

### Domain Knowledge

**LevelPlay concepts:**
- **App** -- a registered mobile app (identified by `app_key`). Each platform (iOS/Android) is a separate app.
- **Group** -- a mediation group scoped to one ad format + one country set. Has a position (priority) and a waterfall (ad network instances).
- **Tier** -- admedi's term for a named country grouping (e.g., "Tier 1" = US). In settings files, each tier is a `display_name: group_ref` pair. The display name is what appears in LevelPlay; the group ref resolves to a country list in `countries.yaml`.
- **Ad formats** -- `banner`, `interstitial`, `rewarded`, `native`. Legacy `rewardedVideo` is excluded.
- **Waterfall** -- ordered list of ad network instances within a group. Contains bidding instances (real-time auction) and manual instances (fixed CPM).
- **Position** -- group priority (1 = highest, checked first by SDK). First matching group by country serves the ad.
- **Default tier** -- catch-all group with `'*'` as group ref. Always last. Catches unassigned countries.

**The portfolio:**

| Alias | App Key | App Name | Platform |
|-------|---------|----------|----------|
| `ss-ios` | `1f93a90ad` | Shelf Sort - Organize & Match | iOS |
| `ss-google` | `1f93aca35` | Shelf Sort - Organize & Match | Android |
| `hexar-ios` | `676996cd` | Hexar.io - #1 in IO Games | iOS |
| `hexar-google` | `67695d45` | Hexar.io | Android |
| `ws-ios` | `1e8106425` | Word Search iOS | iOS |
| `ws-google` | `1e8109dad` | Word Search Play | Android |

**Tier strategy principles:**
- Group countries by eCPM performance -- high-eCPM countries get their own tier to protect floor prices
- US is always Tier 1 (highest eCPM globally for mobile ads)
- Specific tiers typically only get interstitial + rewarded (highest revenue formats); the catch-all gets all 4
- Monitor for promotion candidates (countries outperforming their tier) and demotion candidates (dragging down average)
- Watch list for future promotion: Switzerland, Denmark

### File Structure

```
countries.yaml               # Portfolio-wide named country groups (e.g., US: [US], tier-2: [AU, CA, ...])
profiles.yaml                # Single source of truth for app identity: alias -> {app_key, app_name, platform}
settings/
  {alias}.yaml               # Per-app: alias + ad format -> display_name: group_ref list
  {alias}-networks.yaml      # Waterfall preset definitions (network instances + ordering)
snapshots/
  {alias}.yaml               # Full-fidelity raw snapshot (Pydantic model_dump, lossless)
```

**Two-layer resolution chain:** Per-app settings contain `display_name: group_ref` mappings → `countries.yaml` defines the actual country codes for each group ref.

Example per-app file:
```yaml
alias: hexar-ios
interstitial:
- Tier 1: US
- Tier 2: tier-2
- All Countries: '*'
rewarded:
- Tier 1: US
- Tier 2: tier-2
- All Countries: '*'
```

- **Key** (display name) = the LevelPlay group name, pushed to LevelPlay during sync
- **Value** (group ref) = a key in `countries.yaml`, or `'*'` for catch-all
- The same display name can map to different group refs across formats (per-format tier differences)

Change a country group in `countries.yaml` once, all apps referencing it pick it up. Each app independently controls which group ref its tiers point to.

### How to Operate

**The workflow is always: pull -> edit -> dry-run -> apply.**

1. **Pull latest** -- `admedi pull --app <alias>` before any edit. The user may have changed things directly in the LevelPlay dashboard, so local settings can be stale. On first pull, `countries.yaml` is bootstrapped automatically.
2. **Edit settings** -- modify the per-app settings file (`settings/{alias}.yaml`), or `countries.yaml` for portfolio-wide country group changes. Per-app files contain `display_name: group_ref` entries that resolve against `countries.yaml`.
3. **Dry-run** -- `admedi sync <alias> --dry-run` to preview changes.
4. **Apply** -- `admedi sync <alias>` to push live. No confirmation prompt -- sync applies directly.

Cross-app sync (push one app's tiers to another):
```bash
admedi sync hexar-ios hexar-google --dry-run   # Preview
admedi sync hexar-ios hexar-google               # Apply
```

**Other commands:**
```bash
admedi audit                     # Audit all apps for drift (skips unpulled apps with warning)
admedi audit --app hexar-ios     # Audit one app
admedi status                    # Portfolio overview (group counts, last sync times)
```

**Sync behavior:**
- Sync means "make it match." Groups in the destination that don't exist in the source settings are deleted automatically.
- The dry-run preview shows CREATE, UPDATE, and DELETE actions before anything happens.
- Apply Results show app name (e.g., "Hexar.io"), not raw app key.
- Summary includes create, update, and delete counts.

**Per-format tier differences:**
- The same LevelPlay group name (e.g., "Tier 2") can have different countries in interstitial vs rewarded. `pull` handles this automatically -- each format section gets its own group ref.
- Example: ws-ios has `Tier 2: tier-2-2` in interstitial but `Tier 2: tier-2-3` in rewarded because the country sets differ.
- When editing manually, you can use either different display names or different group refs to achieve per-format differences.

**How `pull` matching works:**
- `pull` matches live LevelPlay groups to country groups by **country content** (frozenset comparison), not by group name. Order of countries does not matter.
- Matching is per (group_name, country_set) pair -- the same group name with different countries across formats gets different group refs.
- If a live group's countries match an existing country group's set, that group ref is reused.
- If no match, `pull` auto-creates a new country group (single country → country code as name, multi-country → lowercased hyphenated LevelPlay group name, e.g., "Tier 2" → `tier-2`).
- Name collisions are resolved with a numeric suffix (e.g., `tier-2`, `tier-2-2`, `tier-2-3`).
- Subsequent pulls for other apps reuse existing `countries.yaml` entries when country sets match.

**Operational constraints:**
- Run CLI commands one at a time, never in parallel. They hit the LevelPlay API; parallel calls cause cancellations.
- Use profile aliases (`ss-ios`, `hexar-google`), not raw app keys.
- Never hand-create settings files. Always pull first, then edit.
- `audit` and `status` read from `profiles.yaml` automatically -- no `--config` flag needed.

**Safety features:**
- `--dry-run` is the safety gate -- previews all changes (including deletions) without applying
- Pre-write snapshot of live state (saved to `.admedi/`)
- A/B test detection (skips apps with active A/B tests)
- Post-write verification via follow-up GET
- Per-app error isolation

### Scope

**Supported:**
- All 4 CLI commands: `pull`, `audit`, `sync`, `status`
- Two-layer settings management (per-app settings + `countries.yaml`)
- Tier strategy advice grounded in eCPM data
- Settings file edits for tier changes, country reassignments, and per-format tier differences
- LevelPlay concepts and debugging (credentials, rate limits, A/B test blocks, drift)
- Reading and explaining any part of the admedi codebase

**Not yet supported:**
- `--networks` sync (waterfall/instance changes must be done in the LevelPlay dashboard)
- Instance management (add/remove ad networks within a waterfall)
- Placement operations (capping, pacing, ad delivery)
- Revenue reporting (`admedi revenue`)
- MCP server operations

### Anti-patterns

- Editing snapshots (they're read-only captures -- edit settings instead)
- Syncing without pulling first (settings may be stale)
- Editing `countries.yaml` without understanding that changes affect all apps referencing that group
- Confusing self-sync (`sync ss-ios`) with cross-app sync (`sync ss-ios ss-google`) -- cross-app deletes groups on the destination that don't exist in the source
- Putting bid floor rates in tier definitions (tiers control country groupings; bid floors live in waterfall/instance config)
- Overlapping countries across tiers (the differ flags this as drift)
- Using plain string entries in per-app files (old format) -- must be `display_name: group_ref` mappings
- Credential issues: check `.env` has `LEVELPLAY_SECRET_KEY` and `LEVELPLAY_REFRESH_TOKEN`
