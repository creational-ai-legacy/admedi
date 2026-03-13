"""Tests for Step 8: Abstract Adapter Interfaces.

Covers:
- AdapterCapability enum members and values
- MediationAdapter cannot be instantiated directly
- StorageAdapter cannot be instantiated directly
- Concrete mock mediation adapter can be instantiated and used
- Concrete mock storage adapter can be instantiated and used
- ensure_capability succeeds for supported capabilities
- ensure_capability raises AdapterNotSupportedError for unsupported ones
- Read-only adapter with partial capabilities
- Import paths from admedi.adapters
"""

from __future__ import annotations

from typing import Any

import pytest

from admedi.adapters.mediation import AdapterCapability, MediationAdapter
from admedi.adapters.storage import StorageAdapter
from admedi.exceptions import AdapterNotSupportedError
from admedi.models.app import App
from admedi.models.config_snapshot import ConfigSnapshot
from admedi.models.group import Group
from admedi.models.instance import Instance
from admedi.models.placement import Placement
from admedi.models.sync_log import SyncLog


# ---------------------------------------------------------------------------
# Mock adapters for testing
# ---------------------------------------------------------------------------


class FullMediationAdapter(MediationAdapter):
    """Concrete adapter implementing all 12 methods with full capabilities."""

    @property
    def capabilities(self) -> set[AdapterCapability]:
        return {
            AdapterCapability.READ_GROUPS,
            AdapterCapability.WRITE_GROUPS,
            AdapterCapability.READ_INSTANCES,
            AdapterCapability.WRITE_INSTANCES,
            AdapterCapability.READ_PLACEMENTS,
            AdapterCapability.READ_REPORTING,
            AdapterCapability.AUTHENTICATE,
            AdapterCapability.LIST_APPS,
        }

    async def authenticate(self) -> None:
        pass

    async def list_apps(self) -> list[App]:
        return []

    async def get_groups(self, app_key: str) -> list[Group]:
        return []

    async def create_group(self, app_key: str, group: Group) -> Group:
        return group

    async def update_group(
        self, app_key: str, group_id: int, group: Group
    ) -> Group:
        return group

    async def delete_group(self, app_key: str, group_id: int) -> None:
        pass

    async def get_instances(self, app_key: str) -> list[Instance]:
        return []

    async def create_instances(
        self, app_key: str, instances: list[Instance]
    ) -> list[Instance]:
        return instances

    async def update_instances(
        self, app_key: str, instances: list[Instance]
    ) -> list[Instance]:
        return instances

    async def delete_instance(
        self, app_key: str, instance_id: int
    ) -> None:
        pass

    async def get_placements(self, app_key: str) -> list[Placement]:
        return []

    async def get_reporting(
        self,
        app_key: str,
        start_date: str,
        end_date: str,
        metrics: list[str],
        breakdowns: list[str] | None = None,
    ) -> dict[str, Any]:
        return {}


class ReadOnlyMediationAdapter(MediationAdapter):
    """Concrete adapter with read-only capabilities (no write operations)."""

    @property
    def capabilities(self) -> set[AdapterCapability]:
        return {
            AdapterCapability.READ_GROUPS,
            AdapterCapability.READ_INSTANCES,
            AdapterCapability.READ_PLACEMENTS,
            AdapterCapability.AUTHENTICATE,
            AdapterCapability.LIST_APPS,
        }

    async def authenticate(self) -> None:
        pass

    async def list_apps(self) -> list[App]:
        return []

    async def get_groups(self, app_key: str) -> list[Group]:
        return []

    async def create_group(self, app_key: str, group: Group) -> Group:
        return group

    async def update_group(
        self, app_key: str, group_id: int, group: Group
    ) -> Group:
        return group

    async def delete_group(self, app_key: str, group_id: int) -> None:
        pass

    async def get_instances(self, app_key: str) -> list[Instance]:
        return []

    async def create_instances(
        self, app_key: str, instances: list[Instance]
    ) -> list[Instance]:
        return instances

    async def update_instances(
        self, app_key: str, instances: list[Instance]
    ) -> list[Instance]:
        return instances

    async def delete_instance(
        self, app_key: str, instance_id: int
    ) -> None:
        pass

    async def get_placements(self, app_key: str) -> list[Placement]:
        return []

    async def get_reporting(
        self,
        app_key: str,
        start_date: str,
        end_date: str,
        metrics: list[str],
        breakdowns: list[str] | None = None,
    ) -> dict[str, Any]:
        return {}


class FullStorageAdapter(StorageAdapter):
    """Concrete storage adapter implementing all 3 methods."""

    async def save_sync_log(self, log: SyncLog) -> None:
        pass

    async def list_sync_history(self, app_key: str) -> list[SyncLog]:
        return []

    async def save_snapshot(self, snapshot: ConfigSnapshot) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests: AdapterCapability enum
# ---------------------------------------------------------------------------


