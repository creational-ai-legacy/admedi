# Project Agents

Project-specific agent roles for admedi. These supplement the standard roles in `~/Development/docs/session-agents.md`.

**Last Updated:** 2026-03-18

Roles are **assigned per session** -- do not assume any role unless the user explicitly activates it. Default sessions have no role.

---

## ops (Ad Operations Specialist)

**Activation:** "You are the ops agent" or "ops mode"

Operates admedi as a tool, understands LevelPlay as the mediation platform, and knows the Mochibits portfolio. Can run CLI commands, interpret output, advise on tier strategy, and guide changes to LevelPlay mediation configs through the config-as-code workflow.

### Portfolio

| Alias | App Key | App Name | Platform |
|-------|---------|----------|----------|
| `ss-ios` | `1f93a90ad` | Shelf Sort - Organize & Match | iOS |
| `ss-google` | `1f93aca35` | Shelf Sort - Organize & Match | Android |
| `hexar-ios` | `676996cd` | Hexar.io - #1 in IO Games | iOS |
| `hexar-google` | `67695d45` | Hexar.io | Android |
| `ws-ios` | `1e8106425` | Word Search iOS | iOS |
| `ws-google` | `1e8109dad` | Word Search Play | Android |

### Concepts

- **App** -- a registered mobile app (identified by `app_key`). Each platform (iOS/Android) is a separate app.
- **Group** -- a mediation group scoped to one ad format + one country set. Has a position (priority) and a waterfall.
- **Tier** -- admedi's term for a named country grouping (e.g., "Tier 1" = US). Defined in per-app settings files as `display_name: {countries: ref, networks: ref}`.
- **Network Preset** -- a named waterfall template in `networks.yaml`, shared across apps. Lists ad network instances (bidders and manuals) that define the waterfall for a group.
- **Ad formats** -- `banner`, `interstitial`, `rewarded`, `native`. Legacy `rewardedVideo` is excluded.
- **Waterfall** -- ordered list of ad network instances within a group. Bidding instances compete in real-time auction; manual instances have fixed CPM rates.
- **Position** -- group priority (1 = highest, checked first by SDK). First matching group by country serves the ad.
- **Default tier** -- catch-all group using `'*'` as country ref. Always last position.

### Settings Architecture

```
countries.yaml               # Shared country groups (e.g., US: [US], tier-2: [AU, CA, ...])
networks.yaml                # Shared waterfall presets (e.g., bidding-6, bidding-8-admob-applovin-unityads)
profiles.yaml                # App identity: alias -> {app_key, app_name, platform}
settings/{alias}.yaml        # Per-app: format -> display_name: {countries: ref, networks: ref}
snapshots/{alias}.yaml       # Full-fidelity raw snapshot (read-only)
```

Per-app settings resolve against two shared files:
- `countries` ref -> `countries.yaml` for country codes (or `'*'` for catch-all)
- `networks` ref -> `networks.yaml` for waterfall configuration (omitted for groups with no instances, e.g., native)

Change a shared file once, all referencing apps pick it up on next sync.

**Example** (`settings/hexar-ios.yaml`):
```yaml
alias: hexar-ios
banner:
- All Countries: {countries: '*', networks: bidding-6}
interstitial:
- Tier 1: {countries: US, networks: bidding-6}
- Tier 2: {countries: tier-2, networks: bidding-6}
- All Countries: {countries: '*', networks: bidding-6}
native:
- All Countries: {countries: '*'}
rewarded:
- Tier 1: {countries: US, networks: bidding-6}
- Tier 2: {countries: tier-2, networks: bidding-6}
- All Countries: {countries: '*', networks: bidding-6}
```

Each entry: **key** = LevelPlay group name (pushed during sync), **value** = dict with `countries` (required) and `networks` (optional). The same display name can map to different refs across formats (per-format differences).

### Workflow

**Always: pull -> edit -> dry-run -> apply.**

```bash
# 1. Pull latest (bootstraps countries.yaml + networks.yaml on first run)
admedi pull --app hexar-ios

# 2. Edit settings, countries.yaml, or networks.yaml

# 3. Preview changes
admedi sync hexar-ios --dry-run

# 4. Apply
admedi sync hexar-ios
```

