"""Integration tests to verify LevelPlay Groups v4 PUT behavior.

Determines whether partial PUT payloads (only groupName, groupCountries,
groupPosition) preserve or wipe unincluded fields like instances, waterfall,
floorPrice, etc. This is the #1 production risk for the config engine.

These tests require real LevelPlay credentials in a ``.env`` file:
- ``LEVELPLAY_SECRET_KEY``
- ``LEVELPLAY_REFRESH_TOKEN``

All tests are marked with ``@pytest.mark.integration`` and are **deselected
by default** via ``addopts = "-m 'not integration'"`` in ``pyproject.toml``.

To run::

    uv run pytest tests/test_levelplay_put_behavior.py -m integration -v -s
"""

from __future__ import annotations

import copy
import json
import logging

import pytest

from admedi.adapters.levelplay import LevelPlayAdapter, load_credential_from_env
from admedi.constants import GROUPS_V4_URL, MEDIATION_MGMT_V2_URL
from admedi.exceptions import AuthError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared fixture: load credential or skip
# ---------------------------------------------------------------------------


@pytest.fixture
def credential() -> "Credential":
    """Load real LevelPlay credentials from .env, or skip if unavailable."""
    try:
        return load_credential_from_env()
    except AuthError as exc:
        pytest.skip(f"LevelPlay credentials not available: {exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers(adapter: LevelPlayAdapter) -> dict[str, str]:
    """Build Authorization header from the adapter's cached bearer token."""
    return {"Authorization": f"Bearer {adapter._bearer_token}"}


async def _raw_get_groups(adapter: LevelPlayAdapter, app_key: str) -> list[dict]:
    """GET groups via raw httpx, bypassing model parsing."""
    resp = await adapter._client.request(
        "GET",
        f"{GROUPS_V4_URL}/{app_key}",
        headers=_auth_headers(adapter),
    )
    assert resp.status_code == 200, f"Raw GET groups failed: {resp.status_code} {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLevelPlayPutBehavior:
    """Verify LevelPlay Groups v4 PUT behavior with partial payloads.

    Tests are numbered to enforce execution order: shape dump (read-only),
    throwaway group POST+PUT (minimal risk), then the critical preservation
    test on a real group.
    """

    async def test_01_raw_response_shape(self, credential) -> None:
        """Document the actual fields returned by Groups v4 GET (read-only).

        Zero risk. Prints all top-level keys, their types, and flags any
        keys not present in our Group model.
        """
        model_fields = {
            "groupId", "groupName", "adFormat", "countries", "position",
            "floorPrice", "abTest", "instances", "waterfall",
            "mediationAdUnitId", "mediationAdUnitName", "segments",
        }

        async with LevelPlayAdapter(credential) as adapter:
            await adapter.authenticate()
            apps = await adapter.list_apps()
            assert len(apps) > 0, "Need at least one app"

            app_key = apps[0].app_key
            raw_groups = await _raw_get_groups(adapter, app_key)

            print(f"\n  App: {apps[0].app_name} (key={app_key})")
            print(f"  Total groups: {len(raw_groups)}")

            all_keys: set[str] = set()
            for group in raw_groups:
                all_keys.update(group.keys())

                print(f"\n  --- Group: {group.get('groupName', '?')} (id={group.get('groupId', '?')}) ---")
                for key in sorted(group.keys()):
                    val = group[key]
                    type_name = type(val).__name__
                    if isinstance(val, list):
                        type_name = f"list[{len(val)} items]"
                    elif isinstance(val, dict):
                        type_name = f"dict[{len(val)} keys]"
                    elif isinstance(val, str) and len(val) > 60:
                        val = val[:60] + "..."
                    print(f"    {key}: {type_name} = {val}")

                # Print instance sub-keys if present
                instances = group.get("instances")
                if instances and isinstance(instances, list) and len(instances) > 0:
                    print(f"    >> Instance keys: {sorted(instances[0].keys())}")

            # Flag undocumented keys
            undocumented = all_keys - model_fields
            if undocumented:
                print(f"\n  UNDOCUMENTED KEYS (not in Group model): {sorted(undocumented)}")
            else:
                print("\n  All keys are covered by the Group model.")

    async def test_02_put_partial_on_new_group(self, credential) -> None:
        """Create a throwaway group, PUT with partial payload, then clean up.

        Uses Antarctica (AQ) to avoid any traffic impact. Proves the API
        accepts partial PUT payloads at minimum.

        Tries multiple apps and ad formats to find a combination that the
        API accepts (some apps restrict group creation due to bidding-only
        mode or position constraints).

        API learnings encoded here:
        - POST uses GET-style field names: ``countries``, ``position``
          (not ``groupCountries`` / ``groupPosition`` used by PUT)
        - ``adFormat`` values: ``rewarded`` (not ``rewardedVideo``),
          ``interstitial``, ``banner``, ``native``
        - ``position`` must be ≤ (existing group count for that format + 1)
        """
        test_group_name = "_admedi_test_put_behavior"
        ad_formats = ["interstitial", "banner", "rewarded"]
        created_group_id: int | None = None
        used_app_key: str | None = None
        used_position: int = 1

        async with LevelPlayAdapter(credential) as adapter:
            await adapter.authenticate()
            apps = await adapter.list_apps()
            assert len(apps) > 0, "Need at least one app"

            headers = _auth_headers(adapter)

            # Try each app + format combination until POST succeeds
            post_ok = False
            last_error = ""
            for app in apps:
                # Get existing groups to compute valid position per format
                try:
                    raw_groups = await _raw_get_groups(adapter, app.app_key)
                except AssertionError:
                    continue

                for ad_format in ad_formats:
                    # Count existing groups for this format to set valid position
                    format_count = sum(
                        1 for g in raw_groups if g.get("adFormat") == ad_format
                    )
                    position = format_count + 1

                    post_payload = [{
                        "groupName": test_group_name,
                        "adFormat": ad_format,
                        "countries": ["AQ"],
                        "position": position,
                    }]

                    post_resp = await adapter._client.request(
                        "POST",
                        f"{GROUPS_V4_URL}/{app.app_key}",
                        json=post_payload,
                        headers=headers,
                    )
                    if post_resp.status_code in (200, 201):
                        post_ok = True
                        used_app_key = app.app_key
                        used_position = position
                        print(f"\n  POST succeeded on app={app.app_name} format={ad_format} position={position}")
                        print(f"  POST status: {post_resp.status_code}")
                        print(f"  POST response: {post_resp.text[:500]}")
                        break
                    last_error = f"{app.app_name}/{ad_format}: {post_resp.status_code} {post_resp.text[:200]}"
                    print(f"  POST rejected: {last_error}")

                if post_ok:
                    break

            if not post_ok:
                pytest.skip(
                    f"Could not create a throwaway group on any app/format. "
                    f"Last error: {last_error}"
                )

            try:
                # GET: find the created group
                raw_groups = await _raw_get_groups(adapter, used_app_key)
                created = [g for g in raw_groups if g.get("groupName") == test_group_name]
                assert len(created) == 1, (
                    f"Expected 1 group named '{test_group_name}', found {len(created)}"
                )
                created_group_id = created[0]["groupId"]
                print(f"  Created group ID: {created_group_id}")

                # PUT: partial update with same values (no-op)
                put_payload = [{
                    "groupId": created_group_id,
                    "groupName": test_group_name,
                    "groupCountries": ["AQ"],
                    "groupPosition": used_position,
                }]
                print(f"  PUT payload: {json.dumps(put_payload, indent=2)}")

                put_resp = await adapter._client.request(
                    "PUT",
                    f"{GROUPS_V4_URL}/{used_app_key}",
                    json=put_payload,
                    headers=headers,
                )
                print(f"  PUT status: {put_resp.status_code}")
                print(f"  PUT response: {put_resp.text[:500]}")
                assert put_resp.status_code in (200, 204), (
                    f"PUT failed: {put_resp.status_code} {put_resp.text}"
                )

                print("  SUCCESS: API accepts partial PUT payload on new group")

            finally:
                # Cleanup: delete the throwaway group
                if created_group_id is not None:
                    await self._cleanup_group(adapter, used_app_key, created_group_id, headers)

    async def test_03_put_preserves_unincluded_fields(self, credential) -> None:
        """THE critical test: partial PUT must not wipe instances/waterfall.

        Finds a group with preservation targets (instances, floorPrice, etc.),
        sends a PUT with only groupId/groupName/groupCountries/groupPosition
        using the exact same current values (no-op write), then verifies all
        non-sent fields are unchanged.
        """
        # Fields we will send in PUT — everything else must survive
        sent_keys = {"groupId", "groupName", "groupCountries", "groupPosition"}

        async with LevelPlayAdapter(credential) as adapter:
            await adapter.authenticate()
            apps = await adapter.list_apps()
            assert len(apps) > 0, "Need at least one app"

            app_key = apps[0].app_key
            headers = _auth_headers(adapter)

            # GET: full raw state
            raw_groups = await _raw_get_groups(adapter, app_key)

            # Score groups by richness of preservation targets
            def _score(g: dict) -> int:
                score = 0
                instances = g.get("instances")
                if instances and isinstance(instances, list) and len(instances) > 0:
                    score += 3  # highest weight
                if g.get("floorPrice") is not None and g.get("floorPrice") != 0:
                    score += 1
                if g.get("mediationAdUnitId"):
                    score += 1
                waterfall = g.get("waterfall")
                if waterfall and isinstance(waterfall, (dict, list)):
                    score += 2
                if g.get("segments"):
                    score += 1
                # Penalize groups with active A/B tests
                ab = g.get("abTest")
                if ab is not None and ab != "N/A":
                    score -= 10
                return score

            scored = sorted(raw_groups, key=_score, reverse=True)
            best = scored[0] if scored else None

            if best is None or _score(best) <= 0:
                pytest.skip("No group with preservation targets found (all groups are empty or have A/B tests)")

            group_id = best["groupId"]
            group_name = best["groupName"]
            print(f"\n  Target group: {group_name} (id={group_id})")
            print(f"  Preservation score: {_score(best)}")

            # Deep-copy pre-state
            pre_state = copy.deepcopy(best)

            # Build no-op PUT payload using exact current values
            # API field mapping: GET 'countries' → PUT 'groupCountries',
            #                    GET 'position' → PUT 'groupPosition'
            put_payload = [{
                "groupId": group_id,
                "groupName": group_name,
                "groupCountries": best["countries"],
                "groupPosition": best["position"],
            }]
            print(f"  PUT payload: {json.dumps(put_payload, indent=2)}")

            # PUT
            put_resp = await adapter._client.request(
                "PUT",
                f"{GROUPS_V4_URL}/{app_key}",
                json=put_payload,
                headers=headers,
            )
            print(f"  PUT status: {put_resp.status_code}")
            print(f"  PUT response: {put_resp.text[:500]}")
            assert put_resp.status_code in (200, 204), (
                f"PUT failed: {put_resp.status_code} {put_resp.text}"
            )

            # GET: post-state
            post_groups = await _raw_get_groups(adapter, app_key)
            post_state = next(
                (g for g in post_groups if g.get("groupId") == group_id), None
            )
            assert post_state is not None, f"Group {group_id} disappeared after PUT!"

            # Compare every non-sent field
            # Map PUT field names back to GET field names for exclusion
            get_equivalents_of_sent = {"groupId", "groupName", "countries", "position"}
            check_keys = set(pre_state.keys()) | set(post_state.keys())
            check_keys -= get_equivalents_of_sent

            mismatches: list[str] = []
            print("\n  Field                    | Pre-PUT              | Post-PUT             | Match?")
            print("  " + "─" * 80)

            for key in sorted(check_keys):
                pre_val = pre_state.get(key)
                post_val = post_state.get(key)

                # Format display values
                if isinstance(pre_val, list):
                    pre_display = f"list[{len(pre_val)} items]"
                elif isinstance(pre_val, dict):
                    pre_display = f"dict[{len(pre_val)} keys]"
                else:
                    pre_display = str(pre_val)[:20]

                if isinstance(post_val, list):
                    post_display = f"list[{len(post_val)} items]"
                elif isinstance(post_val, dict):
                    post_display = f"dict[{len(post_val)} keys]"
                else:
                    post_display = str(post_val)[:20]

                match = pre_val == post_val
                status = "YES" if match else "NO !!!"
                print(f"  {key:<25}| {pre_display:<21}| {post_display:<21}| {status}")

                if not match:
                    mismatches.append(key)

            if mismatches:
                # Dump full diff for mismatched fields
                print(f"\n  MISMATCHED FIELDS: {mismatches}")
                for key in mismatches:
                    print(f"\n  === {key} DIFF ===")
                    print(f"  PRE:  {json.dumps(pre_state.get(key), indent=2, default=str)}")
                    print(f"  POST: {json.dumps(post_state.get(key), indent=2, default=str)}")

            assert not mismatches, (
                f"Partial PUT WIPED fields: {mismatches}. "
                "The config engine must use read-modify-write instead of sparse PUT."
            )
            print("\n  RESULT: Partial PUT preserves all unincluded fields.")

    # -- Cleanup helpers -------------------------------------------------------

    @staticmethod
    async def _cleanup_group(
        adapter: LevelPlayAdapter,
        app_key: str,
        group_id: int,
        headers: dict[str, str],
    ) -> None:
        """Attempt to delete a throwaway group. Try v4 DELETE, then v2 fallback."""
        # v4 DELETE with correct payload: {"ids": [groupId]}
        # Discovered 2026-03-12: API expects {"ids": [int]} not [{"groupId": int}]
        try:
            del_resp = await adapter._client.request(
                "DELETE",
                f"{GROUPS_V4_URL}/{app_key}",
                json={"ids": [group_id]},
                headers=headers,
            )
            if del_resp.status_code in (200, 204):
                print(f"  Cleanup: deleted group {group_id} via v4 DELETE")
                return
            print(f"  Cleanup: v4 DELETE returned {del_resp.status_code}: {del_resp.text[:200]}")
        except Exception as exc:
            print(f"  Cleanup: v4 DELETE failed: {exc}")

        # Fallback: try v2 DELETE
        try:
            del_resp = await adapter._client.request(
                "DELETE",
                MEDIATION_MGMT_V2_URL,
                params={"appKey": app_key, "groupId": str(group_id)},
                headers=headers,
            )
            if del_resp.status_code in (200, 204):
                print(f"  Cleanup: deleted group {group_id} via v2 DELETE")
                return
            print(f"  Cleanup: v2 DELETE returned {del_resp.status_code}: {del_resp.text[:200]}")
        except Exception as exc:
            print(f"  Cleanup: v2 DELETE failed: {exc}")

        print(f"  WARNING: Could not delete test group {group_id}. Manual cleanup required.")
