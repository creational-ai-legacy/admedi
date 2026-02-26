# Admedi

Config-as-code for ad mediation. Define country tiers in YAML, diff against live configs, sync across your entire app portfolio.

## What It Does

Admedi replaces manual dashboard clicking with a config-driven workflow:

1. **Define** country tiers and waterfall configs in a YAML template
2. **Diff** your template against live mediation platform configs via API
3. **Sync** changes across every app, platform, and ad format in one command

A studio with 6 games on 3 platforms has 18+ configuration surfaces. Admedi manages them all from a single source of truth.

## Architecture

Three-layer design with two adapter boundaries:

```
Interface Layer     CLI (typer) · MCP Server (FastMCP) · Python Library
                                    │
Core Engine         Loader (YAML) → Differ (diff) → Applier (sync)
                                    │
Adapter Layer       Mediation Adapters    Storage Adapters
                    └─ LevelPlay (MVP)    └─ Local File (default)
                    └─ MAX (future)       └─ SQLite
                    └─ AdMob (future)     └─ Postgres (SaaS)
```

Adding a new mediator or storage backend = implementing an interface.

## Installation

```bash
pip install git+https://github.com/creational-ai/admedi
```

## Configuration

Create a `.env` file with your mediation platform credentials:

```env
LEVELPLAY_SECRET_KEY=your_secret_key
LEVELPLAY_REFRESH_TOKEN=your_refresh_token
```

## Usage

```bash
# Sync tier template across all apps
admedi sync-tiers

# Audit live configs against your template
admedi audit

# Pull revenue data by country/tier
admedi revenue
```

Also available as an MCP server for AI agent workflows (Claude Code, Cursor, etc.) with generic tool names: `get_groups`, `sync_tiers`, `audit`.

## Tech Stack

- **Python 3.10+** with async/await throughout
- **httpx** for concurrent API calls
- **typer** for CLI
- **FastMCP** for MCP server
- **pydantic** for typed models
- **ruamel.yaml** for round-trip YAML

## Status

Pre-implementation — design and planning phase. LevelPlay adapter is the MVP target, built for managing [Mochibits](https://mochibits.com)' Shelf Sort portfolio.

## Roadmap

**Core** (internal tool) → **Open Source** (community launch) → **SaaS** (hosted offering) → **Multi-Mediator** (MAX, AdMob) → **Intelligence** (AI-powered tier recommendations)

## License

Apache-2.0 for the open-source core. Commercial features under `/ee`.

---

Built by [Creational.ai](https://creational.ai)