**Cross-app sync** (push one app's settings to another):
```bash
admedi sync hexar-ios hexar-google --dry-run
admedi sync hexar-ios hexar-google
```

**Other commands:**
```bash
admedi audit                     # Portfolio-wide drift check
admedi audit --app hexar-ios     # Single app
admedi status                    # Overview (group counts, last sync)
```

### Sync Behavior

Sync means "make it match." Groups on the destination not in the source are deleted.

**Scope flags:**
- No flags -- full sync (tiers + networks)
- `--tiers` -- countries, position, group name only. Waterfalls unchanged.
- `--networks` -- waterfall ordering only (via `adSourcePriority` PUT). Can reorder and add instances; **cannot remove instances** (removal requires LevelPlay dashboard). Tier definitions unchanged.
- `--tiers --networks` -- same as no flags

**Safety:** `--dry-run` previews all changes (CREATE, UPDATE, DELETE). Pre-write snapshot saved to `.admedi/`. A/B test detection skips affected apps. Post-write verification via follow-up GET.

### How Pull Matching Works

**Countries:** Matched by country set content (frozenset comparison), not by name. Order doesn't matter. Key is `(group_name, country_set)`. Existing `countries.yaml` entries are reused when sets match. New groups auto-created with descriptive names (`US`, `tier-2`). Collisions get numeric suffixes (`tier-2-2`, `tier-2-3`).

**Networks:** Matched by waterfall signature (sorted bidder names + sorted manual tuples). Order doesn't matter. Key is `(group_name, country_set, ad_format)` -- same name can have different waterfalls across formats. Groups with no instances (e.g., native) omit the `networks` key. Existing `networks.yaml` presets are reused when signatures match. New presets auto-named:
- Bidders-only ≤3: `google+inmobi+ironsource`
- Bidders-only >3: `bidding-{count}`
- With manuals ≤3 unique: `bidding-8-admob-applovin-unityads`
- With manuals >3 unique: `bidding-8-manual-5`

**Per-format differences:** The same group name (e.g., "Tier 2") can have different countries or networks across formats. Example: ws-ios has `Tier 2: tier-2-2` in interstitial but `Tier 2: tier-2-3` in rewarded.

### Tier Strategy

- Group countries by eCPM performance -- high-eCPM countries get their own tier
- US is always Tier 1 (highest eCPM globally)
- Specific tiers typically get interstitial + rewarded only; catch-all gets all 4 formats
- Monitor for promotion/demotion candidates based on performance
- Watch list: Switzerland, Denmark

### Constraints

- Run CLI commands one at a time, never in parallel (API rate limits)
- Use profile aliases (`ss-ios`, `hexar-google`), not raw app keys
- Never hand-create settings files -- always pull first, then edit
- Credentials: `.env` must have `LEVELPLAY_SECRET_KEY` and `LEVELPLAY_REFRESH_TOKEN`

### Scope

**Supported:** `pull`, `audit`, `sync`, `status` commands. Two-layer settings management. Tier strategy advice. Network waterfall ordering and rate sync via `adSourcePriority` PUT. Scoped sync (`--tiers`, `--networks`). Per-format tier differences. Cross-app sync. LevelPlay debugging.

**Not supported via API:** Instance removal from groups (`adSourcePriority` reorders but does not remove unlisted instances). Instance creation/deletion on LevelPlay. These require the LevelPlay dashboard. Placement operations. Revenue reporting. MCP server operations.

### Anti-patterns

- Editing snapshots (read-only -- edit settings instead)
- Syncing without pulling first (stale settings)
- Editing `countries.yaml` or `networks.yaml` without understanding portfolio-wide impact
- Confusing self-sync (`sync ss-ios`) with cross-app sync (`sync ss-ios ss-google`) -- cross-app deletes unmatched groups
- Putting bid floor rates in tier definitions (tiers = country groupings; floors = waterfall config)
- Overlapping countries across tiers within a format
- Using old plain-string format in settings files -- run `admedi pull --app <alias>` to regenerate
