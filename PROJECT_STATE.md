# Project State: Admedi

> **Last Updated**: 2026-03-12T19:08:51-0700

**Admedi** is a config-driven ad mediation management tool that replaces manual dashboard clicking with config-as-code: define country tiers in YAML, diff against live mediation configs via platform APIs, and sync across an entire app portfolio.

**Current Status**: Core milestone nearing completion -- Foundation, Auth-Reads, and Config-Engine tasks complete. Full config-as-code pipeline operational: YAML template loading, diffing, applying via CLI, and local audit storage. 1098 tests passing. Next: Dogfood on Shelf Sort portfolio.

---

## Progress

### Milestone: Core

| ID | Name | Type | Status | Tests | Docs |
|-----|------|------|--------|-------|------|
| foundation | Project Foundation | foundation | ✅ Complete | 509 passing | `core-foundation-*.md` |
| auth-reads | LevelPlay Auth + Core Reads | feature | ✅ Complete | 205 passing | `core-auth-reads-*.md` |
| config-engine | ConfigEngine + Group Writes + CLI + Storage | feature | ✅ Complete | 386 passing | `core-config-engine-*.md` |
| dogfood | Shelf Sort Dogfood | validation | ⬜ Pending | -- | -- |

**Post-Core (deferred)**: Instance CRUD, placement ops, reporting API, MCP server, `admedi revenue`, `admedi manage-instances`

**Total Tests**: 1098 passing (100% pass rate), 10 deselected (integration markers)

---

## Key Decisions

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-11 | Revised task plan: 3 tasks to dogfood (down from 4) | Merged auth+reads, merged engine+CLI+storage, deferred MCP and non-tier-sync ops. Faster path to working `admedi sync-tiers`. |
| 2026-03-11 | Defer MCP server to post-core | CLI-first approach -- once it works locally, adding cloud/AI interfaces is straightforward. Easier to debug. |
| 2026-03-11 | Defer instance CRUD, placements, reporting | Independent of tier sync critical path. Shelf Sort apps already have all instances configured -- we just rearrange tiers. |
| 2026-02-27 | `setuptools.build_meta` over `_legacy` backend | The `_legacy` backend path does not exist; `build_meta` is the standard PEP 517 backend |
| 2026-02-27 | camelCase for network registry field names | Majority pattern across networks; outliers documented in `_UNVERIFIED_CASING` |
| 2026-02-27 | Fyber `adSpotId` over lib's `adSoptId` | The lib's spelling is almost certainly a typo |
| 2026-02-27 | `frozen=True` for TierTemplate only | Tier configs are declarative YAML templates; API entity models may need mutation during normalization |

---

## What's Next

**Recommended Next Steps**:
1. Run integration tests with real credentials to validate adapter + write operations against live LevelPlay API
2. Portfolio Dogfood -- real Shelf Sort validation for 2+ weeks with `admedi sync-tiers`
3. After dogfood: open-source packaging, MCP server, extended operations

**System Status**: ✅ **Config-Engine Complete**
- Full config-as-code pipeline: YAML -> Loader -> Differ -> Applier -> CLI
- 4 CLI commands: audit, sync-tiers, snapshot, status
- Group write operations: create, update, delete via LevelPlay v4 API
- Local file storage: JSONL sync logs, per-snapshot JSON files in .admedi/
- Safety: dry-run default, pre-write snapshots, A/B test detection, per-app error isolation
- 1098 tests passing, zero regressions

---

## Latest Health Check

### 2026-03-12 - Config-Engine Task Finalization
**Status**: ✅ On Track

**Context**:
Config-Engine task completed -- the third task in the Core milestone. All 14 implementation steps (Steps 0-13) finished with zero deviations from plan. The full config-as-code pipeline is now operational: YAML template loading, diffing against live API state, applying changes via CLI, and persisting audit trails locally.

**Findings**:
- ✅ Work aligns with Core milestone phase 3 (engine + CLI + storage) as defined in the milestone spec
- ✅ Architecture follows the three-layer design: ConfigEngine orchestrates Loader/Differ/Applier; CLI delegates to engine; storage is pluggable via StorageAdapter interface
- ✅ Production-grade safety: dry-run default, pre-write snapshots, A/B test detection at both diff-time and apply-time, per-app error isolation, post-write verification
- ✅ No scope drift -- all 14 steps implemented per plan with zero deviations
- ✅ Complexity proportionate: engine (5 modules), CLI (2 modules), storage (1 module), models (4 modules) -- clean separation of concerns
- ✅ 1098 tests passing (712 existing + 386 new config engine), 10 integration tests deselected by default

**Challenges**:
- LevelPlay Groups v4 API uses different field names for POST vs PUT -- silent acceptance of wrong field names required careful mapping per endpoint
- Python 3.14 changed `str()` behavior on `(str, Enum)` subclasses, requiring test assertion updates in Step 2a
- Export-tracking tests in `test_foundation_final.py` required updates at Steps 1, 2a, 2b as new models were added to `__init__.py`

**Results**:
- ✅ ConfigEngine orchestrator with 4 async methods: audit, sync, snapshot, status
- ✅ Loader: YAML -> validated PortfolioConfig with human-readable error messages
- ✅ Differ: template vs remote comparison with name+position matching, format-union iteration, A/B test detection
- ✅ Applier: dry-run default, pre-write snapshots, ascending-position CREATE ordering, post-write verification, sync log recording
- ✅ Group write operations: create_group, update_group, delete_group via LevelPlay v4 API
- ✅ CLI: 4 typer commands (audit, sync-tiers, snapshot, status) with rich terminal output, JSON output mode, proper exit codes
- ✅ LocalFileStorageAdapter: JSONL sync logs, per-snapshot JSON files, .admedi/ directory auto-creation
- ✅ Real Shelf Sort YAML template (6 apps, 4 tiers, per-tier ad_format scoping)

**Lessons Learned**:
- LevelPlay's POST/PUT field name asymmetry is a silent failure source -- must verify field names per endpoint, not assume consistency
- Single-group position skip rule is essential for banner format to avoid false-positive diffs
- Check existing model files before creating duplicates when a plan specifies co-location -- avoids import ambiguity

**Next**: Run integration tests against live LevelPlay API with real credentials, then proceed to Portfolio Dogfood (Shelf Sort validation for 2+ weeks)
