"""SyncLog model for tracking mediation sync operations.

Records each sync action (diff, apply, rollback) with its timestamp,
target app, diff summary, and result.

Example:
    >>> from datetime import datetime, timezone
    >>> from admedi.models.sync_log import SyncLog
    >>> log = SyncLog(
    ...     timestamp=datetime.now(tz=timezone.utc),
    ...     app_key="abc123",
    ...     action="apply",
    ...     diff_summary={"groups_added": 2, "groups_removed": 0},
    ...     result="success",
    ... )
    >>> log.action
    'apply'
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SyncLog(BaseModel):
    """Record of a mediation sync operation.

    Attributes:
        timestamp: When the sync operation occurred.
        app_key: Target application identifier.
        user: Who initiated the sync, if known.
        action: Type of sync operation (e.g., "apply", "diff", "rollback").
        diff_summary: Summary of changes as a nested dict.
        result: Outcome of the operation (e.g., "success", "partial_failure").
        error_detail: Error description if the operation failed.
    """

    model_config = ConfigDict(populate_by_name=True)

    timestamp: datetime
    app_key: str
    user: str | None = Field(default=None)
    action: str
    diff_summary: dict[str, Any]
    result: str
    error_detail: str | None = Field(default=None)