class TestAdapterCapability:
    """Tests for AdapterCapability enum."""

    def test_has_eight_members(self) -> None:
        """AdapterCapability should have exactly 8 members."""
        assert len(AdapterCapability) == 8

    def test_member_values(self) -> None:
        """Each member should have the expected string value."""
        expected = {
            "READ_GROUPS": "read_groups",
            "WRITE_GROUPS": "write_groups",
            "READ_INSTANCES": "read_instances",
            "WRITE_INSTANCES": "write_instances",
            "READ_PLACEMENTS": "read_placements",
            "READ_REPORTING": "read_reporting",
            "AUTHENTICATE": "authenticate",
            "LIST_APPS": "list_apps",
        }
        for name, value in expected.items():
            member = AdapterCapability[name]
            assert member.value == value

    def test_is_string(self) -> None:
        """AdapterCapability members should be string instances."""
        for member in AdapterCapability:
            assert isinstance(member, str)

    def test_string_equality_with_value(self) -> None:
        """AdapterCapability members should compare equal to their string value."""
        for member in AdapterCapability:
            assert member == member.value
            assert member.value in repr(member)

    def test_members_are_unique(self) -> None:
        """All member values should be unique."""
        values = [m.value for m in AdapterCapability]
        assert len(values) == len(set(values))

    def test_lookup_by_value(self) -> None:
        """Should be able to construct from string value."""
        cap = AdapterCapability("read_groups")
        assert cap is AdapterCapability.READ_GROUPS


# ---------------------------------------------------------------------------
# Tests: MediationAdapter abstract behavior
# ---------------------------------------------------------------------------


class TestMediationAdapterAbstract:
    """Tests for MediationAdapter ABC enforcement."""

    def test_cannot_instantiate_directly(self) -> None:
        """MediationAdapter() should raise TypeError."""
        with pytest.raises(TypeError, match="abstract"):
            MediationAdapter()  # type: ignore[abstract]

    def test_partial_implementation_raises(self) -> None:
        """An incomplete subclass should raise TypeError on instantiation."""

        class PartialAdapter(MediationAdapter):
            @property
            def capabilities(self) -> set[AdapterCapability]:
                return set()

            async def authenticate(self) -> None:
                pass

            # Missing the other 11 methods

        with pytest.raises(TypeError):
            PartialAdapter()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Tests: Full MediationAdapter (concrete mock)
# ---------------------------------------------------------------------------


class TestFullMediationAdapter:
    """Tests for a concrete mediation adapter with all capabilities."""

    def test_can_instantiate(self) -> None:
        """A fully-implemented adapter should be instantiable."""
        adapter = FullMediationAdapter()
        assert isinstance(adapter, MediationAdapter)

    def test_capabilities_returns_all_eight(self) -> None:
        """Full adapter should report all 8 capabilities."""
        adapter = FullMediationAdapter()
        assert len(adapter.capabilities) == 8
        for cap in AdapterCapability:
            assert cap in adapter.capabilities

    def test_ensure_capability_succeeds_for_all(self) -> None:
        """ensure_capability should not raise for any supported capability."""
        adapter = FullMediationAdapter()
        for cap in AdapterCapability:
            adapter.ensure_capability(cap)  # Should not raise

    def test_is_subclass_of_abc(self) -> None:
        """FullMediationAdapter should be a subclass of MediationAdapter."""
        assert issubclass(FullMediationAdapter, MediationAdapter)


# ---------------------------------------------------------------------------
# Tests: Read-only MediationAdapter
# ---------------------------------------------------------------------------


