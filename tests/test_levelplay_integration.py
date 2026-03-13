"""Integration tests for LevelPlayAdapter against the live LevelPlay API.

These tests require real LevelPlay credentials in a ``.env`` file:
- ``LEVELPLAY_SECRET_KEY``
- ``LEVELPLAY_REFRESH_TOKEN``

All tests are marked with ``@pytest.mark.integration`` and are **deselected
by default** via ``addopts = "-m 'not integration'"`` in ``pyproject.toml``.

To run integration tests::

    uv run pytest -m integration -v

If credentials are not available, all tests skip gracefully.
"""

from __future__ import annotations

import logging

import pytest

from admedi.adapters.levelplay import LevelPlayAdapter, load_credential_from_env
from admedi.exceptions import AuthError
from admedi.models import Credential

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared fixture: load credential or skip
# ---------------------------------------------------------------------------


@pytest.fixture
def credential() -> Credential:
    """Load real LevelPlay credentials from .env, or skip if unavailable.

    This fixture calls ``load_credential_from_env()`` and catches
    ``AuthError`` to gracefully skip when credentials are not configured.
    """
    try:
        return load_credential_from_env()
    except AuthError as exc:
        pytest.skip(f"LevelPlay credentials not available: {exc}")


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLevelPlayIntegration:
    """Integration tests for LevelPlayAdapter against the live API.

    All tests use real credentials and make real HTTP requests. They are
    skipped by default and must be run explicitly with ``-m integration``.

    Each test prints key fields from the response for manual comparison
    with the LevelPlay dashboard.
    """

    async def test_authenticate(self, credential: Credential) -> None:
        """Authenticate with the live LevelPlay OAuth endpoint.

        Verifies that a real Bearer JWT is obtained and cached with a
        valid expiry timestamp.
        """
        async with LevelPlayAdapter(credential) as adapter:
            await adapter.authenticate()

            assert adapter._bearer_token is not None, "Bearer token should be set"
            assert adapter._token_expiry is not None, "Token expiry should be set"
            assert adapter._token_expiry.tzinfo is not None, "Expiry should be timezone-aware"

            print(f"\n  Token (masked): {adapter._mask_token(adapter._bearer_token)}")
            print(f"  Token expiry: {adapter._token_expiry.isoformat()}")

    async def test_list_apps(self, credential: Credential) -> None:
        """List all apps from the live LevelPlay Applications API.

        Verifies that at least one app is returned and prints key fields
        for manual dashboard comparison.
        """
        async with LevelPlayAdapter(credential) as adapter:
            apps = await adapter.list_apps()

            assert len(apps) > 0, "Expected at least one app from LevelPlay"

            print(f"\n  Total apps: {len(apps)}")
            for app in apps:
                print(
                    f"  - {app.app_name} | key={app.app_key} | "
                    f"platform={app.platform} | bundle={app.bundle_id}"
                )

    async def test_get_groups(self, credential: Credential) -> None:
        """Get mediation groups for the first app from the live API.

        Uses ``list_apps()`` to find a real app key, then fetches its
        mediation groups. Verifies that the response parses into valid
        ``Group`` models and prints key fields.
        """
        async with LevelPlayAdapter(credential) as adapter:
            apps = await adapter.list_apps()
            assert len(apps) > 0, "Need at least one app to test get_groups"

            app_key = apps[0].app_key
            groups = await adapter.get_groups(app_key)

            print(f"\n  App: {apps[0].app_name} (key={app_key})")
            print(f"  Total groups: {len(groups)}")
            for group in groups:
                instance_count = len(group.instances) if group.instances else 0
                countries = group.countries[:5] if group.countries else []
                countries_display = ", ".join(countries)
                if group.countries and len(group.countries) > 5:
                    countries_display += f" ... (+{len(group.countries) - 5} more)"
                print(
                    f"  - {group.group_name} | id={group.group_id} | "
                    f"format={group.ad_format} | countries=[{countries_display}] | "
                    f"instances={instance_count} | ab_test={group.ab_test}"
                )

    async def test_get_instances(self, credential: Credential) -> None:
        """Test standalone Instances API and group-embedded instances.

        The standalone Instances API (v3 and v1) returns 410 Gone as of
        2026. The adapter should handle this gracefully by returning an
        empty list. Instance data is now only available embedded in
        Groups v4 responses.
        """
        async with LevelPlayAdapter(credential) as adapter:
            apps = await adapter.list_apps()
            assert len(apps) > 0, "Need at least one app to test get_instances"

            app_key = apps[0].app_key

            # Standalone API returns 410 — adapter should return empty list
            instances = await adapter.get_instances(app_key)
            print(f"\n  App: {apps[0].app_name} (key={app_key})")
            print(f"  Standalone instances: {len(instances)} (expected 0 — API is 410 Gone)")

            # Instance data is embedded in Groups v4 responses
            groups = await adapter.get_groups(app_key)
            total_instances = sum(
                len(g.instances) for g in groups if g.instances
            )
            print(f"  Group-embedded instances: {total_instances}")
            assert total_instances > 0, (
                "Expected at least one instance embedded in groups"
            )

            # Print a sample of embedded instances
            for group in groups:
                if group.instances:
                    for inst in group.instances[:2]:
                        print(
                            f"  - [{group.group_name}/{group.ad_format.value}] "
                            f"{inst.instance_name} | id={inst.instance_id} | "
                            f"network={inst.network_name} | bidder={inst.is_bidder}"
                        )
