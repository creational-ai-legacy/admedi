"""Storage adapter abstract interface.

Defines the ``StorageAdapter`` abstract base class that all persistence
backends (local file, PostgreSQL via RDS/Supabase) must implement.

Example::

    from admedi.adapters.storage import StorageAdapter

    class LocalFileStorageAdapter(StorageAdapter):
        async def save_config(self, config: TierTemplate) -> None:
            ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from admedi.models.config_snapshot import ConfigSnapshot
from admedi.models.sync_log import SyncLog
from admedi.models.tier_template import TierTemplate


class StorageAdapter(ABC):
    """Abstract base class for persistence backends.

    Concrete adapters (e.g., ``LocalFileStorageAdapter``,
    ``PostgresStorageAdapter``) implement all 5 async methods to
    persist tier configs, sync history, and configuration snapshots.

    Example::

        storage = LocalFileStorageAdapter(data_dir="/path/to/data")
        await storage.save_config(tier_template)
        loaded = await storage.load_config("shelf-sort-interstitial")
    """

    @abstractmethod
    async def save_config(self, config: TierTemplate) -> None:
        """Persist a tier template configuration.

        Args:
            config: The tier template to save.

        Raises:
            AdmediError: If persistence fails.
        """
        ...

    @abstractmethod
    async def load_config(self, config_id: str) -> TierTemplate | None:
        """Load a tier template by identifier.

        Args:
            config_id: Unique identifier for the config (e.g., template name
                or slug).

        Returns:
            The loaded ``TierTemplate``, or ``None`` if not found.

        Raises:
            AdmediError: If loading fails for reasons other than not found.
        """
        ...

    @abstractmethod
    async def save_sync_log(self, log: SyncLog) -> None:
        """Persist a sync operation log entry.

        Args:
            log: The sync log entry to save.

        Raises:
            AdmediError: If persistence fails.
        """
        ...

    @abstractmethod
    async def list_sync_history(self, app_key: str) -> list[SyncLog]:
        """List sync history for an app.

        Args:
            app_key: Platform-specific app identifier to filter by.

        Returns:
            List of ``SyncLog`` entries, ordered by timestamp (newest first).

        Raises:
            AdmediError: If the query fails.
        """
        ...

    @abstractmethod
    async def save_snapshot(self, snapshot: ConfigSnapshot) -> None:
        """Persist a configuration snapshot.

        Snapshots capture the raw platform configuration at a point in time,
        enabling before/after comparisons during sync operations.

        Args:
            snapshot: The configuration snapshot to save.

        Raises:
            AdmediError: If persistence fails.
        """
        ...
