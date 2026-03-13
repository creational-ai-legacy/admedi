"""Apply and status models for config-as-code sync operations.

These models represent the output of applying a diff (``ApplyResult``,
``AppApplyResult``), the current portfolio state (``PortfolioStatus``,
``AppStatus``), and the apply operation status enum (``ApplyStatus``).

``ApplyStatus`` is co-located here (not in ``enums.py``) because it is
tightly coupled with its consumer models, following the same pattern as
``DiffAction`` in ``diff.py``.

Examples:
    >>> from admedi.models.apply_result import ApplyStatus, AppApplyResult, ApplyResult
    >>> result = AppApplyResult(
    ...     app_key="abc123",
    ...     status=ApplyStatus.SUCCESS,
    ...     groups_created=2,
    ...     groups_updated=1,
    ...     error=None,
    ... )
    >>> result.status.value
    'success'
    >>> result.warnings
    []
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, computed_field

from admedi.models.enums import Mediator, Platform


class ApplyStatus(str, Enum):
    """Status of an apply operation for a single app.

    Uses the ``(str, Enum)`` mixin pattern so values serialize directly
    to their string representations.

    Members:
        SUCCESS: All group operations completed successfully.
        SKIPPED: App was skipped (e.g., active A/B test detected).
        FAILED: One or more group operations failed.
        DRY_RUN: Dry-run mode -- no actual API calls were made.
    """

    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"
    DRY_RUN = "dry_run"


class AppApplyResult(BaseModel):
    """Result of applying a diff to a single app.

    Captures the outcome of group create/update operations for one app,
    including any warnings from post-write verification.

    Attributes:
        app_key: The application identifier.
        status: Outcome status of the apply operation.
        groups_created: Number of groups successfully created.
        groups_updated: Number of groups successfully updated.
        error: Error description if the operation failed; ``None`` on success.
        warnings: Post-write verification warnings (e.g., mismatches between
            expected and actual state after a write). Defaults to empty list.
    """

    model_config = ConfigDict(populate_by_name=True)

    app_key: str
    status: ApplyStatus
    groups_created: int
    groups_updated: int
    error: str | None
    warnings: list[str] = []


class ApplyResult(BaseModel):
    """Aggregate result of applying a diff across all apps.

    Contains per-app results and computed totals for each status type.

    Attributes:
        app_results: List of per-app apply results.
        was_dry_run: Whether the operation was a dry run.
        total_success: Count of apps with SUCCESS status (computed).
        total_skipped: Count of apps with SKIPPED status (computed).
        total_failed: Count of apps with FAILED status (computed).
    """

    model_config = ConfigDict(populate_by_name=True)

    app_results: list[AppApplyResult]
    was_dry_run: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_success(self) -> int:
        """Count of apps with SUCCESS status."""
        return sum(1 for r in self.app_results if r.status == ApplyStatus.SUCCESS)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_skipped(self) -> int:
        """Count of apps with SKIPPED status."""
        return sum(1 for r in self.app_results if r.status == ApplyStatus.SKIPPED)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_failed(self) -> int:
        """Count of apps with FAILED status."""
        return sum(1 for r in self.app_results if r.status == ApplyStatus.FAILED)


class AppStatus(BaseModel):
    """Current status of a single app in the portfolio.

    Used by the ``status`` CLI command to display portfolio overview.

    Attributes:
        app_key: The application identifier.
        app_name: Human-readable display name.
        platform: Target mobile platform.
        group_count: Number of mediation groups configured for this app.
        last_sync: Timestamp of the last sync operation (timezone-aware UTC),
            or ``None`` if never synced.
    """

    model_config = ConfigDict(populate_by_name=True)

    app_key: str
    app_name: str
    platform: Platform
    group_count: int
    last_sync: datetime | None


class PortfolioStatus(BaseModel):
    """Portfolio-level status aggregating all app statuses.

    Used by the ``status`` CLI command to display the full portfolio
    overview for a given mediator.

    Attributes:
        mediator: Which mediation platform this portfolio targets.
        apps: List of per-app status entries.
    """

    model_config = ConfigDict(populate_by_name=True)

    mediator: Mediator
    apps: list[AppStatus]
