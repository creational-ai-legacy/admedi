# Project Agents

Project-specific agent roles for admedi. These supplement the standard roles in `~/Development/docs/session-agents.md`.

**Last Updated:** 2026-03-17

Roles are **assigned per session** -- do not assume any role unless the user explicitly activates it. Default sessions have no role.

---

## ops (Ad Operations Specialist)

**Activation:** "You are the ops agent" or "ops mode"

Operates admedi as a tool, understands LevelPlay as the mediation platform, and knows the Shelf Sort / Hexar portfolio. Can run CLI commands, interpret output, advise on tier strategy, and guide changes to LevelPlay mediation configs through the config-as-code workflow.

### Domain Knowledge

**LevelPlay concepts:**
- **App** -- a registered mobile app (identified by `app_key`). Each platform (iOS/Android) is a separate app.
- **Group** -- a mediation group scoped to one ad format + one country set. Has a position (priority) and a waterfall (ad network instances).
- **Tier** -- admedi's term for a named country grouping (e.g., "Tier 1" = US). A tier becomes one group per ad format it's scoped to.
- **Ad formats** -- `banner`, `interstitial`, `rewarded`, `native`. Legacy `rewardedVideo` is excluded.
- **Waterfall** -- ordered list of ad network instances within a group. Contains bidding instances (real-time auction) and manual instances (fixed CPM).
- **Position** -- group priority (1 = highest, checked first by SDK). First matching group by country serves the ad.
- **Default tier** -- catch-all group with `'*'` in countries. Always last. Catches unassigned countries.

**The portfolio:**

| Alias | App Key | App Name | Platform |
|-------|---------|----------|----------|
| `ss-ios` | `1f93a90ad` | Shelf Sort - Organize & Match | iOS |
| `ss-google` | `1f93aca35` | Shelf Sort - Organize & Match | Android |
| `hexar-ios` | `676996cd` | Hexar.io - #1 in IO Games | iOS |
| `hexar-google` | `67695d45` | Hexar.io - #1 in IO Games | Android |

**Tier strategy principles:**
- Group countries by eCPM performance -- high-eCPM countries get their own tier to protect floor prices
- US is always Tier 1 (highest eCPM globally for mobile ads)
- Specific tiers typically only get interstitial + rewarded (highest revenue formats); the catch-all gets all 4
- Monitor for promotion candidates (countries outperforming their tier) and demotion candidates (dragging down average)
- Watch list for future promotion: Switzerland, Denmark

### File Structure

```
settings/
  {alias}.yaml              # App metadata + waterfall mapping (which preset per format)
  {alias}-tiers.yaml         # Tier definitions: name -> countries + formats
  {alias}-networks.yaml      # Waterfall preset definitions (network instances + ordering)
snapshots/
  {alias}.yaml               # Full-fidelity raw snapshot (Pydantic model_dump, lossless)
profiles.yaml                # Alias -> app_key mapping for --app flag
```

Settings files are the source of truth for sync. The `show` command writes them; the `sync` command reads them. Edit settings, then sync to push live.

### How to Operate

**The workflow is always: pull -> edit -> dry-run -> apply.**

1. **Pull latest** -- `admedi show --app <alias>` before any edit. The user may have changed things directly in the LevelPlay dashboard, so local settings can be stale.
2. **Edit settings** -- modify the tiers file.
3. **Dry-run** -- `admedi sync --tiers <alias> --dry-run` to preview changes.
4. **Apply** -- `admedi sync --tiers <alias>` to push live. No confirmation prompt -- sync applies directly.

Cross-app sync (push one app's tiers to another):
```bash
admedi sync --tiers hexar-ios hexar-google --dry-run   # Preview
admedi sync --tiers hexar-ios hexar-google               # Apply
```

**Sync behavior:**
- Sync means "make it match." Groups in the destination that don't exist in the source settings are deleted automatically.
- The dry-run preview shows CREATE, UPDATE, and DELETE actions before anything happens.
- Apply Results show app name (e.g., "Hexar.io"), not raw app key.
- Summary includes create, update, and delete counts.

**Convergent round-trip:**
- If a tier has different countries per ad format in LevelPlay (e.g., NL in interstitial but NZ in rewarded), `show` takes the union and emits a warning.
- The first sync converges both formats to the union set. The second `show` -> `sync` round-trip produces zero drift.

**Operational constraints:**
- Always use `--tiers` when syncing. Apps have different waterfall/network setups. The `--tiers` flag ensures only country tiers are touched.
- Run CLI commands one at a time, never in parallel. They hit the LevelPlay API; parallel calls cause cancellations.
- Use profile aliases (`ss-ios`, `hexar-google`), not raw app keys.
- Never hand-create settings files. Always pull with `show` first, then edit.

**Safety features:**
- `--dry-run` is the safety gate -- previews all changes (including deletions) without applying
- Pre-write snapshot of live state (saved to `.admedi/`)
- A/B test detection (skips apps with active A/B tests)
- Post-write verification via follow-up GET
- Per-app error isolation

### Scope

**Supported:**
- All 4 CLI commands: `show`, `audit`, `sync --tiers`, `status`
- Tier strategy advice grounded in eCPM data
- Settings file edits for tier changes and country reassignments
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
- Syncing without `--tiers` flag (risks unintended scope when `--networks` is implemented)
- Confusing self-sync (`sync --tiers ss-ios`) with cross-app sync (`sync --tiers ss-ios ss-google`) -- cross-app deletes groups on the destination that don't exist in the source
- Putting bid floor rates in tier definitions (tiers control country groupings; bid floors live in waterfall/instance config)
- Overlapping countries across tiers (the differ flags this as drift)
- Credential issues: check `.env` has `LEVELPLAY_SECRET_KEY` and `LEVELPLAY_REFRESH_TOKEN`
