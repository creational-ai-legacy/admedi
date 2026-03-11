"""ConfigSnapshot model for capturing raw mediation configurations.

Stores a point-in-time snapshot of the raw API configuration for an
application, enabling diff and rollback operations.

Example:
    >>> from datetime import datetime, timezone
    >>> from admedi.models.config_snapshot import ConfigSnapshot
    >>> snapshot = ConfigSnapshot(
    ...     app_key="abc123",
    ...     timestamp=datetime.now(tz=timezone.utc),
    ...     raw_config={"groups": [{"groupName": "Default"}]},
    ... )
    >>> snapshot.raw_config["groups"][0]["groupName"]
    'Default'
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ConfigSnapshot(BaseModel):
    """Point-in-time snapshot of raw mediation configuration.

    Attributes:
        app_key: Application identifier this snapshot belongs to.
        timestamp: When the snapshot was taken.
        raw_config: The raw API configuration as a nested dict.
    """

    model_config = ConfigDict(populate_by_name=True)

    app_key: str
    timestamp: datetime
    raw_config: dict[str, Any]
