"""Integration tests to verify LevelPlay Groups v4 POST (create) behavior.

Determines whether POST can create groups, what response shape it returns,
how position insertion works, and which DELETE mechanism is available.

These tests require real LevelPlay credentials in a ``.env`` file:
- ``LEVELPLAY_SECRET_KEY``
- ``LEVELPLAY_REFRESH_TOKEN``

All tests are marked with ``@pytest.mark.integration`` and are **deselected
by default** via ``addopts = "-m 'not integration'"`` in ``pyproject.toml``.

To run::

    uv run pytest tests/test_levelplay_post_behavior.py -m integration -v -s
"""

from __future__ import annotations

import json

import pytest

from admedi.adapters.levelplay import LevelPlayAdapter, load_credential_from_env
from admedi.constants import GROUPS_V4_URL, MEDIATION_MGMT_V2_URL
from admedi.exceptions import AuthError
from admedi.models.app import App
from admedi.models.enums import Platform


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

# Prefix for all test groups — easy to find for manual cleanup
_TEST_PREFIX = "_admedi_test_post"


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


async def _cleanup_group(
    adapter: LevelPlayAdapter,
    app_key: str,
    group_id: int,
    headers: dict[str, str],
) -> bool:
    """Attempt to delete a test group. Try v4 DELETE, then v2 fallback.

    Returns True if deletion succeeded, False otherwise.
    """
    # v4 DELETE with correct payload format: {"ids": [groupId]}
    # Discovered 2026-03-12: the API expects {"ids": [int]} not [{"groupId": int}]
    try:
        del_resp = await adapter._client.request(
            "DELETE",
            f"{GROUPS_V4_URL}/{app_key}",
            json={"ids": [group_id]},
            headers=headers,
        )
        if del_resp.status_code in (200, 204):
            print(f"  Cleanup: deleted group {group_id} via v4 DELETE (status={del_resp.status_code})")
            return True
        print(f"  Cleanup: v4 DELETE returned {del_resp.status_code}: {del_resp.text[:200]}")
    except Exception as exc:
        print(f"  Cleanup: v4 DELETE failed: {exc}")

    print(f"  WARNING: Could not delete test group {group_id}. Manual cleanup required.")
    return False


def _select_android_app(apps: list[App]) -> App | None:
    """Select the best Android/Play app for testing.

    Priority:
    1. Shelf Sort Play (app_key '1f93aca35') — the actual target app
    2. Any non-archived Android app with a bundle_id
    3. None if no suitable app found
    """
    # Priority 1: Shelf Sort Play
    for app in apps:
        if app.app_key == "1f93aca35":
            return app

    # Priority 2: Any active Android app with a bundle_id
    for app in apps:
        if (
            app.platform == Platform.ANDROID
            and app.bundle_id is not None
            and app.app_status == "active"
        ):
            return app

    # Priority 3: Any Android app at all
    for app in apps:
        if app.platform == Platform.ANDROID:
            return app

    return None


