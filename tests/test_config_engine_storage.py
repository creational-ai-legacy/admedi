"""Tests for LocalFileStorageAdapter.

Covers:
- Directory creation on initialization
- Sync log append-only semantics (multiple writes = multiple lines)
- Sync history filtering by app_key
- Sync history ordering (newest-first)
- Missing/empty log file handling (returns empty list)
- Snapshot file creation with filesystem-safe timestamp
- Snapshot filename format ({app_key}_{timestamp}.json)
- All methods work as async (can be awaited)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from admedi.models.apply_result import ApplyStatus
from admedi.models.config_snapshot import ConfigSnapshot
from admedi.models.sync_log import SyncLog
from admedi.storage.local import LocalFileStorageAdapter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def storage(tmp_path: Path) -> LocalFileStorageAdapter:
    """Create a LocalFileStorageAdapter using tmp_path for isolation."""
    base_dir = tmp_path / ".admedi"
    return LocalFileStorageAdapter(base_dir=base_dir)


def _make_sync_log(
    app_key: str = "test_app",
    timestamp: datetime | None = None,
    action: str = "sync",
    groups_created: int = 1,
    groups_updated: int = 0,
    status: ApplyStatus = ApplyStatus.SUCCESS,
    error: str | None = None,
) -> SyncLog:
    """Helper to build a SyncLog with sensible defaults."""
    return SyncLog(
        app_key=app_key,
        timestamp=timestamp or datetime.now(tz=timezone.utc),
        action=action,
        groups_created=groups_created,
        groups_updated=groups_updated,
        status=status,
        error=error,
    )


def _make_snapshot(
    app_key: str = "test_app",
    timestamp: datetime | None = None,
    raw_config: dict | None = None,
) -> ConfigSnapshot:
    """Helper to build a ConfigSnapshot with sensible defaults."""
    return ConfigSnapshot(
        app_key=app_key,
        timestamp=timestamp or datetime.now(tz=timezone.utc),
        raw_config=raw_config or {"groups": [{"groupName": "Default"}]},
    )


# ---------------------------------------------------------------------------
# Directory Creation
# ---------------------------------------------------------------------------


class TestDirectoryCreation:
    """Tests for directory tree creation on initialization."""

    def test_creates_base_dir(self, tmp_path: Path) -> None:
        """Base directory is created when it does not exist."""
        base_dir = tmp_path / ".admedi"
        assert not base_dir.exists()

        LocalFileStorageAdapter(base_dir=base_dir)

        assert base_dir.is_dir()

    def test_creates_logs_dir(self, tmp_path: Path) -> None:
        """Logs subdirectory is created on initialization."""
        base_dir = tmp_path / ".admedi"
        adapter = LocalFileStorageAdapter(base_dir=base_dir)

        assert adapter.logs_dir.is_dir()
        assert adapter.logs_dir == base_dir / "logs"

    def test_creates_snapshots_dir(self, tmp_path: Path) -> None:
        """Snapshots subdirectory is created on initialization."""
        base_dir = tmp_path / ".admedi"
        adapter = LocalFileStorageAdapter(base_dir=base_dir)

        assert adapter.snapshots_dir.is_dir()
        assert adapter.snapshots_dir == base_dir / "snapshots"

    def test_idempotent_directory_creation(self, tmp_path: Path) -> None:
        """Creating adapter twice with same base_dir does not raise."""
        base_dir = tmp_path / ".admedi"
        LocalFileStorageAdapter(base_dir=base_dir)
        # Second creation should not fail (exist_ok=True)
        adapter2 = LocalFileStorageAdapter(base_dir=base_dir)
        assert adapter2.logs_dir.is_dir()

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        """Accepts a string path for base_dir."""
        base_dir = str(tmp_path / ".admedi")
        adapter = LocalFileStorageAdapter(base_dir=base_dir)
        assert adapter.base_dir == Path(base_dir)
        assert adapter.logs_dir.is_dir()


# ---------------------------------------------------------------------------
# save_sync_log
# ---------------------------------------------------------------------------


class TestSaveSyncLog:
    """Tests for save_sync_log() append-only semantics."""

    @pytest.mark.asyncio
    async def test_creates_file_on_first_write(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """sync.jsonl is created on first save_sync_log() call."""
        assert not storage.sync_log_path.exists()

        log = _make_sync_log()
        await storage.save_sync_log(log)

        assert storage.sync_log_path.exists()

    @pytest.mark.asyncio
    async def test_append_only_semantics(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """Multiple writes produce multiple lines (append, not overwrite)."""
        log1 = _make_sync_log(app_key="app1", groups_created=1)
        log2 = _make_sync_log(app_key="app2", groups_created=2)
        log3 = _make_sync_log(app_key="app3", groups_created=3)

        await storage.save_sync_log(log1)
        await storage.save_sync_log(log2)
        await storage.save_sync_log(log3)

        lines = storage.sync_log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    @pytest.mark.asyncio
    async def test_each_line_is_valid_json(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """Each line in sync.jsonl is valid JSON deserializable to SyncLog."""
        log = _make_sync_log(app_key="valid_json_test")
        await storage.save_sync_log(log)

        text = storage.sync_log_path.read_text(encoding="utf-8").strip()
        parsed = SyncLog.model_validate_json(text)
        assert parsed.app_key == "valid_json_test"

    @pytest.mark.asyncio
    async def test_preserves_all_fields(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """All SyncLog fields are preserved in serialization round-trip."""
        ts = datetime(2026, 3, 12, 10, 30, 0, tzinfo=timezone.utc)
        log = _make_sync_log(
            app_key="full_fields",
            timestamp=ts,
            action="sync",
            groups_created=5,
            groups_updated=3,
            status=ApplyStatus.FAILED,
            error="Something went wrong",
        )
        await storage.save_sync_log(log)

        text = storage.sync_log_path.read_text(encoding="utf-8").strip()
        parsed = SyncLog.model_validate_json(text)
        assert parsed.app_key == "full_fields"
        assert parsed.groups_created == 5
        assert parsed.groups_updated == 3
        assert parsed.status == ApplyStatus.FAILED
        assert parsed.error == "Something went wrong"

    @pytest.mark.asyncio
    async def test_is_async(self, storage: LocalFileStorageAdapter) -> None:
        """save_sync_log() is a coroutine that can be awaited."""
        log = _make_sync_log()
        coro = storage.save_sync_log(log)
        assert asyncio.iscoroutine(coro)
        await coro


# ---------------------------------------------------------------------------
# list_sync_history
# ---------------------------------------------------------------------------


class TestListSyncHistory:
    """Tests for list_sync_history() filtering and ordering."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_file_missing(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """Returns empty list when sync.jsonl does not exist."""
        assert not storage.sync_log_path.exists()
        result = await storage.list_sync_history("any_key")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_file_empty(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """Returns empty list when sync.jsonl exists but is empty."""
        storage.sync_log_path.write_text("", encoding="utf-8")
        result = await storage.list_sync_history("any_key")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_by_app_key(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """Only entries matching app_key are returned."""
        await storage.save_sync_log(_make_sync_log(app_key="app_a"))
        await storage.save_sync_log(_make_sync_log(app_key="app_b"))
        await storage.save_sync_log(_make_sync_log(app_key="app_a"))
        await storage.save_sync_log(_make_sync_log(app_key="app_c"))

        result = await storage.list_sync_history("app_a")
        assert len(result) == 2
        assert all(entry.app_key == "app_a" for entry in result)

    @pytest.mark.asyncio
    async def test_returns_newest_first(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """Entries are sorted by timestamp descending (newest first)."""
        ts1 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        ts3 = datetime(2026, 3, 10, 8, 0, 0, tzinfo=timezone.utc)

        # Write in non-chronological order
        await storage.save_sync_log(_make_sync_log(app_key="app_x", timestamp=ts1))
        await storage.save_sync_log(_make_sync_log(app_key="app_x", timestamp=ts3))
        await storage.save_sync_log(_make_sync_log(app_key="app_x", timestamp=ts2))

        result = await storage.list_sync_history("app_x")
        assert len(result) == 3
        assert result[0].timestamp == ts2  # newest
        assert result[1].timestamp == ts3
        assert result[2].timestamp == ts1  # oldest

    @pytest.mark.asyncio
    async def test_no_matching_app_key(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """Returns empty list when no entries match the requested app_key."""
        await storage.save_sync_log(_make_sync_log(app_key="other_app"))
        result = await storage.list_sync_history("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_is_async(self, storage: LocalFileStorageAdapter) -> None:
        """list_sync_history() is a coroutine that can be awaited."""
        coro = storage.list_sync_history("test")
        assert asyncio.iscoroutine(coro)
        await coro


# ---------------------------------------------------------------------------
# save_snapshot
# ---------------------------------------------------------------------------


class TestSaveSnapshot:
    """Tests for save_snapshot() file creation and naming."""

    @pytest.mark.asyncio
    async def test_creates_snapshot_file(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """A JSON file is created in the snapshots directory."""
        snapshot = _make_snapshot(app_key="snap_app")
        await storage.save_snapshot(snapshot)

        files = list(storage.snapshots_dir.glob("*.json"))
        assert len(files) == 1

    @pytest.mark.asyncio
    async def test_filename_format(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """Filename follows {app_key}_{timestamp}.json with safe timestamp."""
        ts = datetime(2026, 3, 12, 14, 30, 45, tzinfo=timezone.utc)
        snapshot = _make_snapshot(app_key="myapp", timestamp=ts)
        await storage.save_snapshot(snapshot)

        files = list(storage.snapshots_dir.glob("*.json"))
        assert len(files) == 1
        filename = files[0].name
        # Colons replaced with dashes in timestamp
        assert filename.startswith("myapp_")
        assert filename.endswith(".json")
        # Should not contain colons (filesystem safety)
        assert ":" not in filename

    @pytest.mark.asyncio
    async def test_timestamp_colons_replaced(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """ISO 8601 colons are replaced with dashes for filesystem safety."""
        ts = datetime(2026, 3, 12, 14, 30, 45, tzinfo=timezone.utc)
        snapshot = _make_snapshot(app_key="colon_test", timestamp=ts)
        await storage.save_snapshot(snapshot)

        files = list(storage.snapshots_dir.glob("*.json"))
        filename = files[0].name
        # The timestamp portion should have dashes instead of colons
        # 2026-03-12T14:30:45+00:00 -> 2026-03-12T14-30-45+00-00
        assert "14-30-45" in filename

    @pytest.mark.asyncio
    async def test_snapshot_content_is_valid_json(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """Snapshot file contains valid JSON with all fields."""
        ts = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)
        raw = {"groups": [{"groupName": "Tier 1", "countries": ["US"]}]}
        snapshot = _make_snapshot(app_key="json_test", timestamp=ts, raw_config=raw)
        await storage.save_snapshot(snapshot)

        files = list(storage.snapshots_dir.glob("*.json"))
        content = json.loads(files[0].read_text(encoding="utf-8"))
        assert content["app_key"] == "json_test"
        assert content["raw_config"]["groups"][0]["groupName"] == "Tier 1"

    @pytest.mark.asyncio
    async def test_multiple_snapshots_create_separate_files(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """Each snapshot creates a distinct file."""
        ts1 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 1, 2, 0, 0, 0, tzinfo=timezone.utc)

        await storage.save_snapshot(_make_snapshot(app_key="app1", timestamp=ts1))
        await storage.save_snapshot(_make_snapshot(app_key="app1", timestamp=ts2))

        files = list(storage.snapshots_dir.glob("*.json"))
        assert len(files) == 2

    @pytest.mark.asyncio
    async def test_is_async(self, storage: LocalFileStorageAdapter) -> None:
        """save_snapshot() is a coroutine that can be awaited."""
        snapshot = _make_snapshot()
        coro = storage.save_snapshot(snapshot)
        assert asyncio.iscoroutine(coro)
        await coro


# ---------------------------------------------------------------------------
# Integration: round-trip save + list
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Tests verifying save -> list round-trip through the adapter."""

    @pytest.mark.asyncio
    async def test_save_then_list_sync_log(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """Saved sync log is retrievable via list_sync_history."""
        log = _make_sync_log(app_key="roundtrip", groups_created=7)
        await storage.save_sync_log(log)

        history = await storage.list_sync_history("roundtrip")
        assert len(history) == 1
        assert history[0].app_key == "roundtrip"
        assert history[0].groups_created == 7

    @pytest.mark.asyncio
    async def test_multiple_apps_interleaved(
        self, storage: LocalFileStorageAdapter
    ) -> None:
        """Interleaved writes for multiple apps filter correctly."""
        ts_base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        for i in range(5):
            app_key = "app_a" if i % 2 == 0 else "app_b"
            ts = ts_base + timedelta(hours=i)
            await storage.save_sync_log(
                _make_sync_log(app_key=app_key, timestamp=ts, groups_created=i)
            )

        a_history = await storage.list_sync_history("app_a")
        b_history = await storage.list_sync_history("app_b")

        assert len(a_history) == 3  # indices 0, 2, 4
        assert len(b_history) == 2  # indices 1, 3

        # Verify newest-first ordering
        assert a_history[0].groups_created == 4  # i=4
        assert a_history[1].groups_created == 2  # i=2
        assert a_history[2].groups_created == 0  # i=0


# ---------------------------------------------------------------------------
# Import from admedi.storage
# ---------------------------------------------------------------------------


class TestImport:
    """Tests that LocalFileStorageAdapter is importable from admedi.storage."""

    def test_import_from_storage_package(self) -> None:
        """LocalFileStorageAdapter is exported from admedi.storage."""
        from admedi.storage import LocalFileStorageAdapter as Imported

        assert Imported is LocalFileStorageAdapter

    def test_is_subclass_of_storage_adapter(self) -> None:
        """LocalFileStorageAdapter is a subclass of StorageAdapter."""
        from admedi.adapters.storage import StorageAdapter

        assert issubclass(LocalFileStorageAdapter, StorageAdapter)
