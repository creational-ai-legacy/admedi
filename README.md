# admedi

Define ad mediation tiers in YAML, diff against live configs, and sync across your entire app portfolio in one command.

* `admedi show --app <key>` — inspect live mediation groups with waterfall details, saves both a full-fidelity snapshot and modular settings
* `admedi audit` — diff your YAML template against live LevelPlay configs, report drift per app
* `admedi sync <source> [dest]` — sync settings to live LevelPlay configs: creates, updates, and deletes groups to match the source
* `admedi status` — group counts, platforms, and last sync times at a glance
* `profiles.yaml` aliases — use `--app mygame-ios` instead of `--app abc123def`

## Table of Contents

- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Show](#show)
- [Audit](#audit)
- [Sync](#sync)
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
git clone https://github.com/creational-ai/admedi.git
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
│ show    Show live mediation settings for an app.                             │
│ audit   Audit the portfolio for drift against the tier template.             │
│ sync    Sync settings to live LevelPlay mediation groups.                    │
│ status  Show current portfolio status.                                       │
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
  - app_key: "abc123def"
    name: "My Game iOS"
    platform: iOS
  - app_key: "def456ghi"
    name: "My Game Android"
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

### Profiles

`profiles.yaml` maps short aliases to LevelPlay app keys, so you can use `--app mygame-ios` instead of `--app abc123def`:

```yaml
profiles:
  mygame-ios: "abc123def"
  mygame-android: "def456ghi"
```

All `--app` flags across every command accept either a profile alias or a raw app key.

## Show

Inspect an app's live mediation settings, organized by ad format with waterfall details. Produces dual output from a single API fetch: a full-fidelity raw snapshot and derived modular settings.

```
$ admedi show --app mygame-ios

╭─────────────────────────────────── admedi ───────────────────────────────────╮
│ My Game                                                                      │
│ Platform: iOS  Key: abc123def  Groups: 8                                     │
╰──────────────────────────────────────────────────────────────────────────────╯
                                     banner
╭─────┬───────────────┬───────────┬────────────────────────────────────────────╮
│   # │ Group         │ Countries │ Waterfall                                  │
├─────┼───────────────┼───────────┼────────────────────────────────────────────┤
│   1 │ All Countries │ * (all)   │ Bidding: AdColony, AdMob, UnityAds         │
╰─────┴───────────────┴───────────┴────────────────────────────────────────────╯
                                  interstitial
╭─────┬───────────────┬────────────────────────────┬───────────────────────────╮
│   # │ Group         │ Countries                  │ Waterfall                 │
├─────┼───────────────┼────────────────────────────┼───────────────────────────┤
│   1 │ Tier 1        │ US                         │ Bidding: AdColony, AdMob, │
│     │               │                            │ Meta, Pangle, UnityAds    │
│     │               │                            │ Manual:  AppLovin @ $2.00 │
├─────┼───────────────┼────────────────────────────┼───────────────────────────┤
│   2 │ Tier 2        │ AU, CA, DE, GB, JP, KR,    │ Bidding: AdColony, AdMob, │
│     │               │ NZ, TW                     │ Meta, Pangle, UnityAds    │
├─────┼───────────────┼────────────────────────────┼───────────────────────────┤
│   3 │ Tier 3        │ FR, NL                     │ Bidding: AdColony, AdMob, │
│     │               │                            │ Meta, Pangle, UnityAds    │
├─────┼───────────────┼────────────────────────────┼───────────────────────────┤
│   4 │ All Countries │ * (all)                    │ Bidding: AdColony, AdMob, │
│     │               │                            │ Meta, Pangle, UnityAds    │
╰─────┴───────────────┴────────────────────────────┴───────────────────────────╯
  ... (rewarded, native tables follow the same pattern)

Snapshot saved to: snapshots/mygame-ios.yaml
Settings saved to: settings/mygame-ios.yaml
```

Two outputs are saved:

- **Snapshot** (`snapshots/{alias}.yaml`) — full-fidelity capture of live API data (group IDs, instance IDs, rates, floor prices, A/B test state). Lossless round-trip via Pydantic `model_dump`/`model_validate`.
- **Settings** (`settings/`) — derived modular view with per-app files: `{alias}.yaml` (tier names + waterfall refs), `{alias}-tiers.yaml` (country groupings), and `{alias}-networks.yaml` (waterfall presets).

> The `--output` flag overrides the settings file path only. Snapshots always write to `snapshots/`.

| Flag | Purpose |
|------|---------|
| `--app` | App key or profile alias (required) |
| `--output`, `-o` | Override settings file path |

## Audit

Compare your tier template against live LevelPlay configs and report drift per app.

```
$ admedi audit

              Audit Results
┏━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━┓
┃ App           ┃ Status ┃ Issues      ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━┩
│ My Game iOS   │ DRIFT  │ 1 to update │
└───────────────┴────────┴─────────────┘

1 change(s) across 1 app(s)
```

When everything matches:

```
$ admedi audit

              Audit Results
┏━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ App           ┃ Status ┃ Issues          ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ My Game iOS   │ OK     │ All groups match│
└───────────────┴────────┴─────────────────┘

All apps in sync.
```

Filter to a single app:

```bash
admedi audit --app mygame-ios
```

> Exit code 0 = no drift. Exit code 1 = drift detected. Use this in CI to gate deployments.

| Flag | Purpose |
|------|---------|
| `--config` | Path to YAML tier template (default: `admedi.yaml`) |
| `--app` | Filter to a specific app key or profile alias |
| `--format` | `text` (default) or `json` |

## Sync

Sync settings to live LevelPlay mediation groups. The source app's settings files (generated by `admedi show`) define the desired state — sync creates, updates, and deletes groups to make the destination match.

```
$ admedi sync mygame-ios --dry-run

                          Sync Preview
┏━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ App           ┃ Group  ┃ Change                                  ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ My Game iOS   │ Tier 2 │ UPDATE: Countries: added NZ; removed NL │
└───────────────┴────────┴─────────────────────────────────────────┘

1 change(s) will be applied (0 create, 1 update, 0 delete)
```

Without `--dry-run`, sync applies directly:

```
$ admedi sync mygame-ios

                          Sync Preview
┏━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ App           ┃ Group  ┃ Change                                  ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ My Game iOS   │ Tier 2 │ UPDATE: Countries: added NZ; removed NL │
└───────────────┴────────┴─────────────────────────────────────────┘

1 change(s) will be applied (0 create, 1 update, 0 delete)

              Apply Results
┏━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ App           ┃ Status  ┃ Created ┃ Updated ┃ Deleted ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ My Game iOS   │ SUCCESS │       0 │       1 │       0 │
└───────────────┴─────────┴─────────┴─────────┴─────────┘

Summary: 1 success, 0 skipped, 0 failed
```

Cross-app sync — apply one app's settings to a different app. Groups on the destination that don't exist in the source are deleted:

```
$ admedi sync mygame-ios mygame-android --dry-run

                          Sync Preview
┏━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ App                   ┃ Group  ┃ Change                            ┃
┡━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ My Game Android       │ Tier 2 │ UPDATE: Countries: added NZ       │
│                       │ Tier 3 │ DELETE                            │
└───────────────────────┴────────┴───────────────────────────────────┘

2 change(s) will be applied (0 create, 1 update, 1 delete)
```

> The sync pipeline has layered safety guards: dry-run preview before applying, pre-write snapshot of live state, A/B test detection (skips apps with active A/B tests), per-app isolation (one app failing doesn't affect others), and post-write verification via follow-up GET.

| Argument / Flag | Purpose |
|-----------------|---------|
| `SOURCE` | App alias whose settings files define the desired state (required) |
| `DESTINATION` | Target app alias to sync against (defaults to SOURCE for self-sync) |
| `--tiers` | Sync tier definitions (default if no scope flag given) |
| `--dry-run` | Preview changes without applying |
| `--format` | `text` (default) or `json` |

## Status

Show group counts, platforms, and last sync times for all portfolio apps.

```
$ admedi status

                    Portfolio Status (levelplay)
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ App              ┃ Platform ┃ Groups ┃ Last Sync        ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ My Game          │ iOS      │      8 │ 2026-03-12 14:30 │
│ My Game          │ Android  │      8 │ Never            │
└──────────────────┴──────────┴────────┴──────────────────┘
```

| Flag | Purpose |
|------|---------|
| `--config` | Path to YAML tier template (default: `admedi.yaml`) |
| `--format` | `text` (default) or `json` |

## Development

```bash
git clone https://github.com/creational-ai/admedi.git
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

MIT
