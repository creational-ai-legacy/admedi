"""SyncLog model for tracking mediation sync operations.

Records each sync action per app per sync operation with its timestamp,
target app, group change counts, status, and optional error detail.
One entry is created per app per sync.

Serialized to JSONL via ``model_dump_json()`` by
``LocalFileStorageAdapter.save_sync_log()``.

Example:
    >>> from datetime import datetime, timezone
    >>> from admedi.models.sync_log import SyncLog
    >>> from admedi.models.apply_result import ApplyStatus
    >>> log = SyncLog(
    ...     app_key="abc123",
    ...     timestamp=datetime.now(tz=timezone.utc),
    ...     action="sync",
    ...     groups_created=2,
    ...     groups_updated=1,
    ...     status=ApplyStatus.SUCCESS,
    ... )
    >>> log.action
    'sync'
    >>> log.error is None
    True
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from admedi.models.apply_result import ApplyStatus


class SyncLog(BaseModel):
    """Record of a mediation sync operation for a single app.

    Attributes:
        app_key: Target application identifier.
        timestamp: When the sync operation occurred (timezone-aware UTC).
        action: Type of sync operation (e.g., ``"sync"``).
        groups_created: Number of groups created during this sync.
        groups_updated: Number of groups updated during this sync.
        status: Outcome status of the sync operation.
        error: Error description if the operation failed; ``None`` on success.
    """

    model_config = ConfigDict(populate_by_name=True)

    app_key: str
    timestamp: datetime
    action: str
    groups_created: int
    groups_updated: int
    status: ApplyStatus
    error: str | None = None