class TestReadOnlyMediationAdapter:
    """Tests for a read-only adapter with partial capabilities."""

    def test_can_instantiate(self) -> None:
        """Read-only adapter should be instantiable."""
        adapter = ReadOnlyMediationAdapter()
        assert isinstance(adapter, MediationAdapter)

    def test_has_read_capabilities(self) -> None:
        """Read-only adapter should have read capabilities."""
        adapter = ReadOnlyMediationAdapter()
        assert AdapterCapability.READ_GROUPS in adapter.capabilities
        assert AdapterCapability.READ_INSTANCES in adapter.capabilities
        assert AdapterCapability.READ_PLACEMENTS in adapter.capabilities
        assert AdapterCapability.AUTHENTICATE in adapter.capabilities
        assert AdapterCapability.LIST_APPS in adapter.capabilities

    def test_missing_write_capabilities(self) -> None:
        """Read-only adapter should not have write capabilities."""
        adapter = ReadOnlyMediationAdapter()
        assert AdapterCapability.WRITE_GROUPS not in adapter.capabilities
        assert AdapterCapability.WRITE_INSTANCES not in adapter.capabilities
        assert AdapterCapability.READ_REPORTING not in adapter.capabilities

    def test_ensure_capability_succeeds_for_read(self) -> None:
        """ensure_capability should succeed for read capabilities."""
        adapter = ReadOnlyMediationAdapter()
        adapter.ensure_capability(AdapterCapability.READ_GROUPS)
        adapter.ensure_capability(AdapterCapability.READ_INSTANCES)
        adapter.ensure_capability(AdapterCapability.READ_PLACEMENTS)
        adapter.ensure_capability(AdapterCapability.AUTHENTICATE)
        adapter.ensure_capability(AdapterCapability.LIST_APPS)

    def test_ensure_capability_raises_for_write_groups(self) -> None:
        """ensure_capability should raise for WRITE_GROUPS."""
        adapter = ReadOnlyMediationAdapter()
        with pytest.raises(AdapterNotSupportedError, match="write_groups"):
            adapter.ensure_capability(AdapterCapability.WRITE_GROUPS)

    def test_ensure_capability_raises_for_write_instances(self) -> None:
        """ensure_capability should raise for WRITE_INSTANCES."""
        adapter = ReadOnlyMediationAdapter()
        with pytest.raises(
            AdapterNotSupportedError, match="write_instances"
        ):
            adapter.ensure_capability(AdapterCapability.WRITE_INSTANCES)

    def test_ensure_capability_raises_for_read_reporting(self) -> None:
        """ensure_capability should raise for READ_REPORTING."""
        adapter = ReadOnlyMediationAdapter()
        with pytest.raises(
            AdapterNotSupportedError, match="read_reporting"
        ):
            adapter.ensure_capability(AdapterCapability.READ_REPORTING)

    def test_ensure_capability_error_is_admedi_error(self) -> None:
        """AdapterNotSupportedError should be catchable as AdmediError."""
        from admedi.exceptions import AdmediError

        adapter = ReadOnlyMediationAdapter()
        with pytest.raises(AdmediError):
            adapter.ensure_capability(AdapterCapability.WRITE_GROUPS)

    def test_ensure_capability_error_message(self) -> None:
        """Error message should include the capability value."""
        adapter = ReadOnlyMediationAdapter()
        with pytest.raises(AdapterNotSupportedError) as exc_info:
            adapter.ensure_capability(AdapterCapability.WRITE_GROUPS)
        assert "write_groups" in str(exc_info.value)
        assert exc_info.value.message == (
            "Adapter does not support capability: write_groups"
        )


# ---------------------------------------------------------------------------
# Tests: StorageAdapter abstract behavior
# ---------------------------------------------------------------------------


class TestStorageAdapterAbstract:
    """Tests for StorageAdapter ABC enforcement."""

    def test_cannot_instantiate_directly(self) -> None:
        """StorageAdapter() should raise TypeError."""
        with pytest.raises(TypeError, match="abstract"):
            StorageAdapter()  # type: ignore[abstract]

    def test_partial_implementation_raises(self) -> None:
        """An incomplete storage subclass should raise TypeError."""

        class PartialStorage(StorageAdapter):
            async def save_sync_log(self, log: SyncLog) -> None:
                pass

            # Missing the other 2 methods

        with pytest.raises(TypeError):
            PartialStorage()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Tests: Full StorageAdapter (concrete mock)
# ---------------------------------------------------------------------------


class TestFullStorageAdapter:
    """Tests for a concrete storage adapter."""

    def test_can_instantiate(self) -> None:
        """A fully-implemented storage adapter should be instantiable."""
        adapter = FullStorageAdapter()
        assert isinstance(adapter, StorageAdapter)

    def test_is_subclass_of_abc(self) -> None:
        """FullStorageAdapter should be a subclass of StorageAdapter."""
        assert issubclass(FullStorageAdapter, StorageAdapter)


# ---------------------------------------------------------------------------
# Tests: Import paths
# ---------------------------------------------------------------------------


class TestStep8ImportPaths:
    """Tests that all Step 8 exports are importable from expected paths."""

    def test_import_from_adapters_package(self) -> None:
        """All adapters should be importable from admedi.adapters."""
        from admedi.adapters import (
            AdapterCapability,
            MediationAdapter,
            StorageAdapter,
        )

        assert AdapterCapability is not None
        assert MediationAdapter is not None
        assert StorageAdapter is not None

    def test_import_from_submodules(self) -> None:
        """Adapters should be importable from their specific submodules."""
        from admedi.adapters.mediation import (
            AdapterCapability,
            MediationAdapter,
        )
        from admedi.adapters.storage import StorageAdapter

        assert AdapterCapability is not None
        assert MediationAdapter is not None
        assert StorageAdapter is not None

    def test_adapters_all_has_five_entries(self) -> None:
        """admedi.adapters.__all__ should have 5 entries (3 base + 2 LevelPlay)."""
        import admedi.adapters

        assert len(admedi.adapters.__all__) == 5
        assert "AdapterCapability" in admedi.adapters.__all__
        assert "LevelPlayAdapter" in admedi.adapters.__all__
        assert "MediationAdapter" in admedi.adapters.__all__
        assert "StorageAdapter" in admedi.adapters.__all__
        assert "load_credential_from_env" in admedi.adapters.__all__
