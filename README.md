# admedi

Define ad mediation tiers and waterfalls as YAML, diff against live LevelPlay configs, and sync across your entire app portfolio in one command.

* `admedi pull --app <alias>` — fetch live mediation groups, render them as tables, and write per-app settings + a full-fidelity snapshot
* `admedi audit` — diff your settings against live LevelPlay configs, report drift per app (exit 1 on drift, for CI gating)
* `admedi sync <source> [dest]` — reconcile live groups to your settings: create, update, **and delete** groups (and remove networks from waterfalls) to make live match
* `admedi status` — group counts, platforms, and last sync times at a glance
* Config-as-code in four files — `profiles.yaml` (app identity), `countries.yaml` (shared country groups), `settings/<alias>.yaml` (per-app tiers), `networks.yaml` (shared waterfall presets)

## Table of Contents

- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Pull](#pull)
- [Audit](#audit)
- [Sync](#sync)
- [Status](#status)
- [Development](#development)
- [License](#license)

## Getting started

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
pip install git+https://github.com/creational-ai-legacy/admedi
```

Or clone for development:

```bash
git clone https://github.com/creational-ai-legacy/admedi.git
cd admedi
uv sync
```

Add your LevelPlay API credentials:

```bash
cp .env.example .env
```

```
LEVELPLAY_SECRET_KEY=your_secret_key_here
LEVELPLAY_REFRESH_TOKEN=your_refresh_token_here
```

Verify the install:

```
$ admedi --help

 Usage: admedi [OPTIONS] COMMAND [ARGS]...

 Config-driven ad mediation management

╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ pull    Pull live mediation settings for an app.                             │
│ audit   Audit the portfolio for drift against per-app settings.              │
│ sync    Sync settings to live LevelPlay mediation groups.                    │
│ status  Show current portfolio status.                                       │
╰──────────────────────────────────────────────────────────────────────────────╯
```

> Get credentials from the ironSource / Unity LevelPlay dashboard under API Keys. Every command loads them from the environment or `.env`; a missing key exits with code 2 and a clear message.

## Configuration

admedi reads four YAML files from the working directory. There is no monolithic template — each file owns one concern, and per-app settings reference the shared files by name.

### `profiles.yaml` — app identity

The single source of truth mapping a short alias to its LevelPlay app key, name, and platform. Every `--app`/`SOURCE`/`DESTINATION` value is a profile alias (raw app keys are not accepted).

```yaml
profiles:
  ss-ios:
    app_key: "1f93a90ad"
    app_name: "Shelf Sort - Organize & Match"
    platform: iOS
  ss-google:
    app_key: "1f93aca35"
    app_name: "Shelf Sort - Organize & Match"
    platform: Android
```

### `countries.yaml` — shared country groups

Named country groups, referenced by per-app settings. Country codes are ISO 3166-1 alpha-2.

```yaml
US:
- US
tier-2:
- AU
- CA
- DE
- FR
- GB
- JP
- KR
- NL
- NZ
- SA
- TW
```

### `settings/<alias>.yaml` — per-app tiers

One file per app (written by `admedi pull`, edited by you, read by `admedi sync`). Each ad format lists its groups in priority order; each group names a country group from `countries.yaml` and a waterfall preset from `networks.yaml`. The `networks` key is optional.

```yaml
alias: ss-ios
banner:
- All Countries: {countries: '*', networks: bidding-6-2}
interstitial:
- Tier 1: {countries: US, networks: bidding-6-applovin}
- Tier 2: {countries: tier-2, networks: bidding-6-applovin}
- All Countries: {countries: '*', networks: bidding-6-applovin}
rewarded:
- Tier 1: {countries: US, networks: bidding-6-applovin}
- Tier 2: {countries: tier-2, networks: bidding-6-applovin}
- All Countries: {countries: '*', networks: bidding-6-applovin}
```

`countries: '*'` is the catch-all for unassigned countries. The same group name across formats (e.g. `Tier 1`) creates a separate LevelPlay group per format.

### `networks.yaml` — shared waterfall presets

Named waterfall presets reused across apps, so a network change is one edit that propagates portfolio-wide. Each entry is a network with `bidder: true|false`; manual (non-bidder) entries may carry a `rate`, and a network with multiple instances disambiguates with `name`.

```yaml
bidding-6-applovin:
- network: Google
  bidder: true
- network: InMobi
  bidder: true
- network: Meta
  bidder: true
- network: ironSource
  bidder: true
- network: AppLovin
  bidder: false
  rate: 1.0
```

**Resolution:** `settings/<alias>.yaml` → `countries.yaml` (group → country list) + `networks.yaml` (preset → waterfall) → the desired live state for that app.

## Pull

Fetch an app's live mediation groups, render them by ad format, and write per-app settings + a snapshot. Groups are matched to existing tier definitions by country content, so re-pulling preserves your tier names.

```
$ admedi pull --app ss-ios

╭─────────────────────────────────── admedi ───────────────────────────────────╮
│ Shelf Sort - Organize & Match                                                │
│ Platform: iOS  Key: 1f93a90ad  Groups: 8                                     │
╰──────────────────────────────────────────────────────────────────────────────╯
                                  interstitial
╭─────┬───────────────┬────────────────────────────┬───────────────────────────╮
│   # │ Group         │ Countries                  │ Waterfall                 │
├─────┼───────────────┼────────────────────────────┼───────────────────────────┤
│   1 │ Tier 1        │ US                         │ Bidding: Google, InMobi,  │
│     │               │                            │ Meta, UnityAds, ironSource│
│     │               │                            │ Manual:  AppLovin @ $1.00 │
├─────┼───────────────┼────────────────────────────┼───────────────────────────┤
│   2 │ Tier 2        │ AU, CA, DE, FR, GB, JP,    │ Bidding: Google, InMobi,  │
│     │               │ KR, NL, NZ, SA, TW         │ Meta, UnityAds, ironSource│
├─────┼───────────────┼────────────────────────────┼───────────────────────────┤
│   3 │ All Countries │ * (all)                    │ Bidding: Google, InMobi,  │
│     │               │                            │ Meta, UnityAds, ironSource│
╰─────┴───────────────┴────────────────────────────┴───────────────────────────╯
  ... (banner, rewarded, native tables follow the same pattern)

Snapshot saved to: snapshots/ss-ios.yaml
Settings saved to: settings/ss-ios.yaml
Networks saved to: networks.yaml
```

Three outputs are written from a single fetch:

- **Snapshot** (`snapshots/<alias>.yaml`) — full-fidelity capture of live API data (group IDs, instance IDs, rates, floor prices, A/B state). Lossless Pydantic round-trip. `snapshots/` is gitignored (ephemeral; regenerated by `pull`).
- **Settings** (`settings/<alias>.yaml`) — the editable per-app tier file (tracked).
- **Networks** (`networks.yaml`) — shared waterfall presets, merged across pulls.

> `--output` overrides the per-app settings file path only. The snapshot always writes to `snapshots/`, and `networks.yaml` is always shared.

| Flag | Purpose |
|------|---------|
| `--app` | Profile alias from `profiles.yaml` (required) |
| `--output`, `-o` | Override the per-app settings file path |

## Audit

Compare your per-app settings against live LevelPlay configs and report drift per app. Read-only — no writes.

```
$ admedi audit

         Audit Results
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━┓
┃ App              ┃ Status ┃ Issues      ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━┩
│ Shelf Sort …     │ DRIFT  │ 1 to update │
└──────────────────┴────────┴─────────────┘

1 change(s) across 1 app(s)
```

When everything matches:

```
$ admedi audit

         Audit Results
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ App              ┃ Status ┃ Issues          ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ Shelf Sort …     │ OK     │ All groups match│
└──────────────────┴────────┴─────────────────┘

All apps in sync.
```

Filter to a single app:

```bash
admedi audit --app ss-ios
```

> Exit code 0 = no drift, 1 = drift detected, 2 = error. Use the exit code in CI to gate deployments.

| Flag | Purpose |
|------|---------|
| `--app` | Filter to a specific profile alias (default: all apps in `profiles.yaml`) |
| `--format` | `text` (default) or `json` |

## Sync

Reconcile live LevelPlay groups to a source app's settings. `SOURCE` defines the desired state; `DESTINATION` is the app to write to (defaults to `SOURCE` for self-sync). Sync **creates, updates, and deletes** groups — and removes networks from waterfalls — to make live match. `--dry-run` is the single safety gate.

```
$ admedi sync ss-ios --dry-run

           Sync Preview
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ App              ┃ Group  ┃ Change                                  ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Shelf Sort …     │ Tier 2 │ UPDATE: Countries: added NZ; removed NL │
└──────────────────┴────────┴─────────────────────────────────────────┘

1 change(s) will be applied (0 create, 1 update, 0 delete)
```

Without `--dry-run`, sync applies and prints an apply summary:

```
$ admedi sync ss-ios

           ... (Sync Preview as above) ...

        Apply Results
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ App              ┃ Status  ┃ Created ┃ Updated ┃ Deleted ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ Shelf Sort …     │ SUCCESS │       0 │       1 │       0 │
└──────────────────┴─────────┴─────────┴─────────┴─────────┘

Summary: 1 success, 0 skipped, 0 failed
```

### Deprecating a network

Drop a network from a preset in `networks.yaml`, then sync each affected app — admedi removes that network from every live waterfall it appears in (across all formats and tiers), including default instances. Removal rides the existing `sync` path; no special flag.

```
$ admedi sync ss-google --dry-run

                                  Sync Preview
┃ App                           ┃ Group         ┃ Change
│ Shelf Sort - Organize & Match │ Tier 1        │ UPDATE: Waterfall: InMobi (bidder)
│                               │               │ is in live but not in preset (will be
│                               │               │ removed from the waterfall on sync)
│                               │ Tier 2        │ UPDATE: Waterfall: InMobi (bidder) …
│                               │ All Countries │ UPDATE: Waterfall: InMobi (bidder) …

3 change(s) will be applied (0 create, 3 update, 0 delete)
```

### Scoped sync

Pass no scope flag for a full sync (tiers + networks). Pass `--tiers` and/or `--networks` to narrow what is reconciled.

```bash
admedi sync ss-ios --tiers      # tier/country groups only
admedi sync ss-ios --networks   # waterfall membership/ordering only
```

### Cross-app sync

Apply one app's settings to a different app. Groups on the destination that aren't in the source are deleted:

```bash
admedi sync ss-ios ss-google --dry-run
```

> The sync pipeline has layered safety guards: dry-run preview, a pre-write snapshot of live state, A/B test detection (apps with an active A/B test are skipped wholesale), per-app isolation (one app failing doesn't affect others), abort-on-ambiguity for waterfall resolution, and post-write verification via a follow-up GET. Exit code 0 = applied or no drift, 1 = drift detected under `--dry-run`, 2 = error.

| Argument / Flag | Purpose |
|-----------------|---------|
| `SOURCE` | Source app alias — its settings define the desired state (required) |
| `DESTINATION` | Target app alias (defaults to `SOURCE` for self-sync) |
| `--tiers` | Sync tier/country definitions |
| `--networks` | Sync network waterfall configurations |
| `--dry-run` | Preview changes without applying (exits 1 if drift exists) |
| `--format` | `text` (default) or `json` |

## Status

Group counts, platforms, and last sync times for every app in `profiles.yaml`.

```
$ admedi status

              Portfolio Status (levelplay)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ App                          ┃ Platform ┃ Groups ┃ Last Sync        ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ Shelf Sort - Organize & Match│ iOS      │      8 │ 2026-06-04 14:30 │
│ Shelf Sort - Organize & Match│ Android  │      8 │ Never            │
└──────────────────────────────┴──────────┴────────┴──────────────────┘
```

| Flag | Purpose |
|------|---------|
| `--format` | `text` (default) or `json` |

## Development

```bash
git clone https://github.com/creational-ai-legacy/admedi.git
cd admedi
uv sync
```

```bash
# Unit tests (scope to tests/ — the repo root contains vendored reference repos)
uv run pytest tests/

# Integration tests (require live credentials; deselected by default)
set -a; . ./.env; set +a            # export creds into the environment first
uv run pytest tests/ -m integration -v -s
uv run pytest tests/integration/    # live shape probes (module-skip without creds)

# Lint and type check
uv run ruff check src/admedi/
uv run mypy src/admedi/
```

> Integration tests read credentials from the process environment (`os.getenv`), not from `.env` directly. Source `.env` first (`set -a; . ./.env; set +a`) — a bare run silently skips them and looks like a pass.

| Dependency | Purpose |
|------------|---------|
| `httpx` | Async HTTP for concurrent multi-app API calls |
| `pydantic` | Typed models with camelCase alias support |
| `pyjwt` | LevelPlay JWT auth tokens |
| `typer` + `rich` | CLI with styled tables and panels |
| `ruamel.yaml` | Round-trip YAML (preserves comments) |
| `fastmcp` | MCP server framework (post-core) |
| `python-dotenv` | `.env` credential loading |
| `ruff` + `mypy` | Linting and strict type checking |
| `pytest` + `pytest-asyncio` | Testing |

## License

MIT
