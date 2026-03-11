# Project State: Admedi

> **Last Updated**: 2026-02-27T21:57:48-0800

**Admedi** is a config-driven ad mediation management tool that replaces manual dashboard clicking with config-as-code: define country tiers in YAML, diff against live mediation configs via platform APIs, and sync across an entire app portfolio.

**Current Status**: Core milestone in progress -- Foundation task complete with 9 pydantic v2 models, abstract adapter interfaces, typed exceptions, and 509 passing tests.

---

## Progress

### Milestone: Core

| ID | Name | Type | Status | Tests | Docs |
|-----|------|------|--------|-------|------|
| foundation | Project Foundation | foundation | ✅ Complete | 509 passing | `core-foundation-*.md` |
| auth | LevelPlay Authentication | feature | ⬜ Pending | -- | -- |
| config-engine | ConfigEngine (Loader, Differ, Applier) | feature | ⬜ Pending | -- | -- |
| interfaces | CLI + MCP + Local Storage | feature | ⬜ Pending | -- | -- |
| dogfood | Shelf Sort Dogfood | validation | ⬜ Pending | -- | -- |

**Total Tests**: 509 passing (100% pass rate)

---

## Key Decisions

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-02-27 | `setuptools.build_meta` over `_legacy` backend | The `_legacy` backend path does not exist; `build_meta` is the standard PEP 517 backend |
| 2026-02-27 | camelCase for network registry field names | Majority pattern across networks; outliers documented in `_UNVERIFIED_CASING` |
| 2026-02-27 | Fyber `adSpotId` over lib's `adSoptId` | The lib's spelling is almost certainly a typo |
| 2026-02-27 | `frozen=True` for TierTemplate only | Tier configs are declarative YAML templates; API entity models may need mutation during normalization |

---

## What's Next

**Recommended Next Steps**:
1. LevelPlay Authentication -- OAuth 2.0 bearer token flow, auto-refresh, `list_apps` endpoint
2. ConfigEngine -- YAML template loader, differ (local vs remote), applier (push changes)
3. CLI + MCP interfaces and local file storage adapter

**System Status**: ✅ **Foundation Complete**
- 9 pydantic v2 models with full validation
- Abstract adapter interfaces (MediationAdapter + StorageAdapter)
- 34-network credential registry
- 509 tests, zero lint/type errors

---

## Latest Health Check

### 2026-02-27 - Core Foundation Task Finalization
**Status**: ✅ On Track

**Context**:
Foundation task completed -- the first task in the Core milestone. All 10 implementation steps finished with zero deviations from plan. This health check runs as part of task finalization.

**Findings**:
- ✅ All work aligns with the Core milestone's first phase (repo scaffolding, models, adapters) as defined in the roadmap
- ✅ Models derived from real LevelPlay API response shapes with camelCase alias support -- production-grade data layer
- ✅ Architecture matches the three-layer design (Interface, Core Engine, Adapter) from the architecture doc
- ✅ No scope drift -- implementation matches plan exactly across all 10 steps
- ✅ Complexity is proportionate: 22 source files for 9 models + 5 enums + exceptions + adapters + network registry
- ✅ 509 tests passing, zero ruff errors, zero mypy errors across 22 source files

**Challenges**:
- `setuptools.backends._legacy:_Backend` was specified in the design but does not exist; resolved by using `setuptools.build_meta`
- ironSource lib has inconsistent field casing across 34 networks; resolved by normalizing to camelCase and documenting all deviations in `_UNVERIFIED_CASING`

**Results**:
- ✅ Installable Python package with `uv sync` and all 6 subpackages importable
- ✅ 9 pydantic v2 models with validation, boolean normalization, and serialization round-trips
- ✅ Abstract adapter interfaces with capability negotiation pattern
- ✅ Data-driven network credential registry covering all 34 LevelPlay networks
- ✅ Typed exception hierarchy with 5 specific subclasses
- ✅ Shelf Sort TierTemplate constructs and validates correctly

**Lessons Learned**:
- ironSource lib field casing is inconsistent and contains at least one typo (Fyber `adSoptId`) -- integration testing will be essential for verifying the credential registry
- The `(str, Enum)` pattern behavior changed in Python 3.14 regarding `str()` output -- use `.value` explicitly for API strings

**Next**: LevelPlay Authentication (OAuth 2.0 bearer token, auto-refresh, `list_apps` concrete adapter method)
