"""Tests for LevelPlayAdapter group write operations (Step 7).

Tests cover:
- _request() returns parsed JSON for 201 responses
- _request() returns {} (empty dict) for 204 responses
- _request() handles empty body on 200 (returns {} empty dict)
- create_group() sends POST with correct URL and payload fields
- create_group() follow-up get_groups() returns created Group
- update_group() sends PUT with groupId present and prefixed field names
- delete_group() sends DELETE with {"ids": [group_id]} body
- create_group() and update_group() raise ValueError for AdFormat.REWARDED_VIDEO
- capabilities now includes WRITE_GROUPS
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from admedi.adapters.levelplay import LevelPlayAdapter
from admedi.adapters.mediation import AdapterCapability
from admedi.constants import GROUPS_V4_URL
from admedi.exceptions import ApiError
from admedi.models.credential import Credential
from admedi.models.enums import AdFormat, Mediator
from admedi.models.group import Group


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int = 200,
    json_body: Any = None,
    text: str = "",
    content_type: str = "application/json",
) -> httpx.Response:
    """Build a mock httpx.Response with the given status, body, and content-type."""
    headers = {"content-type": content_type}
    if json_body is not None:
        body_bytes = json.dumps(json_body).encode()
    elif text:
        body_bytes = text.encode()
    else:
        body_bytes = b""
    return httpx.Response(
        status_code=status_code,
        headers=headers,
        content=body_bytes,
        request=httpx.Request("GET", "https://example.com"),
    )


def _make_adapter() -> LevelPlayAdapter:
    """Create a LevelPlayAdapter with a mock credential and pre-set bearer token."""
    cred = Credential(
        mediator=Mediator.LEVELPLAY,
        secret_key="test_secret_key_abc123",
        refresh_token="test_refresh_token_xyz789",
    )
    adapter = LevelPlayAdapter(cred)
    # Pre-set token to avoid auth calls in tests
    adapter._bearer_token = "fake-token"
    from datetime import UTC, datetime, timedelta

    adapter._token_expiry = datetime.now(UTC) + timedelta(hours=1)
    return adapter


def _make_group(
    name: str = "Test Group",
    ad_format: str = "interstitial",
    countries: list[str] | None = None,
    position: int = 1,
    group_id: int | None = None,
) -> Group:
    """Create a Group model with the given fields."""
    data: dict[str, Any] = {
        "groupName": name,
        "adFormat": ad_format,
        "countries": countries or ["US"],
        "position": position,
    }
    if group_id is not None:
        data["groupId"] = group_id
    return Group.model_validate(data)


# ---------------------------------------------------------------------------
# Tests: _request() 2xx range handling
# ---------------------------------------------------------------------------


class TestRequestSuccessRange:
    """Tests that _request() handles the full 2xx status range correctly."""

    async def test_request_returns_json_for_201(self) -> None:
        """_request() should return parsed JSON for 201 Created."""
        adapter = _make_adapter()
        response = _make_response(status_code=201, json_body={"id": 42, "name": "new"})

        with patch.object(adapter._client, "request", new_callable=AsyncMock, return_value=response):
            result = await adapter._request("POST", "https://example.com/api")

        assert result == {"id": 42, "name": "new"}

    async def test_request_returns_empty_dict_for_204(self) -> None:
        """_request() should return {} for 204 No Content (empty body)."""
        adapter = _make_adapter()
        response = _make_response(status_code=204, text="")

        with patch.object(adapter._client, "request", new_callable=AsyncMock, return_value=response):
            result = await adapter._request("DELETE", "https://example.com/api")

        assert result == {}

    async def test_request_handles_empty_body_on_200(self) -> None:
        """_request() should return {} when 200 response has empty body."""
        adapter = _make_adapter()
        response = _make_response(status_code=200, text="")

        with patch.object(adapter._client, "request", new_callable=AsyncMock, return_value=response):
            result = await adapter._request("POST", "https://example.com/api")

        assert result == {}

    async def test_request_handles_null_body_on_200(self) -> None:
        """_request() should return {} when 200 response body is 'null'."""
        adapter = _make_adapter()
        response = _make_response(status_code=200, text="null")

        with patch.object(adapter._client, "request", new_callable=AsyncMock, return_value=response):
            result = await adapter._request("POST", "https://example.com/api")

        assert result == {}

    async def test_request_returns_json_for_200_with_body(self) -> None:
        """_request() should still return parsed JSON for 200 with content."""
        adapter = _make_adapter()
        response = _make_response(status_code=200, json_body=[{"groupId": 1}])

        with patch.object(adapter._client, "request", new_callable=AsyncMock, return_value=response):
            result = await adapter._request("GET", "https://example.com/api")

        assert result == [{"groupId": 1}]

    async def test_request_returns_text_for_200_non_json(self) -> None:
        """_request() should return raw text for 200 with non-JSON content."""
        adapter = _make_adapter()
        response = _make_response(
            status_code=200, text="plain text response", content_type="text/plain"
        )

        with patch.object(adapter._client, "request", new_callable=AsyncMock, return_value=response):
            result = await adapter._request("GET", "https://example.com/api")

        assert result == "plain text response"


# ---------------------------------------------------------------------------
# Tests: create_group()
# ---------------------------------------------------------------------------


class TestCreateGroup:
    """Tests for create_group() POST operation."""

    async def test_create_group_sends_post_with_correct_url_and_payload(self) -> None:
        """create_group() should POST to {GROUPS_V4_URL}/{app_key} with GET-style fields."""
        adapter = _make_adapter()
        group = _make_group(name="US Tier 1", ad_format="interstitial", countries=["US"], position=1)

        # Mock _request to capture the call, then mock get_groups for follow-up
        created_group = _make_group(
            name="US Tier 1", ad_format="interstitial", countries=["US"], position=1, group_id=999
        )

        request_calls: list[tuple[str, str, dict[str, Any] | None]] = []

        async def mock_request(method: str, url: str, *, json_body: Any = None, **kwargs: Any) -> Any:
            request_calls.append((method, url, json_body))
            if method == "POST":
                return {}
            # GET for get_groups follow-up
            return [
                {
                    "groupId": 999,
                    "groupName": "US Tier 1",
                    "adFormat": "interstitial",
                    "countries": ["US"],
                    "position": 1,
                }
            ]

        with patch.object(adapter, "_request", side_effect=mock_request):
            result = await adapter.create_group("app123", group)

        # Verify POST call
        post_method, post_url, post_payload = request_calls[0]
        assert post_method == "POST"
        assert post_url == f"{GROUPS_V4_URL}/app123"
        assert post_payload == [{
            "groupName": "US Tier 1",
            "adFormat": "interstitial",
            "countries": ["US"],
            "position": 1,
        }]

        # Verify result
        assert result.group_id == 999
        assert result.group_name == "US Tier 1"

    async def test_create_group_matches_by_name_and_format(self) -> None:
        """create_group() should match by both groupName AND adFormat on follow-up."""
        adapter = _make_adapter()
        group = _make_group(name="Test", ad_format="banner", countries=["US"], position=1)

        async def mock_request(method: str, url: str, *, json_body: Any = None, **kwargs: Any) -> Any:
            if method == "POST":
                return {}
            # Return multiple groups: same name different format, same format different name
            return [
                {
                    "groupId": 100,
                    "groupName": "Test",
                    "adFormat": "interstitial",
                    "countries": ["US"],
                    "position": 1,
                },
                {
                    "groupId": 200,
                    "groupName": "Other",
                    "adFormat": "banner",
                    "countries": ["US"],
                    "position": 2,
                },
                {
                    "groupId": 300,
                    "groupName": "Test",
                    "adFormat": "banner",
                    "countries": ["US"],
                    "position": 3,
                },
            ]

        with patch.object(adapter, "_request", side_effect=mock_request):
            result = await adapter.create_group("app123", group)

        # Should match group_id=300 (name=Test AND format=banner)
        assert result.group_id == 300

    async def test_create_group_raises_value_error_for_rewarded_video(self) -> None:
        """create_group() should reject AdFormat.REWARDED_VIDEO with ValueError."""
        adapter = _make_adapter()
        group = _make_group(name="RV Group", ad_format="rewardedVideo", countries=["US"], position=1)

        with pytest.raises(ValueError, match="REWARDED_VIDEO"):
            await adapter.create_group("app123", group)

    async def test_create_group_raises_api_error_when_not_found(self) -> None:
        """create_group() should raise ApiError if created group not found in follow-up."""
        adapter = _make_adapter()
        group = _make_group(name="Missing", ad_format="interstitial", countries=["US"], position=1)

        async def mock_request(method: str, url: str, *, json_body: Any = None, **kwargs: Any) -> Any:
            if method == "POST":
                return {}
            return []  # Empty -- group not found

        with patch.object(adapter, "_request", side_effect=mock_request):
            with pytest.raises(ApiError, match="not found after creation"):
                await adapter.create_group("app123", group)


# ---------------------------------------------------------------------------
# Tests: update_group()
# ---------------------------------------------------------------------------


class TestUpdateGroup:
    """Tests for update_group() PUT operation."""

    async def test_update_group_sends_put_with_correct_fields(self) -> None:
        """update_group() should PUT with list-wrapped payload using GET-style field names."""
        adapter = _make_adapter()
        group = _make_group(
            name="Updated Tier", ad_format="interstitial", countries=["US", "CA"], position=2
        )

        request_calls: list[tuple[str, str, dict[str, Any] | None]] = []

        async def mock_request(method: str, url: str, *, json_body: Any = None, **kwargs: Any) -> Any:
            request_calls.append((method, url, json_body))
            if method == "PUT":
                return {}
            # GET for get_groups follow-up
            return [
                {
                    "groupId": 555,
                    "groupName": "Updated Tier",
                    "adFormat": "interstitial",
                    "countries": ["US", "CA"],
                    "position": 2,
                }
            ]

        with patch.object(adapter, "_request", side_effect=mock_request):
            result = await adapter.update_group("app123", 555, group)

        # Verify PUT call
        put_method, put_url, put_payload = request_calls[0]
        assert put_method == "PUT"
        assert put_url == f"{GROUPS_V4_URL}/app123"
        assert put_payload == [{
            "groupId": 555,
            "adFormat": "interstitial",
            "groupName": "Updated Tier",
            "countries": ["US", "CA"],
            "position": 2,
        }]

        # Verify result
        assert result.group_id == 555
        assert result.group_name == "Updated Tier"

    async def test_update_group_raises_value_error_for_rewarded_video(self) -> None:
        """update_group() should reject AdFormat.REWARDED_VIDEO with ValueError."""
        adapter = _make_adapter()
        group = _make_group(name="RV Group", ad_format="rewardedVideo", countries=["US"], position=1)

        with pytest.raises(ValueError, match="REWARDED_VIDEO"):
            await adapter.update_group("app123", 123, group)

    async def test_update_group_raises_api_error_when_not_found(self) -> None:
        """update_group() should raise ApiError if updated group not found."""
        adapter = _make_adapter()
        group = _make_group(name="Missing", ad_format="interstitial", countries=["US"], position=1)

        async def mock_request(method: str, url: str, *, json_body: Any = None, **kwargs: Any) -> Any:
            if method == "PUT":
                return {}
            return []  # Empty -- group not found

        with patch.object(adapter, "_request", side_effect=mock_request):
            with pytest.raises(ApiError, match="not found after update"):
                await adapter.update_group("app123", 999, group)


# ---------------------------------------------------------------------------
# Tests: delete_group()
# ---------------------------------------------------------------------------


class TestDeleteGroup:
    """Tests for delete_group() DELETE operation."""

    async def test_delete_group_sends_delete_with_ids_body(self) -> None:
        """delete_group() should DELETE with {"ids": [group_id]} body."""
        adapter = _make_adapter()

        request_calls: list[tuple[str, str, dict[str, Any] | None]] = []

        async def mock_request(method: str, url: str, *, json_body: Any = None, **kwargs: Any) -> Any:
            request_calls.append((method, url, json_body))
            return {}

        with patch.object(adapter, "_request", side_effect=mock_request):
            await adapter.delete_group("app123", 777)

        assert len(request_calls) == 1
        method, url, body = request_calls[0]
        assert method == "DELETE"
        assert url == f"{GROUPS_V4_URL}/app123"
        assert body == {"ids": [777]}

    async def test_delete_group_returns_none(self) -> None:
        """delete_group() should return None (no return value)."""
        adapter = _make_adapter()

        async def mock_request(method: str, url: str, *, json_body: Any = None, **kwargs: Any) -> Any:
            return {}

        with patch.object(adapter, "_request", side_effect=mock_request):
            result = await adapter.delete_group("app123", 777)

        assert result is None


# ---------------------------------------------------------------------------
# Tests: Capabilities with WRITE_GROUPS
# ---------------------------------------------------------------------------


class TestWriteGroupsCapability:
    """Tests that capabilities now includes WRITE_GROUPS."""

    def test_capabilities_includes_write_groups(self) -> None:
        """capabilities should include WRITE_GROUPS."""
        adapter = _make_adapter()
        assert AdapterCapability.WRITE_GROUPS in adapter.capabilities

    def test_ensure_capability_write_groups_passes(self) -> None:
        """ensure_capability(WRITE_GROUPS) should not raise."""
        adapter = _make_adapter()
        adapter.ensure_capability(AdapterCapability.WRITE_GROUPS)

    def test_capabilities_still_includes_read_groups(self) -> None:
        """Existing READ_GROUPS capability must not be removed."""
        adapter = _make_adapter()
        assert AdapterCapability.READ_GROUPS in adapter.capabilities

    def test_capabilities_count(self) -> None:
        """capabilities should now have 5 entries."""
        adapter = _make_adapter()
        assert len(adapter.capabilities) == 5


# ---------------------------------------------------------------------------
# Tests: update_group() with waterfall_payload and include_tier_fields
# ---------------------------------------------------------------------------


class TestUpdateGroupWaterfallPayload:
    """Tests for update_group() with waterfall_payload and include_tier_fields parameters."""

    async def test_update_group_with_waterfall_payload_includes_ad_source_priority(
        self,
    ) -> None:
        """update_group() PUT payload includes adSourcePriority when waterfall_payload is provided."""
        adapter = _make_adapter()
        group = _make_group(
            name="Tier 1", ad_format="interstitial", countries=["US"], position=1
        )

        waterfall = {
            "bidding": {
                "tierType": "bidding",
                "instances": [{"providerName": "Meta", "instanceId": 101}],
            }
        }

        request_calls: list[tuple[str, str, dict[str, Any] | None]] = []

        async def mock_request(
            method: str, url: str, *, json_body: Any = None, **kwargs: Any
        ) -> Any:
            request_calls.append((method, url, json_body))
            if method == "PUT":
                return {}
            return [
                {
                    "groupId": 555,
                    "groupName": "Tier 1",
                    "adFormat": "interstitial",
                    "countries": ["US"],
                    "position": 1,
                }
            ]

        with patch.object(adapter, "_request", side_effect=mock_request):
            await adapter.update_group(
                "app123", 555, group, waterfall_payload=waterfall
            )

        put_payload = request_calls[0][2]
        assert put_payload is not None
        assert len(put_payload) == 1
        assert "adSourcePriority" in put_payload[0]
        assert put_payload[0]["adSourcePriority"] == waterfall

    async def test_update_group_without_waterfall_payload_no_ad_source_priority(
        self,
    ) -> None:
        """update_group() without waterfall_payload produces same payload as before (backward compatible)."""
        adapter = _make_adapter()
        group = _make_group(
            name="Tier 1", ad_format="interstitial", countries=["US"], position=1
        )

        request_calls: list[tuple[str, str, dict[str, Any] | None]] = []

        async def mock_request(
            method: str, url: str, *, json_body: Any = None, **kwargs: Any
        ) -> Any:
            request_calls.append((method, url, json_body))
            if method == "PUT":
                return {}
            return [
                {
                    "groupId": 555,
                    "groupName": "Tier 1",
                    "adFormat": "interstitial",
                    "countries": ["US"],
                    "position": 1,
                }
            ]

        with patch.object(adapter, "_request", side_effect=mock_request):
            await adapter.update_group("app123", 555, group)

        put_payload = request_calls[0][2]
        assert put_payload is not None
        assert len(put_payload) == 1
        assert "adSourcePriority" not in put_payload[0]
        # Verify backward-compatible payload structure
        assert put_payload[0] == {
            "groupId": 555,
            "adFormat": "interstitial",
            "groupName": "Tier 1",
            "countries": ["US"],
            "position": 1,
        }

    async def test_update_group_include_tier_fields_false_omits_tier_fields(
        self,
    ) -> None:
        """update_group() with include_tier_fields=False omits groupName, countries, position."""
        adapter = _make_adapter()
        group = _make_group(
            name="Tier 1", ad_format="interstitial", countries=["US"], position=1
        )

        waterfall = {
            "bidding": {
                "tierType": "bidding",
                "instances": [{"providerName": "Meta", "instanceId": 101}],
            }
        }

        request_calls: list[tuple[str, str, dict[str, Any] | None]] = []

        async def mock_request(
            method: str, url: str, *, json_body: Any = None, **kwargs: Any
        ) -> Any:
            request_calls.append((method, url, json_body))
            if method == "PUT":
                return {}
            return [
                {
                    "groupId": 555,
                    "groupName": "Tier 1",
                    "adFormat": "interstitial",
                    "countries": ["US"],
                    "position": 1,
                }
            ]

        with patch.object(adapter, "_request", side_effect=mock_request):
            await adapter.update_group(
                "app123",
                555,
                group,
                waterfall_payload=waterfall,
                include_tier_fields=False,
            )

        put_payload = request_calls[0][2]
        assert put_payload is not None
        assert len(put_payload) == 1
        payload_dict = put_payload[0]
        # Only groupId, adFormat, and adSourcePriority should be present
        assert "groupId" in payload_dict
        assert "adFormat" in payload_dict
        assert "adSourcePriority" in payload_dict
        # Tier fields should be absent
        assert "groupName" not in payload_dict
        assert "countries" not in payload_dict
        assert "position" not in payload_dict

    async def test_update_group_include_tier_fields_true_includes_all_fields(
        self,
    ) -> None:
        """update_group() with include_tier_fields=True (default) includes all fields."""
        adapter = _make_adapter()
        group = _make_group(
            name="Tier 1", ad_format="interstitial", countries=["US", "CA"], position=3
        )

        request_calls: list[tuple[str, str, dict[str, Any] | None]] = []

        async def mock_request(
            method: str, url: str, *, json_body: Any = None, **kwargs: Any
        ) -> Any:
            request_calls.append((method, url, json_body))
            if method == "PUT":
                return {}
            return [
                {
                    "groupId": 777,
                    "groupName": "Tier 1",
                    "adFormat": "interstitial",
                    "countries": ["US", "CA"],
                    "position": 3,
                }
            ]

        with patch.object(adapter, "_request", side_effect=mock_request):
            await adapter.update_group(
                "app123", 777, group, include_tier_fields=True
            )

        put_payload = request_calls[0][2]
        assert put_payload is not None
        payload_dict = put_payload[0]
        assert payload_dict["groupId"] == 777
        assert payload_dict["adFormat"] == "interstitial"
        assert payload_dict["groupName"] == "Tier 1"
        assert payload_dict["countries"] == ["US", "CA"]
        assert payload_dict["position"] == 3
