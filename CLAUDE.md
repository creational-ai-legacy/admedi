# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
- Install via GitHub, not PyPI: `pip install git+https://github.com/creational-ai-legacy/admedi`
- GitHub repo: `creational-ai-legacy/admedi` (repo moved here from the `creational-ai` org). Use the legacy identity for git remotes: `git@github-creational-legacy:creational-ai-legacy/admedi.git` (key `id_ed25519_creational`). The `docchang` account has only READ access.
- Existing open-source base to draw patterns from: `ironSource/mobile-api-lib-python` (abandoned Dec 2022, Apache-2.0)
- Licensing: Apache-2.0 for open-source core. Commercial `/ee` directory for SaaS features.

---

## Mission Control Integration

**This project is tracked in Mission Control portfolio system.**

When using Mission Control MCP tools (`mcp__mission-control__*`) to manage tasks, milestones, or project status, you are acting as the **PM (Project Manager) role**. Read these docs to understand the workflow, timestamp conventions, and scope:

- **Slug:** `admedi`
- **Role:** PM (Project Manager)
- **Read 1st:** `get_guide(name="PM_GUIDE")` - Project-level tactical execution
- **Read 2nd:** `get_guide(name="MCP_TOOLS_REFERENCE")` - Complete tool parameters

---

## Session Agents

This project uses session agents — see `.session-agents/agents.md` for the roster. Activation model and standard role definitions live in the `session-agents` skill at `~/.claude/skills/session-agents/`.

**Utility pane completion protocol:** Utility panes ack work via doorbell — `comms reply` (emits `[--reply]`) when you want completion confirmation, `comms no-reply` (emits `[--no-reply]`) when you don't. On `[--reply]` receipt the `/comms` command (1) **TaskCreates the reply as Protocol-step 0** with subject `[protocol] fire reply to <sender>` and description = the exact `comms no-reply <sender> -m "<task> done"` command (this puts the pending reply in CC's task list — the strongest forget-resistant surface the harness offers, addressing the documented "agent forgets reply at turn-end" failure mode), (2) runs the body (executes as slash command if `/`-prefixed, else acknowledges in conversation), (3) fires the doorbell as its final tool call and marks the step-0 task complete. On `[--no-reply]` receipt: same body-execution rule, no reply. No-marker wires fall back to no-reply mode AND emit a one-line conversation diagnostic. See `claude-code/session-agents/commands/comms.md` (deployed to `~/.claude/commands/comms.md`) for canonical protocol mechanics; `SKILL.md` § Persistent behaviors carries the "Fire pending doorbell reply before turn end" anchor.

---
