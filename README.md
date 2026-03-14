# admedi

Define ad mediation tiers in YAML, diff against live configs, and sync across your entire app portfolio in one command.

* `admedi show --app <key>` — inspect live mediation groups with waterfall details, organized by ad format
* `admedi audit` — diff your YAML template against live LevelPlay configs, report drift per app
* `admedi sync-tiers` — preview and apply tier changes with dry-run, confirmation prompt, and post-write verification
* `admedi snapshot` — export current mediation groups to YAML for a single app or the whole portfolio
* `admedi status` — group counts, platforms, and last sync times at a glance
* `profiles.yaml` aliases — use `--app ss-ios` instead of `--app 1f93a90ad`

## Table of Contents

- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Show](#show)
- [Audit](#audit)
- [Sync](#sync)
- [Snapshot](#snapshot)
- [Status](#status)
- [Development](#development)
- [License](#license)

## Getting started

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
pip install git+https://github.com/creational-ai/admedi
```

Or clone for development:

```bash
git clone git@github-creational:creational-ai/admedi.git
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

```bash
$ admedi --help

Usage: admedi [OPTIONS] COMMAND [ARGS]...

 Config-driven ad mediation management

╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ show        Show live mediation settings for an app.                         │
│ audit       Audit the portfolio for drift against the tier template.         │
│ sync-tiers  Sync tier template changes to live LevelPlay mediation groups.   │
│ snapshot    Export a YAML snapshot of current mediation groups.              │
│ status      Show current portfolio status.                                   │
╰──────────────────────────────────────────────────────────────────────────────╯
```

> Get credentials from the ironSource / Unity LevelPlay dashboard under API Keys.

## Configuration

### Tier template

The tier template (`admedi.yaml` by default) defines your desired mediation state. Every command that operates on the portfolio reads from this file.

```yaml
schema_version: 1
mediator: levelplay

portfolio:
  - app_key: "1f93a90ad"
    name: "Shelf Sort iOS"
    platform: iOS
  - app_key: "2a84b91be"
    name: "Shelf Sort Android"
    platform: Android

tiers:
  # Position 1 = highest priority, checked first by the SDK
  - name: "Tier 1"
    countries: ["US"]
    position: 1
    ad_formats: [interstitial, rewarded]

  - name: "Tier 2"
    countries: ["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"]
    position: 2
    ad_formats: [interstitial, rewarded]

  - name: "Tier 3"
    countries: ["FR", "NL"]
    position: 3
    ad_formats: [interstitial, rewarded]

  # Exactly one tier must be is_default: true
  - name: "All Countries"
    countries: ["*"]         # "*" = catch-all for unassigned countries
    position: 4
    is_default: true
    ad_formats: [banner, interstitial, rewarded, native]
```

Each tier is scoped per `ad_formats` — a tier with `[interstitial, rewarded]` creates separate mediation groups for each format in LevelPlay. Country codes are ISO 3166-1 alpha-2.

> See `examples/shelf-sort-tiers.yaml` for a fully commented production template.

### Profiles

`profiles.yaml` maps short aliases to LevelPlay app keys, so you can use `--app ss-ios` instead of `--app 1f93a90ad`:

```yaml
profiles:
  ss-ios: "1f93a90ad"
  ss-google: "1f93aca35"
  hexar-ios: "676996cd"
  hexar-google: "67695d45"
```

All `--app` flags across every command accept either a profile alias or a raw app key.

## Show

Inspect an app's live mediation settings, organized by ad format with waterfall details.

```bash
$ admedi show --app ss-ios

╭──────────────────── admedi ────────────────────╮
│ Shelf Sort - Organize & Match                  │
│ Platform: iOS  Key: 1f93a90ad  Groups: 10      │
╰────────────────────────────────────────────────╯
╭───────────────────────────── interstitial ─────────────────────────────╮
│ # │ Group          │ Countries                     │ Waterfall         │
├───┼────────────────┼───────────────────────────────┼───────────────────┤
│ 1 │ Tier 1         │ US                            │ Bidding: Pangle,  │
│   │                │                               │ Meta, Unity Ads   │
├───┼────────────────┼───────────────────────────────┼───────────────────┤
│ 2 │ Tier 2         │ AU, CA, DE, GB, JP, NZ, KR,  │ Bidding: Pangle,  │
│   │                │ TW                            │ Meta, Unity Ads   │
├───┼────────────────┼───────────────────────────────┼───────────────────┤
│ 4 │ All Countries  │ * (all)                       │ Bidding: Pangle,  │
│   │                │                               │ Meta, Unity Ads   │
╰───┴────────────────┴───────────────────────────────┴───────────────────╯

Snapshot saved to: settings/ss-ios.yaml
```

The command also saves a modular snapshot — a shared `settings/networks.yaml` for waterfall presets, `settings/tiers.yaml` for country groupings, and a per-app file.

| Flag | Purpose |
|------|---------|
| `--app` | App key or profile alias (required) |
| `--output`, `-o` | Override snapshot file path |

## Audit

Compare your tier template against live LevelPlay configs and report drift per app.

```bash
$ admedi audit --config examples/shelf-sort-tiers.yaml

              Audit Results
┏━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━┓
┃ App            ┃ Status ┃ Issues      ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━┩
│ Shelf Sort iOS │ DRIFT  │ 1 to update │
└────────────────┴────────┴─────────────┘

1 change(s) across 1 app(s)
```

When everything matches:

```bash
$ admedi audit

              Audit Results
┏━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ App            ┃ Status ┃ Issues          ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ Shelf Sort iOS │ OK     │ All groups match│
└────────────────┴────────┴─────────────────┘

All apps in sync.
```

Filter to a single app:

```bash
admedi audit --app ss-ios
```

> Exit code 0 = no drift. Exit code 1 = drift detected. Use this in CI to gate deployments.

| Flag | Purpose |
|------|---------|
| `--config` | Path to YAML tier template (default: `admedi.yaml`) |
| `--app` | Filter to a specific app key or profile alias |
| `--format` | `text` (default) or `json` |

## Sync

Preview and apply tier template changes to live LevelPlay mediation groups.

```bash
$ admedi sync-tiers --config examples/shelf-sort-tiers.yaml --dry-run

                            Sync Preview
┏━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ App            ┃ Group  ┃ Change                                  ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Shelf Sort iOS │ Tier 2 │ UPDATE: Countries: added NZ; removed NL │
└────────────────┴────────┴─────────────────────────────────────────┘

1 change(s) will be applied (0 create, 1 update)
```

Apply changes (prompts for confirmation):

```bash
$ admedi sync-tiers --config examples/shelf-sort-tiers.yaml

                            Sync Preview
┏━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ App            ┃ Group  ┃ Change                                  ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Shelf Sort iOS │ Tier 2 │ UPDATE: Countries: added NZ; removed NL │
└────────────────┴────────┴─────────────────────────────────────────┘

1 change(s) will be applied (0 create, 1 update)
Apply these changes? [y/n]: y

              Apply Results
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ App            ┃ Status  ┃ Created ┃ Updated ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ Shelf Sort iOS │ SUCCESS │       0 │       1 │
└────────────────┴─────────┴─────────┴─────────┘

Summary: 1 success, 0 skipped, 0 failed
```

Skip confirmation with `--yes`:

```bash
admedi sync-tiers --yes
```

> The sync pipeline has layered safety guards: dry-run preview before applying, confirmation prompt (unless `--yes`), pre-write snapshot of live state, A/B test detection (skips apps with active A/B tests), per-app isolation (one app failing doesn't affect others), and post-write verification via follow-up GET.

| Flag | Purpose |
|------|---------|
| `--config` | Path to YAML tier template (default: `admedi.yaml`) |
| `--app` | Filter to a specific app key or profile alias |
| `--dry-run` | Preview changes without applying |
| `--yes`, `-y` | Skip confirmation prompt |
| `--format` | `text` (default) or `json` |

## Snapshot

Export current mediation groups to a YAML file.

```bash
$ admedi snapshot --app 1f93a90ad

╭──────── Snapshot Export ─────────╮
│ Snapshot saved for 1f93a90ad     │
│ Path: 1f93a90ad_snapshot.yaml    │
╰──────────────────────────────────╯
```

Snapshot all portfolio apps at once:

```bash
$ admedi snapshot --all --config examples/shelf-sort-tiers.yaml

╭──────── Snapshot Export ─────────╮
│ Snapshot saved for Shelf Sort iOS│
│ Path: 1f93a90ad_snapshot.yaml    │
╰──────────────────────────────────╯
╭──────── Snapshot Export ──────────────╮
│ Snapshot saved for Shelf Sort Android │
│ Path: 2a84b91be_snapshot.yaml         │
╰───────────────────────────────────────╯
```

> `--app` and `--all` are mutually exclusive. `--all` requires `--config` to know which apps are in the portfolio.

| Flag | Purpose |
|------|---------|
| `--app` | App key or profile alias to snapshot |
| `--all` | Snapshot all portfolio apps (requires `--config`) |
| `--config` | Path to YAML tier template (required with `--all`) |
| `--output`, `-o` | Output file path (or directory with `--all`) |

## Status

Show group counts, platforms, and last sync times for all portfolio apps.

```bash
$ admedi status --config examples/shelf-sort-tiers.yaml

                      Portfolio Status (levelplay)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ App                           ┃ Platform ┃ Groups ┃ Last Sync        ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ Shelf Sort - Organize & Match │ iOS      │     10 │ 2026-03-12 14:30 │
│ Shelf Sort - Organize & Match │ Android  │     10 │ Never            │
└───────────────────────────────┴──────────┴────────┴──────────────────┘
```

| Flag | Purpose |
|------|---------|
| `--config` | Path to YAML tier template (default: `admedi.yaml`) |
| `--format` | `text` (default) or `json` |

## Development

```bash
git clone git@github-creational:creational-ai/admedi.git
cd admedi
uv sync --extra dev
```

```bash
# Unit tests (excludes integration tests)
uv run pytest tests/ -v

# Integration tests (requires credentials)
uv run pytest tests/ -m integration -v -s

# Lint and type check
uv run ruff check src/admedi/
uv run mypy src/admedi/
```

| Dependency | Purpose |
|------------|---------|
| `httpx` | Async HTTP for concurrent multi-app API calls |
| `pydantic` | Typed models with camelCase alias support |
| `typer` + `rich` | CLI with styled tables and panels |
| `ruamel.yaml` | Round-trip YAML (preserves comments) |
| `fastmcp` | MCP server framework |
| `python-dotenv` | `.env` credential loading |
| `ruff` + `mypy` | Linting and strict type checking |
| `pytest` + `pytest-asyncio` | Testing |

## License

Apache-2.0 for the open-source core. Commercial features under `/ee`.
