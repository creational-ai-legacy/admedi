# Admedi — Ad Mediation, Codified

Define country tiers in YAML, diff against live configs, sync across your entire app portfolio.

## What It Does

Admedi replaces manual dashboard clicking with a config-driven workflow:

1. **Define** country tiers and waterfall configs in a YAML template
2. **Diff** your template against live mediation platform configs via API
3. **Sync** changes across every app, platform, and ad format in one command

A studio with 6 games on 3 platforms has 18+ configuration surfaces. Admedi manages them all from a single source of truth.

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) package manager

## Development Setup

```bash
# Clone
git clone git@github-creational:creational-ai/admedi.git
cd admedi

# Install (creates venv, installs all dependencies)
uv sync

# Verify
uv run python -c "import admedi; print(admedi.__version__)"
# → 0.1.0
```

### Running Tests

```bash
# Full test suite
uv run pytest tests/ -v

# Specific component
uv run pytest tests/test_foundation_models.py -v
uv run pytest tests/test_foundation_adapters.py -v

# Linting and type checking
uv run ruff check src/admedi/
uv run mypy src/admedi/
```

## Configuration

Create a `.env` file from the template:

```bash
cp .env.example .env
```

Then fill in your LevelPlay credentials (from the ironSource/Unity LevelPlay dashboard):

```env
LEVELPLAY_SECRET_KEY=your_secret_key
LEVELPLAY_REFRESH_TOKEN=your_refresh_token
```

## Library Usage

### Models

All 9 data models use pydantic v2 with camelCase alias support for direct API compatibility:

```python
from admedi.models import App, Platform, Mediator

# Parse a LevelPlay API response directly (camelCase keys)
app = App.model_validate({
    "appKey": "1abc2def3",
    "appName": "Shelf Sort",
    "platform": "Android",
    "bundleId": "com.mochibits.shelfsort",
})
print(app.app_key)      # "1abc2def3" (snake_case Python access)
print(app.platform)     # Platform.ANDROID

# Serialize back to API format (camelCase keys)
api_payload = app.model_dump(by_alias=True)
# → {"appKey": "1abc2def3", "appName": "Shelf Sort", ...}
```

### Tier Templates

Define country-based tier configurations with built-in validation:

```python
from admedi.models import TierTemplate, TierDefinition, AdFormat

template = TierTemplate(
    name="Shelf Sort",
    ad_formats=[AdFormat.BANNER, AdFormat.INTERSTITIAL, AdFormat.REWARDED_VIDEO],
    tiers=[
        TierDefinition(name="Tier 1", countries=["US"], position=1),
        TierDefinition(name="Tier 2", countries=["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"], position=2),
        TierDefinition(name="Tier 3", countries=["FR", "NL"], position=3),
        TierDefinition(name="All Countries", countries=[], position=4, is_default=True),
    ],
)
# Validators enforce: exactly one default tier, no duplicate countries, valid country codes
```

### Waterfall Configs

Nested models for group/waterfall/instance structures with cross-field validation:

```python
from admedi.models import Group, WaterfallConfig, WaterfallTier, Instance, TierType, AdFormat

group = Group.model_validate({
    "groupId": 100,
    "groupName": "Banner US",
    "adFormat": "banner",
    "countries": ["US"],
    "position": 1,
    "waterfall": {
        "tier1": {
            "tierType": "manual",
            "instances": [
                {"id": 1, "name": "IS", "networkName": "ironSource", "isBidder": False}
            ]
        }
    }
})
# WaterfallConfig rejects OPTIMIZED + bidding tier combinations automatically
```

### Exceptions

Typed exception hierarchy for targeted error handling:

```python
from admedi import AdmediError, ApiError, RateLimitError, AuthError

try:
    # ... API call
    pass
except RateLimitError as e:
    print(f"Rate limited, retry after {e.retry_after}s")
except ApiError as e:
    print(f"API error {e.status_code}: {e.message}")
except AdmediError as e:
    print(f"Admedi error: {e.message}")
```

