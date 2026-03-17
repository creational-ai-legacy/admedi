"""Diff models for config-as-code comparison.

These models represent the output of a diff operation comparing a desired
tier template against live LevelPlay mediation groups. The Differ produces
a ``DiffReport`` containing per-app ``AppDiffReport`` entries, each with
per-group ``GroupDiff`` records describing the required action and field-level
changes.

``DiffAction`` is co-located here (not in ``enums.py``) to avoid circular
imports since it is tightly coupled with its diff model consumers.

Examples:
    >>> from admedi.models.diff import DiffAction, FieldChange, GroupDiff
    >>> from admedi.models.enums import AdFormat
    >>> action = DiffAction.CREATE
    >>> action.value
    'create'
    >>> change = FieldChange(
    ...     field="countries",
    ...     old_value=["US"],
    ...     new_value=["US", "GB"],
    ...     description="Added GB to country list",
    ... )
    >>> change.field
    'countries'
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, computed_field

from admedi.models.enums import AdFormat
from admedi.models.group import Group


class DiffAction(str, Enum):
    """Action required to reconcile a group with the desired state.

    Uses the ``(str, Enum)`` mixin pattern so values serialize directly
    to their string representations.

    Members:
        CREATE: Group exists in template but not in remote -- needs creation.
        UPDATE: Group exists in both but fields differ -- needs update.
        DELETE: Group exists in remote but not in template -- will be deleted
            during sync.
        UNCHANGED: Group matches template -- no action needed.
        EXTRA: Group exists in remote but not in template -- flagged for
            awareness (no automatic action taken).
    """

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    UNCHANGED = "unchanged"
    EXTRA = "extra"


class FieldChange(BaseModel):
    """A single field-level change within a group.

    Captures what changed, the old and new values, and a human-readable
    description for CLI display.

    Attributes:
        field: The name of the changed field (e.g., ``"countries"``).
        old_value: The current remote value.
        new_value: The desired template value.
        description: Human-readable explanation of the change.
    """

    model_config = ConfigDict(populate_by_name=True)

    field: str
    old_value: Any
    new_value: Any
    description: str


class GroupDiff(BaseModel):
    """Diff result for a single mediation group.

    Describes the action needed and the field-level changes for one group.
    For ``CREATE`` actions, ``desired_group`` contains the target state with
    ``group_id=None``. For ``UPDATE``, it contains the full target state.
    For ``UNCHANGED``, ``EXTRA``, and ``DELETE``, ``desired_group`` is ``None``.

    Attributes:
        action: The diff action (CREATE, UPDATE, DELETE, UNCHANGED, EXTRA).
        group_name: Display name of the group.
        group_id: Remote group ID (``None`` for CREATE actions).
        ad_format: The ad format this group handles.
        changes: List of field-level changes (empty for UNCHANGED/EXTRA).
        desired_group: Target state for CREATE/UPDATE actions; ``None`` otherwise.
    """

    model_config = ConfigDict(populate_by_name=True)

    action: DiffAction
    group_name: str
    group_id: int | None
    ad_format: AdFormat
    changes: list[FieldChange]
    desired_group: Group | None


class AppDiffReport(BaseModel):
    """Diff report for a single app.

    Contains all group diffs for one app, plus A/B test awareness.

    Attributes:
        app_key: The app's unique key.
        app_name: Display name of the app.
        group_diffs: List of per-group diff results.
        has_ab_test: Whether any group in this app has an active A/B test.
        ab_test_warning: Human-readable warning if A/B test is active.
    """

    model_config = ConfigDict(populate_by_name=True)

    app_key: str
    app_name: str
    group_diffs: list[GroupDiff]
    has_ab_test: bool
    ab_test_warning: str | None


class DiffReport(BaseModel):
    """Aggregate diff report across all apps in a portfolio.

    Contains per-app reports and computed totals for each action type.

    Attributes:
        app_reports: List of per-app diff reports.
        total_creates: Total number of CREATE actions across all apps.
        total_updates: Total number of UPDATE actions across all apps.
        total_deletes: Total number of DELETE actions across all apps.
        total_unchanged: Total number of UNCHANGED actions across all apps.
        total_extra: Total number of EXTRA actions across all apps.
    """

    model_config = ConfigDict(populate_by_name=True)

    app_reports: list[AppDiffReport]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_creates(self) -> int:
        """Count of CREATE actions across all app reports."""
        return sum(
            1
            for report in self.app_reports
            for diff in report.group_diffs
            if diff.action == DiffAction.CREATE
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_updates(self) -> int:
        """Count of UPDATE actions across all app reports."""
        return sum(
            1
            for report in self.app_reports
            for diff in report.group_diffs
            if diff.action == DiffAction.UPDATE
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_deletes(self) -> int:
        """Count of DELETE actions across all app reports."""
        return sum(
            1
            for report in self.app_reports
            for diff in report.group_diffs
            if diff.action == DiffAction.DELETE
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_unchanged(self) -> int:
        """Count of UNCHANGED actions across all app reports."""
        return sum(
            1
            for report in self.app_reports
            for diff in report.group_diffs
            if diff.action == DiffAction.UNCHANGED
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_extra(self) -> int:
        """Count of EXTRA actions across all app reports."""
        return sum(
            1
            for report in self.app_reports
            for diff in report.group_diffs
            if diff.action == DiffAction.EXTRA
        )
