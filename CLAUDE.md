# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This workspace uses specialized agents. See `agents.md` for role definitions.

## Project Overview

Admedi is a config-driven ad mediation management tool by Creational.ai. It replaces manual dashboard clicking with config-as-code: define country tiers in YAML, diff against live mediation configs via platform APIs, and sync across an entire app portfolio. The immediate use case is managing Mochibits' Shelf Sort portfolio (6 apps x 3 platforms = 18 LevelPlay configuration surfaces).

See `PROJECT_STATE.md` for current status and progress. See `README.md` for how the tool works.

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

## Key References

- Architecture, data model, API details: see `docs/references/`
- Install via GitHub, not PyPI: `pip install git+https://github.com/creational-ai/admedi`
- GitHub org: `creational-ai` (use `git@github-creational:creational-ai/` for remote URLs)
- Existing open-source base to draw patterns from: `ironSource/mobile-api-lib-python` (abandoned Dec 2022, Apache-2.0)
- Licensing: Apache-2.0 for open-source core. Commercial `/ee` directory for SaaS features.

---

## Agent Roles

Roles are **assigned per session** — do not assume any role unless the user explicitly activates it (e.g., "You are the examiner"). See `agents.md` for full role definitions.

---

## Mission Control Integration

**This project is tracked in Mission Control portfolio system.**

When using Mission Control MCP tools (`mcp__mission-control__*`) to manage tasks, milestones, or project status, you are acting as the **PM (Project Manager) role**. Read these docs to understand the workflow, timestamp conventions, and scope:

- **Slug:** `admedi`
- **Role:** PM (Project Manager)
- **Read 1st:** `get_guide(name="PM_GUIDE")` - Project-level tactical execution
- **Read 2nd:** `get_guide(name="MCP_TOOLS_REFERENCE")` - Complete tool parameters

---
