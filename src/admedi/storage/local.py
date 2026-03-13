"""Local file storage adapter for sync logs and snapshots.

Persists sync operation logs and configuration snapshots to the
``.admedi/`` directory on the local filesystem. Sync logs are stored
as append-only JSONL (one JSON object per line) in ``.admedi/logs/sync.jsonl``.
Snapshots are stored as individual JSON files in ``.admedi/snapshots/``.

All methods are async (matching the ``StorageAdapter`` ABC) but use
synchronous file I/O wrapped in ``asyncio.to_thread()`` for compatibility
with the async engine pipeline.

Example::

    from admedi.storage.local import LocalFileStorageAdapter

    storage = LocalFileStorageAdapter(base_dir=".admedi")
    await storage.save_sync_log(sync_log)
    history = await storage.list_sync_history("abc123")
    await storage.save_snapshot(config_snapshot)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from admedi.adapters.storage import StorageAdapter
from admedi.models.config_snapshot import ConfigSnapshot
from admedi.models.sync_log import SyncLog


class LocalFileStorageAdapter(StorageAdapter):
    """File-based storage adapter using ``.admedi/`` directory.

    Creates the directory tree on initialization::

        .admedi/
        ├── logs/
        │   └── sync.jsonl      # Append-only sync log
        └── snapshots/
            └── {app_key}_{timestamp}.json

    Attributes:
        base_dir: Root directory for all storage (default: ``.admedi``).
        logs_dir: Path to the logs subdirectory.
        snapshots_dir: Path to the snapshots subdirectory.
        sync_log_path: Path to the JSONL sync log file.

    Example::

        storage = LocalFileStorageAdapter(base_dir="/tmp/test/.admedi")
        await storage.save_sync_log(log)
        entries = await storage.list_sync_history("my_app_key")
    """

    def __init__(self, base_dir: str | Path = ".admedi") -> None:
        """Initialize storage adapter and create directory tree.

        Args:
            base_dir: Root directory for storage. Created if it does not
                exist, along with ``logs/`` and ``snapshots/`` subdirectories.
        """
        self.base_dir: Path = Path(base_dir)
        self.logs_dir: Path = self.base_dir / "logs"
        self.snapshots_dir: Path = self.base_dir / "snapshots"
        self.sync_log_path: Path = self.logs_dir / "sync.jsonl"

        # Create directory tree
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    async def save_sync_log(self, log: SyncLog) -> None:
        """Append a sync log entry as one JSON line to ``sync.jsonl``.

        The file is created on first write. Each subsequent call appends
        a new line, preserving all previous entries (append-only semantics).

        Args:
            log: The sync log entry to persist.
        """
        await asyncio.to_thread(self._write_sync_log, log)

    async def list_sync_history(self, app_key: str) -> list[SyncLog]:
        """Read sync history for a specific app, newest-first.

        Reads all lines from ``sync.jsonl``, filters by ``app_key``,
        and returns them sorted by timestamp descending (newest first).

        Returns an empty list when the log file does not exist or is empty.

        Args:
            app_key: Platform-specific app identifier to filter by.

        Returns:
            List of ``SyncLog`` entries matching ``app_key``, ordered
            by timestamp descending.
        """
        return await asyncio.to_thread(self._read_sync_history, app_key)

    async def save_snapshot(self, snapshot: ConfigSnapshot) -> None:
        """Save a configuration snapshot as a JSON file.

        The filename uses the pattern ``{app_key}_{timestamp}.json``
        where the timestamp is ISO 8601 with colons replaced by dashes
        for filesystem safety.

        Args:
            snapshot: The configuration snapshot to persist.
        """
        await asyncio.to_thread(self._write_snapshot, snapshot)

    # ------------------------------------------------------------------
    # Private synchronous helpers (run inside asyncio.to_thread)
    # ------------------------------------------------------------------

    def _write_sync_log(self, log: SyncLog) -> None:
        """Append one JSON line to the sync log file."""
        with self.sync_log_path.open("a", encoding="utf-8") as f:
            f.write(log.model_dump_json() + "\n")

    def _read_sync_history(self, app_key: str) -> list[SyncLog]:
        """Read and filter sync history from the JSONL file."""
        if not self.sync_log_path.exists():
            return []

        entries: list[SyncLog] = []
        text = self.sync_log_path.read_text(encoding="utf-8")
        if not text.strip():
            return []

        for line in text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            log = SyncLog.model_validate_json(line)
            if log.app_key == app_key:
                entries.append(log)

        # Sort newest-first by timestamp
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries

    def _write_snapshot(self, snapshot: ConfigSnapshot) -> None:
        """Write a snapshot to an individual JSON file."""
        # Build filesystem-safe timestamp: replace colons with dashes
        ts_str = snapshot.timestamp.isoformat().replace(":", "-")
        filename = f"{snapshot.app_key}_{ts_str}.json"
        filepath = self.snapshots_dir / filename

        filepath.write_text(
            json.dumps(
                snapshot.model_dump(mode="json"),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
