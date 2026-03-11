# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Admedi is a config-driven ad mediation management tool by Creational.ai. It replaces manual dashboard clicking with config-as-code: define country tiers in YAML, diff against live mediation configs via platform APIs, and sync across an entire app portfolio. The immediate use case is managing Mochibits' Shelf Sort portfolio (6 apps x 3 platforms = 18 LevelPlay configuration surfaces).

**Status**: Pre-implementation (planning/design phase). No source code yet — only design documents exist in `docs/`.

## Repository Structure

```
admedi/
├── CLAUDE.md
├── admedi.code-workspace
└── docs/
    ├── core-milestone-spec.md          # Detailed spec for Core milestone (current focus)
    ├── shelf-sort-mediation-tiers.md   # Shelf Sort tier definitions (interstitial)
    └── references/
        ├── admedi-architecture.md     # System architecture, data model, tech stack
        ├── admedi-vision.md           # Product vision, scope, distribution channels
        ├── admedi-roadmap.md          # Full milestone roadmap (Core → OS → SaaS → Multi-Mediator → Intelligence)
        ├── admedi-market-research.md  # Competitive landscape and market analysis
        └── levelplay-api-reference.md  # LevelPlay REST API endpoints, auth, rate limits
```

## Planned Package Structure

When implementation begins, the Python package will be organized as:

```
admedi/
├── adapters/       # MediationAdapter + StorageAdapter implementations
├── engine/         # ConfigEngine: Loader, Differ, Applier
├── cli/            # typer-based CLI commands
├── mcp/            # FastMCP server
├── storage/        # Local file, PostgreSQL (RDS/Supabase) adapters
└── models/         # Pydantic models for 9 core entities
```

## Technology Stack

- **Language**: Python 3.14+ (match/case, type unions, improved error messages, performance)
- **HTTP**: `httpx` (async) — concurrent multi-app API calls
- **CLI**: `typer` — type-hint-driven, auto-generated help
- **MCP**: `FastMCP` — Creational.ai's standard MCP framework
- **Validation**: `pydantic` — typed models for configs and API payloads
- **YAML**: `ruamel.yaml` — preserves comments and formatting on round-trip
- **Credentials**: `python-dotenv` — `.env` file with `LEVELPLAY_SECRET_KEY` and `LEVELPLAY_REFRESH_TOKEN`
- **Testing**: `pytest` + `pytest-asyncio`
- **Linting**: `ruff`, `mypy`

## Architecture

Three-layer design with two adapter boundaries:

1. **Interface Layer**: Python library, CLI (typer), FastMCP server, Cowork plugin (future)
2. **Core Engine (ConfigEngine)**: Loader (YAML → TierTemplate) → Differ (local vs remote) → Applier (push changes)
3. **Adapter Layer**: Mediation adapters (LevelPlay MVP; MAX, AdMob future) + Storage adapters (local file default; PostgreSQL via async SQLAlchemy Core for RDS/Supabase)

Key design patterns:
- **Dual adapter interfaces**: `MediationAdapter` (12 methods for platform API CRUD) and `StorageAdapter` (5 methods for persistence). Adding a new mediator or storage backend = implementing an interface.
- **Generic MCP tool names**: `get_groups`, `sync_tiers`, `audit` — not `levelplay_get_groups`. Active mediator determined by config.
- **Async-first**: All HTTP calls, MCP server, and DB adapters use async/await.
- **Atomic per-app operations**: Partial portfolio failure is reported, not fatal.

## Core Data Model

9 pydantic entities: `App`, `TierTemplate`, `Group`, `WaterfallConfig`, `Instance`, `Placement`, `SyncLog`, `ConfigSnapshot`, `Credential`.

## LevelPlay API

- **Base URL**: `https://platform.ironsrc.com`
- **Auth**: OAuth 2.0 Bearer (secretKey + refreshToken → JWT, 60-min expiry, auto-refresh)
- **Key endpoints**: Groups API v4 (`/levelPlay/groups/v4/{appKey}`), Mediation Mgmt v2 (legacy), Instances v1, Placements v1, Reporting v1
- **Rate limits**: Groups 4K/30min, Instances 8K/30min, Reporting 8K/hr
- **Gotcha**: Mediation Management API fails if an active A/B test is running on the app
- **Batch ops**: Instances API rejects entire batch if any single item fails

## Shelf Sort Tier Configuration

| Tier | Countries |
|------|-----------|
| Tier 1 | US |
| Tier 2 | AU, CA, DE, GB, JP, NZ, KR, TW |
| Tier 3 | FR, NL |
| All Countries | Everything else |

Ad formats: banner, interstitial, rewarded_video

## Implementation Phases (Core Milestone)

1. **Foundation**: Repo scaffolding, pydantic models, LevelPlay adapter (auth + read endpoints)
2. **ConfigEngine**: YAML template format, Loader, Differ, Applier
3. **Interfaces**: CLI commands, FastMCP server, local file storage adapter
4. **Dogfood**: Real Shelf Sort portfolio management for 2+ weeks

## Licensing

Apache-2.0 for open-source core. Commercial `/ee` directory for SaaS features (multi-tenant, scheduled syncs, audit dashboard).

## Key References

- Existing open-source base to draw patterns from: `ironSource/mobile-api-lib-python` (abandoned Dec 2022, Apache-2.0)
- Install via GitHub, not PyPI: `pip install git+https://github.com/creational-ai/admedi`
- GitHub org: `creational-ai` (use `git@github-creational:creational-ai/` for remote URLs)

---

## Mission Control Integration

**This project is tracked in Mission Control portfolio system.**

When using Mission Control MCP tools (`mcp__mission-control__*`) to manage tasks, milestones, or project status, you are acting as the **PM (Project Manager) role**. Read these docs to understand the workflow, timestamp conventions, and scope:

- **Slug:** `admedi`
- **Role:** PM (Project Manager)
- **Read 1st:** `get_guide(name="PM_GUIDE")` - Project-level tactical execution
- **Read 2nd:** `get_guide(name="MCP_TOOLS_REFERENCE")` - Complete tool parameters

---