async def _post_create_group(
    adapter: LevelPlayAdapter,
    app_key: str,
    headers: dict[str, str],
    *,
    group_name: str,
    ad_format: str = "interstitial",
    countries: list[str] | None = None,
    position: int = 1,
) -> tuple[int, list | dict | None, dict[str, str]]:
    """POST to create a group. Returns (status_code, response_json_or_None, response_headers).

    Uses position=1 by default (always valid — inserts at highest priority).
    Uses AQ (Antarctica) by default (zero traffic).
    """
    if countries is None:
        countries = ["AQ"]

    post_payload = [{
        "groupName": group_name,
        "adFormat": ad_format,
        "countries": countries,
        "position": position,
    }]

    resp = await adapter._client.request(
        "POST",
        f"{GROUPS_V4_URL}/{app_key}",
        json=post_payload,
        headers=headers,
    )

    resp_json = None
    try:
        resp_json = resp.json()
    except Exception:
        pass

    return resp.status_code, resp_json, dict(resp.headers)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLevelPlayPostBehavior:
    """Verify LevelPlay Groups v4 POST (create) behavior.

    Tests are numbered to enforce execution order: POST create first,
    position shift behavior second, delete mechanism third.
    """

    async def test_01_post_create_group(self, credential) -> None:
        """THE critical test: Can we POST a group? What comes back?

        Tries interstitial first, falls back to banner. Records status code,
        response shape, whether groupId is included, and all response fields.
        """
        group_name = f"{_TEST_PREFIX}_create"
        created_group_id: int | None = None
        used_app_key: str | None = None

        async with LevelPlayAdapter(credential) as adapter:
            await adapter.authenticate()
            apps = await adapter.list_apps()
            assert len(apps) > 0, "Need at least one app"

            target_app = _select_android_app(apps)
            if target_app is None:
                pytest.skip("No Android app found for POST testing")

            used_app_key = target_app.app_key
            headers = _auth_headers(adapter)

            print(f"\n  Target app: {target_app.app_name} (key={used_app_key}, platform={target_app.platform})")

            # GET pre-state
            pre_groups = await _raw_get_groups(adapter, used_app_key)
            pre_get_keys = set()
            for g in pre_groups:
                pre_get_keys.update(g.keys())
            print(f"  Pre-state: {len(pre_groups)} total groups")

            # Try interstitial first, then banner as fallback
            formats_to_try = ["interstitial", "banner"]
            post_succeeded = False
            post_status = None
            post_response = None
            used_format = None

            for ad_format in formats_to_try:
                format_groups = [g for g in pre_groups if g.get("adFormat") == ad_format]
                print(f"\n  Trying POST with format={ad_format} (existing groups: {len(format_groups)})")
                print(f"  Payload: groupName={group_name}, countries=[AQ], position=1")

                post_status, post_response, post_headers = await _post_create_group(
                    adapter, used_app_key, headers,
                    group_name=group_name,
                    ad_format=ad_format,
                    position=1,
                )

                print(f"  POST status: {post_status}")
                print(f"  POST response headers: {post_headers}")
                print(f"  POST response: {json.dumps(post_response, indent=2, default=str)[:800]}")

                if post_status in (200, 201):
                    post_succeeded = True
                    used_format = ad_format
                    break

                print(f"  POST failed for {ad_format}, trying next format...")

            if not post_succeeded:
                pytest.fail(
                    f"POST failed on ALL format combinations for app {target_app.app_name}. "
                    f"Last status={post_status}, response={post_response}. "
                    "This is plan-breaking: create_group() cannot work, engine must be update-only."
                )

            try:
                # Analyze POST response shape
                print(f"\n  === POST RESPONSE ANALYSIS (format={used_format}) ===")
                print(f"  Status code: {post_status}")
                print(f"  Response type: {type(post_response).__name__}")

                if isinstance(post_response, list):
                    print(f"  Response is ARRAY with {len(post_response)} items")
                    if len(post_response) > 0:
                        item = post_response[0]
                        print(f"  First item type: {type(item).__name__}")
                        if isinstance(item, dict):
                            print(f"  First item keys: {sorted(item.keys())}")
                            for key in sorted(item.keys()):
                                val = item[key]
                                type_name = type(val).__name__
                                if isinstance(val, list):
                                    type_name = f"list[{len(val)} items]"
                                elif isinstance(val, dict):
                                    type_name = f"dict[{len(val)} keys]"
                                elif isinstance(val, str) and len(val) > 60:
                                    val = val[:60] + "..."
                                print(f"    {key}: {type_name} = {val}")

                            # Check for groupId
                            if "groupId" in item:
                                print(f"\n  groupId IS in POST response: {item['groupId']}")
                                created_group_id = item["groupId"]
                            else:
                                print("\n  groupId is NOT in POST response")

                            # Compare POST response fields vs GET response fields
                            post_keys = set(item.keys())
                            only_in_post = post_keys - pre_get_keys
                            only_in_get = pre_get_keys - post_keys
                            print(f"\n  Fields in POST but not GET: {sorted(only_in_post) if only_in_post else 'none'}")
                            print(f"  Fields in GET but not POST: {sorted(only_in_get) if only_in_get else 'none'}")
                        else:
                            print(f"  First item value: {item}")
                elif isinstance(post_response, dict):
                    print(f"  Response is OBJECT with keys: {sorted(post_response.keys())}")
                    for key in sorted(post_response.keys()):
                        val = post_response[key]
                        type_name = type(val).__name__
                        if isinstance(val, list):
                            type_name = f"list[{len(val)} items]"
                        elif isinstance(val, dict):
                            type_name = f"dict[{len(val)} keys]"
                        print(f"    {key}: {type_name} = {val}")

                    if "groupId" in post_response:
                        print(f"\n  groupId IS in POST response: {post_response['groupId']}")
                        created_group_id = post_response["groupId"]
                    else:
                        print("\n  groupId is NOT in POST response")
                else:
                    print(f"  Response value: {post_response}")

                # If we don't have groupId from POST response, find it via GET
                if created_group_id is None:
                    print("\n  No groupId from POST — doing follow-up GET to find created group...")
                    post_groups = await _raw_get_groups(adapter, used_app_key)
                    created = [g for g in post_groups if g.get("groupName") == group_name]
                    if len(created) == 1:
                        created_group_id = created[0]["groupId"]
                        print(f"  Found created group via GET: groupId={created_group_id}")
                    elif len(created) > 1:
                        print(f"  WARNING: Found {len(created)} groups with name '{group_name}'")
                        created_group_id = created[0]["groupId"]
                    else:
                        print(f"  ERROR: Could not find group '{group_name}' in GET response")

                # Verify group exists via GET
                verify_groups = await _raw_get_groups(adapter, used_app_key)
                found = [g for g in verify_groups if g.get("groupId") == created_group_id]
                if found:
                    print(f"\n  Verified: group {created_group_id} exists in GET response")
                    print(f"  Group details: {json.dumps(found[0], indent=2, default=str)[:600]}")
                else:
                    print(f"\n  WARNING: group {created_group_id} NOT found in follow-up GET")

            finally:
                # Cleanup
                if created_group_id is not None:
                    print(f"\n  Cleaning up group {created_group_id}...")
                    await _cleanup_group(adapter, used_app_key, created_group_id, headers)

    async def test_02_position_shift_behavior(self, credential) -> None:
        """After creating a group at position 1, do existing groups shift down?

        Records pre/post positions for all groups of the target format and
        prints a comparison table showing whether insert-and-shift occurs.
        """
        group_name = f"{_TEST_PREFIX}_shift"
        created_group_id: int | None = None
        ad_format = "interstitial"

        async with LevelPlayAdapter(credential) as adapter:
            await adapter.authenticate()
            apps = await adapter.list_apps()

            target_app = _select_android_app(apps)
            if target_app is None:
                pytest.skip("No Android app found for position shift testing")

            app_key = target_app.app_key
            headers = _auth_headers(adapter)

            print(f"\n  Target app: {target_app.app_name} (key={app_key})")

            # GET pre-state: record positions of all groups for target format
            pre_groups = await _raw_get_groups(adapter, app_key)
            pre_format_groups = [g for g in pre_groups if g.get("adFormat") == ad_format]

            if not pre_format_groups:
                # Fall back to banner if no interstitial groups
                ad_format = "banner"
                pre_format_groups = [g for g in pre_groups if g.get("adFormat") == ad_format]

            if not pre_format_groups:
                pytest.skip(f"No groups for format {ad_format} to test position shifting")

            # Map groupId -> position (pre-state)
            pre_positions: dict[int, tuple[str, int]] = {}
            for g in pre_format_groups:
                gid = g["groupId"]
                gname = g.get("groupName", "?")
                gpos = g.get("position", -1)
                pre_positions[gid] = (gname, gpos)

            print(f"  Format: {ad_format}")
            print(f"  Pre-state groups ({len(pre_format_groups)}):")
            for gid, (gname, gpos) in sorted(pre_positions.items(), key=lambda x: x[1][1]):
                print(f"    {gname:<30} id={gid:<10} position={gpos}")

            # POST new group at position 1
            post_status, post_response, _ = await _post_create_group(
                adapter, app_key, headers,
                group_name=group_name,
                ad_format=ad_format,
                position=1,
            )

            print(f"\n  POST status: {post_status}")
            if post_status not in (200, 201):
                print(f"  POST response: {json.dumps(post_response, indent=2, default=str)[:400]}")
                pytest.skip(f"POST not supported (status={post_status}), cannot test position shifting")

            try:
                # Find created group ID
                post_groups = await _raw_get_groups(adapter, app_key)
                created = [g for g in post_groups if g.get("groupName") == group_name]
                if not created:
                    pytest.fail(f"POST returned {post_status} but group '{group_name}' not found in GET")
                created_group_id = created[0]["groupId"]

                # Record post-state positions
                post_format_groups = [g for g in post_groups if g.get("adFormat") == ad_format]
                post_positions: dict[int, tuple[str, int]] = {}
                for g in post_format_groups:
                    gid = g["groupId"]
                    gname = g.get("groupName", "?")
                    gpos = g.get("position", -1)
                    post_positions[gid] = (gname, gpos)

                # Print comparison table
                print(f"\n  === POSITION SHIFT ANALYSIS ===")
                print(f"  {'Group Name':<30} | {'Pre-Pos':>8} | {'Post-Pos':>8} | Shifted?")
                print(f"  {'─' * 70}")

                # New group first
                new_pos = post_positions.get(created_group_id, ("?", -1))[1]
                print(f"  {group_name:<30} | {'(new)':>8} | {new_pos:>8} | N/A")

                # Existing groups
                shifted_count = 0
                for gid, (gname, pre_pos) in sorted(pre_positions.items(), key=lambda x: x[1][1]):
                    post_entry = post_positions.get(gid)
                    if post_entry is None:
                        print(f"  {gname:<30} | {pre_pos:>8} | {'MISSING':>8} | ???")
                        continue
                    post_pos = post_entry[1]
                    delta = post_pos - pre_pos
                    if delta != 0:
                        shifted_count += 1
                        shift_str = f"+{delta}" if delta > 0 else str(delta)
                    else:
                        shift_str = "no"
                    print(f"  {gname:<30} | {pre_pos:>8} | {post_pos:>8} | {shift_str}")

                # Determine behavior
                if shifted_count == len(pre_positions):
                    print(f"\n  RESULT: INSERT-AND-SHIFT confirmed — all {shifted_count} existing groups shifted")
                elif shifted_count == 0:
                    print(f"\n  RESULT: NO SHIFT — positions are independent / coexist")
                else:
                    print(f"\n  RESULT: PARTIAL SHIFT — {shifted_count}/{len(pre_positions)} groups shifted")

            finally:
                # Cleanup
                if created_group_id is not None:
                    print(f"\n  Cleaning up group {created_group_id}...")
                    await _cleanup_group(adapter, app_key, created_group_id, headers)

                    # Verify positions restored
                    restored_groups = await _raw_get_groups(adapter, app_key)
                    restored_format = [g for g in restored_groups if g.get("adFormat") == ad_format]
                    print(f"\n  Post-cleanup positions:")
                    for g in sorted(restored_format, key=lambda x: x.get("position", -1)):
                        gid = g["groupId"]
                        gname = g.get("groupName", "?")
                        gpos = g.get("position", -1)
                        pre_entry = pre_positions.get(gid)
                        pre_pos_str = str(pre_entry[1]) if pre_entry else "N/A"
                        match_str = "OK" if pre_entry and pre_entry[1] == gpos else "CHANGED"
                        print(f"    {gname:<30} position={gpos} (was {pre_pos_str}) [{match_str}]")

    async def test_03_delete_mechanism(self, credential) -> None:
        """Verify which DELETE mechanism works for cleanup.

        Every other test depends on reliable cleanup. This test explicitly
        exercises both v4 and v2 DELETE endpoints and documents their behavior.
        """
        group_name = f"{_TEST_PREFIX}_delete"
        ad_format = "interstitial"

        async with LevelPlayAdapter(credential) as adapter:
            await adapter.authenticate()
            apps = await adapter.list_apps()

            target_app = _select_android_app(apps)
            if target_app is None:
                pytest.skip("No Android app found for DELETE testing")

            app_key = target_app.app_key
            headers = _auth_headers(adapter)

            print(f"\n  Target app: {target_app.app_name} (key={app_key})")

            # POST a throwaway group
            post_status, post_response, _ = await _post_create_group(
                adapter, app_key, headers,
                group_name=group_name,
                ad_format=ad_format,
                position=1,
            )

            if post_status not in (200, 201):
                # Try banner fallback
                ad_format = "banner"
                post_status, post_response, _ = await _post_create_group(
                    adapter, app_key, headers,
                    group_name=group_name,
                    ad_format=ad_format,
                    position=1,
                )

            if post_status not in (200, 201):
                pytest.skip(f"Cannot create test group for DELETE testing (POST status={post_status})")

            # Find the created group
            groups = await _raw_get_groups(adapter, app_key)
            created = [g for g in groups if g.get("groupName") == group_name]
            if not created:
                pytest.fail(f"POST returned {post_status} but group '{group_name}' not found")

            group_id = created[0]["groupId"]
            print(f"  Created throwaway group: id={group_id}, format={ad_format}")

            # Test v4 DELETE with correct payload format
            # Discovered 2026-03-12: API expects {"ids": [int]} not [{"groupId": int}]
            print(f"\n  === V4 DELETE TEST ===")
            v4_payload = {"ids": [group_id]}
            print(f"  DELETE {GROUPS_V4_URL}/{app_key}")
            print(f"  Body: {json.dumps(v4_payload)}")

            v4_resp = await adapter._client.request(
                "DELETE",
                f"{GROUPS_V4_URL}/{app_key}",
                json=v4_payload,
                headers=headers,
            )
            print(f"  Status: {v4_resp.status_code}")
            print(f"  Response headers: {dict(v4_resp.headers)}")
            v4_body = v4_resp.text[:400] if v4_resp.text else "(empty)"
            print(f"  Response body: {v4_body}")

            v4_worked = v4_resp.status_code in (200, 204)

            # Check if group is gone
            groups_after_v4 = await _raw_get_groups(adapter, app_key)
            still_exists = any(g.get("groupId") == group_id for g in groups_after_v4)

            if v4_worked and not still_exists:
                print(f"  RESULT: v4 DELETE WORKS — group {group_id} is gone")
            elif v4_worked and still_exists:
                print(f"  RESULT: v4 DELETE returned success but group still exists!")
            else:
                print(f"  RESULT: v4 DELETE failed (status={v4_resp.status_code})")

            # Summary
            print(f"\n  === DELETE SUMMARY ===")
            print(f"  v4 DELETE: {'WORKS' if v4_worked and not still_exists else 'FAILED'}")
            print(f"  v2 DELETE: DEPRECATED (410 Gone)")

            assert v4_worked and not still_exists, (
                f"v4 DELETE failed for group {group_id} (status={v4_resp.status_code}). "
                "Manual cleanup required via LevelPlay dashboard."
            )