### Network Credential Registry

Validate network-specific credential fields for all 34 supported ad networks:

```python
from admedi.networks import validate_network_credentials

errors = validate_network_credentials("AppLovin", {"sdkKey": "abc", "zoneId": "123"})
# → [] (valid)

errors = validate_network_credentials("AppLovin", {"sdkKey": "abc"})
# → ["Missing required field 'zoneId' ..."]
```

### Adapter Interfaces

Abstract base classes for building mediation and storage adapters:

```python
from admedi.adapters import MediationAdapter, StorageAdapter, AdapterCapability

# MediationAdapter: 12 async methods (authenticate, list_apps, get_groups, ...)
# StorageAdapter: 5 async methods (save_config, load_config, ...)
# AdapterCapability: 8 capabilities (READ_GROUPS, WRITE_GROUPS, ...)

# ensure_capability() checks at runtime:
# adapter.ensure_capability(AdapterCapability.WRITE_GROUPS)
# → raises AdapterNotSupportedError if not supported
```

## Architecture

Three-layer design with two adapter boundaries:

```
Interface Layer     CLI (typer) · MCP Server (FastMCP) · Python Library
                                    │
Core Engine         Loader (YAML) → Differ (diff) → Applier (sync)
                                    │
Adapter Layer       Mediation Adapters    Storage Adapters
                    └─ LevelPlay (MVP)    └─ Local File (default)
                    └─ MAX (future)       └─ PostgreSQL (RDS/Supabase)
                    └─ AdMob (future)
```

Adding a new mediator or storage backend = implementing an interface.

## Package Structure

```
src/admedi/
├── __init__.py              # Version, top-level exception re-exports
├── constants.py             # LevelPlay API URL constants
├── exceptions.py            # AdmediError hierarchy (5 subclasses)
├── networks.py              # 34-network credential registry
├── models/
│   ├── enums.py             # AdFormat, Platform, TierType, Mediator, Networks
│   ├── app.py               # App
│   ├── credential.py        # Credential
│   ├── tier_template.py     # TierTemplate, TierDefinition (frozen, validated)
│   ├── group.py             # Group
│   ├── waterfall.py         # WaterfallConfig, WaterfallTier (validated)
│   ├── instance.py          # Instance, CountryRate
│   ├── placement.py         # Placement, Capping, Pacing (bool normalization)
│   ├── sync_log.py          # SyncLog
│   └── config_snapshot.py   # ConfigSnapshot
├── adapters/
│   ├── mediation.py         # MediationAdapter ABC, AdapterCapability
│   └── storage.py           # StorageAdapter ABC
├── engine/                  # (planned) ConfigEngine: Loader, Differ, Applier
├── cli/                     # (planned) typer CLI
├── mcp/                     # (planned) FastMCP server
└── storage/                 # (planned) Storage adapter implementations
```

## Tech Stack

- **Python 3.14+** with async/await throughout
- **pydantic v2** for typed models with camelCase alias support
- **httpx** for concurrent API calls
- **typer** for CLI
- **FastMCP** for MCP server
- **ruamel.yaml** for round-trip YAML
- **ruff** + **mypy** (strict) for linting and type checking

## Status

**Foundation complete** — all data models, adapter interfaces, typed exceptions, and network registry are implemented with 500+ tests, zero ruff errors, and zero mypy errors.

**Next up**: LevelPlay authentication adapter (OAuth 2.0 token refresh + list_apps).

## Roadmap

**Core** (internal tool) → **Open Source** (community launch) → **SaaS** (hosted offering) → **Multi-Mediator** (MAX, AdMob) → **Intelligence** (AI-powered tier recommendations)

## License

Apache-2.0 for the open-source core. Commercial features under `/ee`.

---

Built by [Creational.ai](https://creational.ai)
