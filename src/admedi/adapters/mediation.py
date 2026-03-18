"""Mediation adapter abstract interface.

Defines the ``MediationAdapter`` abstract base class that all mediation
platform adapters (LevelPlay, MAX, AdMob) must implement, plus the
``AdapterCapability`` enum used for capability negotiation.

Example::

    from admedi.adapters.mediation import MediationAdapter, AdapterCapability

    class LevelPlayAdapter(MediationAdapter):
        @property
        def capabilities(self) -> set[AdapterCapability]:
            return {
                AdapterCapability.AUTHENTICATE,
                AdapterCapability.LIST_APPS,
                AdapterCapability.READ_GROUPS,
                AdapterCapability.WRITE_GROUPS,
                ...
            }

        async def authenticate(self) -> None:
            ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from admedi.exceptions import AdapterNotSupportedError
from admedi.models.app import App
from admedi.models.group import Group
from admedi.models.instance import Instance
from admedi.models.placement import Placement


class AdapterCapability(str, Enum):
    """Capabilities that a mediation adapter may support.

    Used for runtime capability negotiation via
    ``MediationAdapter.ensure_capability()``. Not all mediators support
    every operation -- for example, a read-only adapter would omit
    ``WRITE_GROUPS`` and ``WRITE_INSTANCES``.

    Members:
        READ_GROUPS: Can read mediation groups from the platform.
        WRITE_GROUPS: Can create, update, and delete groups.
        READ_INSTANCES: Can read ad network instances.
        WRITE_INSTANCES: Can create, update, and delete instances.
        READ_PLACEMENTS: Can read placement configurations.
        READ_REPORTING: Can fetch performance reporting data.
        AUTHENTICATE: Can perform OAuth/token-based authentication.
        LIST_APPS: Can list apps registered on the platform.
    """

    READ_GROUPS = "read_groups"
    WRITE_GROUPS = "write_groups"
    READ_INSTANCES = "read_instances"
    WRITE_INSTANCES = "write_instances"
    READ_PLACEMENTS = "read_placements"
    READ_REPORTING = "read_reporting"
    AUTHENTICATE = "authenticate"
    LIST_APPS = "list_apps"


class MediationAdapter(ABC):
    """Abstract base class for mediation platform adapters.

    Concrete adapters (e.g., ``LevelPlayAdapter``) implement all 12 async
    methods and declare their supported capabilities via the ``capabilities``
    property. Callers use ``ensure_capability()`` to fail fast when an
    operation is not supported by the active adapter.

    Example::

        adapter = LevelPlayAdapter(credential)
        adapter.ensure_capability(AdapterCapability.READ_GROUPS)
        groups = await adapter.get_groups(app_key="abc123")
    """

    @property
    @abstractmethod
    def capabilities(self) -> set[AdapterCapability]:
        """Return the set of capabilities this adapter supports."""
        ...

    def ensure_capability(self, cap: AdapterCapability) -> None:
        """Raise if this adapter does not support the given capability.

        Args:
            cap: The capability to check.

        Raises:
            AdapterNotSupportedError: If ``cap`` is not in
                ``self.capabilities``.

        Example::

            adapter.ensure_capability(AdapterCapability.WRITE_GROUPS)
            # Raises AdapterNotSupportedError if adapter is read-only
        """
        if cap not in self.capabilities:
            raise AdapterNotSupportedError(
                f"Adapter does not support capability: {cap.value}"
            )

    # -- Authentication ------------------------------------------------------

    @abstractmethod
    async def authenticate(self) -> None:
        """Authenticate with the mediation platform.

        Performs OAuth token exchange or credential validation. Must be
        called before making API requests.

        Raises:
            AuthError: If authentication fails.
        """
        ...

    # -- Apps ----------------------------------------------------------------

    @abstractmethod
    async def list_apps(self) -> list[App]:
        """List all apps registered on the mediation platform.

        Returns:
            List of ``App`` models with platform-specific metadata.

        Raises:
            AuthError: If not authenticated.
            ApiError: If the API request fails.
        """
        ...

    # -- Groups --------------------------------------------------------------

    @abstractmethod
    async def get_groups(self, app_key: str) -> list[Group]:
        """Get all mediation groups for an app.

        Args:
            app_key: Platform-specific app identifier.

        Returns:
            List of ``Group`` models with waterfall configurations.

        Raises:
            ApiError: If the API request fails.
        """
        ...

    @abstractmethod
    async def create_group(self, app_key: str, group: Group) -> Group:
        """Create a new mediation group for an app.

        Args:
            app_key: Platform-specific app identifier.
            group: Group configuration to create.

        Returns:
            The created ``Group`` with server-assigned ``group_id``.

        Raises:
            ApiError: If the API request fails.
        """
        ...

    @abstractmethod
    async def update_group(
        self,
        app_key: str,
        group_id: int,
        group: Group,
        *,
        waterfall_payload: dict[str, Any] | None = None,
        include_tier_fields: bool = True,
    ) -> Group:
        """Update an existing mediation group.

        Args:
            app_key: Platform-specific app identifier.
            group_id: ID of the group to update.
            group: Updated group configuration.
            waterfall_payload: Optional waterfall ordering payload to include
                as ``adSourcePriority`` in the PUT body. When ``None``
                (default), no waterfall data is sent.
            include_tier_fields: When ``True`` (default), include
                ``groupName``, ``countries``, and ``position`` in the PUT
                body. When ``False``, omit these fields so the server
                preserves them (partial PUT for waterfall-only updates).

        Returns:
            The updated ``Group``.

        Raises:
            ApiError: If the API request fails.
        """
        ...

    @abstractmethod
    async def delete_group(self, app_key: str, group_id: int) -> None:
        """Delete a mediation group.

        Args:
            app_key: Platform-specific app identifier.
            group_id: ID of the group to delete.

        Raises:
            ApiError: If the API request fails.
        """
        ...

    # -- Instances -----------------------------------------------------------

    @abstractmethod
    async def get_instances(self, app_key: str) -> list[Instance]:
        """Get all ad network instances for an app.

        Args:
            app_key: Platform-specific app identifier.

        Returns:
            List of ``Instance`` models.

        Raises:
            ApiError: If the API request fails.
        """
        ...

    @abstractmethod
    async def create_instances(
        self, app_key: str, instances: list[Instance]
    ) -> list[Instance]:
        """Create ad network instances in batch.

        Note: LevelPlay rejects the entire batch if any single item fails.

        Args:
            app_key: Platform-specific app identifier.
            instances: List of instances to create.

        Returns:
            List of created ``Instance`` models with server-assigned IDs.

        Raises:
            ApiError: If the API request fails (entire batch rejected).
        """
        ...

    @abstractmethod
    async def update_instances(
        self, app_key: str, instances: list[Instance]
    ) -> list[Instance]:
        """Update ad network instances in batch.

        Args:
            app_key: Platform-specific app identifier.
            instances: List of instances to update.

        Returns:
            List of updated ``Instance`` models.

        Raises:
            ApiError: If the API request fails.
        """
        ...

    @abstractmethod
    async def delete_instance(
        self, app_key: str, instance_id: int
    ) -> None:
        """Delete an ad network instance.

        Args:
            app_key: Platform-specific app identifier.
            instance_id: ID of the instance to delete.

        Raises:
            ApiError: If the API request fails.
        """
        ...

    # -- Placements ----------------------------------------------------------

    @abstractmethod
    async def get_placements(self, app_key: str) -> list[Placement]:
        """Get all placements for an app.

        Args:
            app_key: Platform-specific app identifier.

        Returns:
            List of ``Placement`` models.

        Raises:
            ApiError: If the API request fails.
        """
        ...

    # -- Reporting -----------------------------------------------------------

    @abstractmethod
    async def get_reporting(
        self,
        app_key: str,
        start_date: str,
        end_date: str,
        metrics: list[str],
        breakdowns: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch performance reporting data for an app.

        Args:
            app_key: Platform-specific app identifier.
            start_date: Start date in ``YYYY-MM-DD`` format.
            end_date: End date in ``YYYY-MM-DD`` format.
            metrics: List of metric names to include (e.g., ``["revenue", "impressions"]``).
            breakdowns: Optional list of breakdown dimensions
                (e.g., ``["country", "network"]``).

        Returns:
            Raw reporting data as a dictionary. Shape varies by platform.

        Raises:
            ApiError: If the API request fails.
        """
        ...
