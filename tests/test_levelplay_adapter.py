"""Unit tests for LevelPlayAdapter -- Steps 1, 2, 3, 4, 5, 6, & 7.

Step 1 tests cover:
- Adapter instantiation with Credential
- Context manager support (close behavior)
- _mask_token() output format
- Rate limit counter increment and window pruning
- 90% warning threshold logging
- Rate limit exhaustion raises RateLimitError
- Unconfigured endpoint passes rate limit check without tracking
- _request() returns parsed JSON on 200 OK
- _request() returns raw text for non-JSON responses
- _request() retries once on 401
- _request() retries up to 3 times on 429 with backoff
- _request() uses Retry-After header when present on 429
- _request() retries up to 2 times on 5xx
- _request() raises ApiError immediately on non-429 4xx
- _request() skips Authorization header when _bearer_token is None

Step 2 tests cover:
- Token acquisition from mocked auth endpoint
- Token caching (second call skips re-fetch)
- Refresh triggers when expiry < 5 min
- Credential change invalidates cache
- Millisecond exp handling
- _ensure_authenticated auto-refresh
- 401 response triggers re-auth via authenticate() (not _ensure_authenticated)
- Token never appears in log output or exception messages
- Credential fingerprinting

Step 3 tests cover:
- load_credential_from_env() returns valid Credential when both env vars are set
- Missing LEVELPLAY_SECRET_KEY raises AuthError
- Missing LEVELPLAY_REFRESH_TOKEN raises AuthError
- Empty string for either var raises AuthError
- Both vars missing raises AuthError naming both
- Returned Credential has mediator=Mediator.LEVELPLAY
- Returned values match env var values exactly

Step 4 tests cover:
- list_apps() returns list[App] with correct fields from fixture data
- list_apps() returns empty list for empty API response
- list_apps() skips apps with unknown platform (e.g., "WebGL") with warning
- list_apps() returns correct app_key, app_name, platform, bundle_id
- Each returned App has mediator=Mediator.LEVELPLAY (default)
- Fixture JSON file loaded correctly
- Ad units nested data parsed correctly

Step 5 tests cover:
- get_groups() returns list[Group] with correct fields from fixture data
- get_groups() returns empty list for empty API response
- get_groups() skips groups with unknown adFormat (e.g., "native") with warning
- Embedded Instance parsing with correct fields
- A/B test detection logged as warning
- Group with abTest "N/A" does not trigger warning
- Group with abTest null does not trigger warning
- Fixture JSON file validation (structure, content)
- Correct URL and endpoint_key used

Step 6 tests cover:
- get_instances() returns list[Instance] with correct fields from v3 fixture data
- get_instances() v3 404 -> v1 fallback with warning logged
- _normalize_instance_response() field name normalization (providerName -> networkName, etc.)
- _normalize_instance_response() countriesPricing sub-field normalization (country -> countryCode, eCPM -> rate)
- _normalize_instance_response() isLive string normalization ("active" -> True, "inactive" -> False)
- Object wrapper extraction (dict with "instances" key -> list)
- Empty response -> empty list
- Instances with missing optional fields still parse correctly
- Fixture JSON file validation (structure, alternate field names)

Step 7 tests cover:
- Concurrent rate limit counter access (3 coroutines, no data corruption)
- Semaphore limits concurrent requests to 10 (launch 15, verify max 10 in-flight)
- Each of 5 stub methods raises AdapterNotSupportedError with descriptive message
- capabilities returns {AUTHENTICATE, LIST_APPS, READ_GROUPS, READ_INSTANCES, WRITE_GROUPS}
- ensure_capability passes for supported capabilities (including WRITE_GROUPS)
- ensure_capability raises AdapterNotSupportedError for unsupported capabilities
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import jwt
import pytest

from admedi.adapters.levelplay import (
    RATE_LIMITS,
    LevelPlayAdapter,
    _RATE_LIMIT_WARNING_THRESHOLD,
    _TOKEN_REFRESH_MARGIN,
    load_credential_from_env,
)
from admedi.adapters.mediation import AdapterCapability
from admedi.constants import APPS_URL, AUTH_URL, GROUPS_V4_URL, INSTANCES_V1_URL, INSTANCES_V3_URL
from admedi.exceptions import AdapterNotSupportedError, ApiError, AuthError, RateLimitError
from admedi.models import Credential, Group, Mediator
from admedi.models.app import App
from admedi.models.enums import AdFormat, Platform
from admedi.models.instance import CountryRate, Instance

# JWT signing key for test tokens (>= 32 bytes per PyJWT RFC 7518 requirement)
_TEST_JWT_KEY = "test-secret-key-at-least-32-bytes-long"

# Path to test fixtures directory
_FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int = 200,
    json_data: dict | list | None = None,
    text: str = "",
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Create a mock httpx.Response with the given attributes."""
    resp_headers = {"content-type": content_type}
    if headers:
        resp_headers.update(headers)

    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.headers = resp_headers
    response.text = text if text else ""

    if json_data is not None:
        response.json.return_value = json_data
        if not text:
            response.text = str(json_data)
    else:
        response.json.side_effect = ValueError("No JSON")

    return response


def _make_jwt_token(
    exp_offset_seconds: int = 3600,
    exp_milliseconds: bool = False,
    sub: str = "test-publisher",
) -> str:
    """Create a JWT token with a known exp claim.

    Args:
        exp_offset_seconds: How many seconds from now the token expires.
        exp_milliseconds: If True, exp is in milliseconds (to test ms handling).
        sub: Subject claim value.

    Returns:
        Encoded JWT string.
    """
    now = int(time.time())
    exp = now + exp_offset_seconds
    if exp_milliseconds:
        exp = exp * 1000  # Convert to milliseconds
    payload = {"sub": sub, "iat": now, "exp": exp}
    return jwt.encode(payload, _TEST_JWT_KEY, algorithm="HS256")


def _make_auth_response(token: str) -> httpx.Response:
    """Create a mock auth endpoint response returning a JWT string."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.text = f'"{token}"'  # Auth endpoint wraps token in quotes
    response.headers = {"content-type": "text/plain"}
    response.json.side_effect = ValueError("Not JSON")
    return response


def _pre_authenticate(adapter: LevelPlayAdapter) -> None:
    """Set adapter to pre-authenticated state so _ensure_authenticated skips.

    This helper is used by Step 1 tests to avoid auth side effects when
    testing _request() behavior.
    """
    adapter._bearer_token = "pre-auth-test-token-for-step1-tests"
    adapter._token_expiry = datetime.now(UTC) + timedelta(hours=1)


# ---------------------------------------------------------------------------
# Tests: Adapter Instantiation
# ---------------------------------------------------------------------------


class TestLevelPlayAdapterInit:
    """Tests for LevelPlayAdapter instantiation."""

    def test_instantiates_with_credential(self, mock_credential: Credential) -> None:
        """Adapter should instantiate without error given a valid Credential."""
        adapter = LevelPlayAdapter(mock_credential)
        assert adapter is not None
        assert adapter._credential is mock_credential

    def test_creates_httpx_client(self, mock_credential: Credential) -> None:
        """Adapter should create a persistent httpx.AsyncClient on init."""
        adapter = LevelPlayAdapter(mock_credential)
        assert isinstance(adapter._client, httpx.AsyncClient)

    def test_client_has_custom_user_agent(self, mock_credential: Credential) -> None:
        """httpx client should have the admedi User-Agent header."""
        adapter = LevelPlayAdapter(mock_credential)
        assert adapter._client.headers.get("user-agent") == "admedi/0.1.0"

    def test_bearer_token_initially_none(self, mock_credential: Credential) -> None:
        """Bearer token should be None before authentication."""
        adapter = LevelPlayAdapter(mock_credential)
        assert adapter._bearer_token is None

    def test_token_expiry_initially_none(self, mock_credential: Credential) -> None:
        """Token expiry should be None before authentication."""
        adapter = LevelPlayAdapter(mock_credential)
        assert adapter._token_expiry is None

    def test_rate_counters_initially_empty(self, mock_credential: Credential) -> None:
        """Rate limit counters should start empty."""
        adapter = LevelPlayAdapter(mock_credential)
        assert adapter._rate_counters == {}

    def test_semaphore_created(self, mock_credential: Credential) -> None:
        """Semaphore for concurrency limiting should be created."""
        adapter = LevelPlayAdapter(mock_credential)
        assert isinstance(adapter._semaphore, asyncio.Semaphore)

    def test_rate_lock_created(self, mock_credential: Credential) -> None:
        """asyncio.Lock for rate counter access should be created."""
        adapter = LevelPlayAdapter(mock_credential)
        assert isinstance(adapter._rate_lock, asyncio.Lock)

    def test_credential_fingerprint_computed(self, mock_credential: Credential) -> None:
        """Credential fingerprint should be computed on init."""
        adapter = LevelPlayAdapter(mock_credential)
        expected = hashlib.sha256(
            (mock_credential.secret_key + mock_credential.refresh_token).encode()
        ).hexdigest()
        assert adapter._credential_fingerprint == expected


# ---------------------------------------------------------------------------
# Tests: Context Manager
# ---------------------------------------------------------------------------


class TestLevelPlayAdapterContextManager:
    """Tests for async context manager support."""

    async def test_context_manager_returns_adapter(
        self, mock_credential: Credential
    ) -> None:
        """async with should return the adapter instance."""
        async with LevelPlayAdapter(mock_credential) as adapter:
            assert isinstance(adapter, LevelPlayAdapter)

    async def test_context_manager_closes_client(
        self, mock_credential: Credential
    ) -> None:
        """Exiting context manager should close the httpx client."""
        adapter = LevelPlayAdapter(mock_credential)
        # Replace the real client with a mock to verify close is called
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        adapter._client = mock_client

        async with adapter:
            pass

        mock_client.aclose.assert_awaited_once()

    async def test_close_method(self, mock_credential: Credential) -> None:
        """close() should close the httpx client."""
        adapter = LevelPlayAdapter(mock_credential)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        adapter._client = mock_client

        await adapter.close()

        mock_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: _mask_token
# ---------------------------------------------------------------------------


class TestMaskToken:
    """Tests for _mask_token() helper."""

    def test_standard_token(self, mock_credential: Credential) -> None:
        """Standard token should show first 8 + ... + last 4 chars."""
        adapter = LevelPlayAdapter(mock_credential)
        result = adapter._mask_token("abcdefgh1234XYZ7890")
        assert result == "abcdefgh...7890"

    def test_short_token_returns_stars(self, mock_credential: Credential) -> None:
        """Token with 12 or fewer chars should return '***'."""
        adapter = LevelPlayAdapter(mock_credential)
        assert adapter._mask_token("short") == "***"
        assert adapter._mask_token("exactly12chr") == "***"

    def test_13_char_token_shows_partial(self, mock_credential: Credential) -> None:
        """13-char token should show first 8 + ... + last 4."""
        adapter = LevelPlayAdapter(mock_credential)
        result = adapter._mask_token("1234567890abc")
        assert result == "12345678...0abc"

    def test_empty_token(self, mock_credential: Credential) -> None:
        """Empty token should return '***'."""
        adapter = LevelPlayAdapter(mock_credential)
        assert adapter._mask_token("") == "***"


# ---------------------------------------------------------------------------
# Tests: Rate Limit Tracking
# ---------------------------------------------------------------------------


class TestRateLimitTracking:
    """Tests for _check_rate_limit() sliding window deque."""

    async def test_counter_increments(self, mock_credential: Credential) -> None:
        """Each call should add a timestamp to the endpoint's deque."""
        adapter = LevelPlayAdapter(mock_credential)

        await adapter._check_rate_limit("groups")
        assert len(adapter._rate_counters["groups"]) == 1

        await adapter._check_rate_limit("groups")
        assert len(adapter._rate_counters["groups"]) == 2

        await adapter._check_rate_limit("groups")
        assert len(adapter._rate_counters["groups"]) == 3

    async def test_prunes_old_timestamps(self, mock_credential: Credential) -> None:
        """Timestamps outside the sliding window should be pruned."""
        adapter = LevelPlayAdapter(mock_credential)
        _, window_seconds = RATE_LIMITS["groups"]

        # Manually insert an old timestamp outside the window
        old_timestamp = time.monotonic() - window_seconds - 10
        adapter._rate_counters["groups"] = deque([old_timestamp])

        await adapter._check_rate_limit("groups")

        # Old timestamp should be pruned, only the new one remains
        assert len(adapter._rate_counters["groups"]) == 1
        assert adapter._rate_counters["groups"][0] > old_timestamp

    async def test_unconfigured_endpoint_passes(
        self, mock_credential: Credential
    ) -> None:
        """Endpoints not in RATE_LIMITS should pass without tracking."""
        adapter = LevelPlayAdapter(mock_credential)

        await adapter._check_rate_limit("default")
        assert "default" not in adapter._rate_counters

        await adapter._check_rate_limit("apps")
        assert "apps" not in adapter._rate_counters

    async def test_exhaustion_raises_rate_limit_error(
        self, mock_credential: Credential
    ) -> None:
        """Rate limit exhaustion should raise RateLimitError."""
        adapter = LevelPlayAdapter(mock_credential)
        max_requests, _ = RATE_LIMITS["groups"]

        # Fill the counter to exactly the limit
        now = time.monotonic()
        adapter._rate_counters["groups"] = deque(
            [now - i * 0.001 for i in range(max_requests)]
        )

        with pytest.raises(RateLimitError, match="Rate limit exhausted"):
            await adapter._check_rate_limit("groups")

    async def test_warning_at_90_percent(
        self, mock_credential: Credential, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Warning should be logged when usage reaches 90% of budget."""
        adapter = LevelPlayAdapter(mock_credential)
        max_requests, _ = RATE_LIMITS["groups"]
        threshold = int(max_requests * _RATE_LIMIT_WARNING_THRESHOLD)

        # Fill the counter to just below warning threshold
        now = time.monotonic()
        adapter._rate_counters["groups"] = deque(
            [now - i * 0.001 for i in range(threshold)]
        )

        with caplog.at_level("WARNING", logger="admedi.adapters.levelplay"):
            await adapter._check_rate_limit("groups")

        assert "Rate limit warning" in caplog.text
        assert "groups" in caplog.text

    async def test_no_warning_below_threshold(
        self, mock_credential: Credential, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No warning should be logged when usage is below 90%."""
        adapter = LevelPlayAdapter(mock_credential)
        max_requests, _ = RATE_LIMITS["groups"]
        threshold = int(max_requests * _RATE_LIMIT_WARNING_THRESHOLD)

        # Fill to one below threshold
        now = time.monotonic()
        adapter._rate_counters["groups"] = deque(
            [now - i * 0.001 for i in range(threshold - 2)]
        )

        with caplog.at_level("WARNING", logger="admedi.adapters.levelplay"):
            await adapter._check_rate_limit("groups")

        assert "Rate limit warning" not in caplog.text

    async def test_rate_limit_error_has_retry_after(
        self, mock_credential: Credential
    ) -> None:
        """RateLimitError should include a retry_after value."""
        adapter = LevelPlayAdapter(mock_credential)
        max_requests, _ = RATE_LIMITS["groups"]

        now = time.monotonic()
        adapter._rate_counters["groups"] = deque(
            [now - i * 0.001 for i in range(max_requests)]
        )

        with pytest.raises(RateLimitError) as exc_info:
            await adapter._check_rate_limit("groups")

        assert exc_info.value.retry_after is not None
        assert exc_info.value.retry_after > 0


# ---------------------------------------------------------------------------
# Tests: _request() -- Success Cases
# ---------------------------------------------------------------------------


class TestRequestSuccess:
    """Tests for _request() successful responses.

    These tests pre-authenticate the adapter so _ensure_authenticated()
    short-circuits and doesn't interfere with mock call counts.
    """

    async def test_returns_json_dict(self, mock_credential: Credential) -> None:
        """200 with JSON dict should return parsed dict."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = _make_response(
            200, json_data={"key": "value"}
        )
        adapter._client = mock_client

        result = await adapter._request("GET", "https://api.example.com/test")
        assert result == {"key": "value"}

    async def test_returns_json_list(self, mock_credential: Credential) -> None:
        """200 with JSON list should return parsed list."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = _make_response(
            200, json_data=[{"id": 1}, {"id": 2}]
        )
        adapter._client = mock_client

        result = await adapter._request("GET", "https://api.example.com/test")
        assert result == [{"id": 1}, {"id": 2}]

    async def test_returns_raw_text_for_non_json(
        self, mock_credential: Credential
    ) -> None:
        """200 with non-JSON content should return raw text."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        response.headers = {"content-type": "text/plain"}
        response.text = "raw-jwt-token-string"
        response.json.side_effect = ValueError("Not JSON")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = response
        adapter._client = mock_client

        result = await adapter._request("GET", "https://api.example.com/auth")
        assert result == "raw-jwt-token-string"

    async def test_skips_auth_header_when_no_token(
        self, mock_credential: Credential
    ) -> None:
        """When _bearer_token is None, no Authorization header should be sent.

        Note: _ensure_authenticated is patched to no-op because the adapter
        has no token yet, and we want to test header injection, not auth.
        """
        adapter = LevelPlayAdapter(mock_credential)
        assert adapter._bearer_token is None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = _make_response(
            200, json_data={"ok": True}
        )
        adapter._client = mock_client

        # Patch _ensure_authenticated to no-op so it doesn't trigger auth
        with patch.object(adapter, "_ensure_authenticated", new_callable=AsyncMock):
            await adapter._request("GET", "https://api.example.com/test")

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert "Authorization" not in headers

    async def test_sends_auth_header_when_token_set(
        self, mock_credential: Credential
    ) -> None:
        """When _bearer_token is set, Authorization header should be included."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)
        adapter._bearer_token = "test-jwt-token"

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = _make_response(
            200, json_data={"ok": True}
        )
        adapter._client = mock_client

        await adapter._request("GET", "https://api.example.com/test")

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer test-jwt-token"


# ---------------------------------------------------------------------------
# Tests: _request() -- 401 Retry
# ---------------------------------------------------------------------------


class TestRequest401Retry:
    """Tests for _request() retry on 401 Unauthorized.

    The 401 retry calls authenticate() directly (not _ensure_authenticated).
    We patch authenticate() to a no-op to isolate the retry logic from
    the real auth HTTP call.
    """

    async def test_retries_once_on_401(self, mock_credential: Credential) -> None:
        """401 should trigger authenticate() and retry once."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        resp_401 = _make_response(401)
        resp_200 = _make_response(200, json_data={"ok": True})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.side_effect = [resp_401, resp_200]
        adapter._client = mock_client

        # Patch authenticate to a no-op (avoid real auth HTTP call)
        with patch.object(adapter, "authenticate", new_callable=AsyncMock):
            result = await adapter._request("GET", "https://api.example.com/test")

        assert result == {"ok": True}
        assert mock_client.request.call_count == 2

    async def test_does_not_retry_401_twice(
        self, mock_credential: Credential
    ) -> None:
        """Second 401 should raise AuthError (only one retry allowed)."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        resp_401 = _make_response(401)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = resp_401
        adapter._client = mock_client

        # Patch authenticate to a no-op
        with patch.object(adapter, "authenticate", new_callable=AsyncMock):
            with pytest.raises(AuthError) as exc_info:
                await adapter._request("GET", "https://api.example.com/test")

        assert "token refresh" in str(exc_info.value)
        assert mock_client.request.call_count == 2


# ---------------------------------------------------------------------------
# Tests: _request() -- 429 Retry with Backoff
# ---------------------------------------------------------------------------


class TestRequest429Retry:
    """Tests for _request() retry on 429 Too Many Requests."""

    @patch("admedi.adapters.levelplay.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_429_and_succeeds(
        self, mock_sleep: AsyncMock, mock_credential: Credential
    ) -> None:
        """429 should retry with backoff and eventually succeed."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        resp_429 = _make_response(429)
        resp_200 = _make_response(200, json_data={"ok": True})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.side_effect = [resp_429, resp_200]
        adapter._client = mock_client

        result = await adapter._request("GET", "https://api.example.com/test")
        assert result == {"ok": True}
        mock_sleep.assert_awaited_once()

    @patch("admedi.adapters.levelplay.asyncio.sleep", new_callable=AsyncMock)
    async def test_raises_after_max_429_retries(
        self, mock_sleep: AsyncMock, mock_credential: Credential
    ) -> None:
        """Exceeding max 429 retries should raise RateLimitError."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        resp_429 = _make_response(429)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = resp_429
        adapter._client = mock_client

        with pytest.raises(RateLimitError, match="Rate limited after 3 retries"):
            await adapter._request("GET", "https://api.example.com/test")

        # 1 initial + 3 retries = 4 total calls
        assert mock_client.request.call_count == 4
        assert mock_sleep.await_count == 3

    @patch("admedi.adapters.levelplay.asyncio.sleep", new_callable=AsyncMock)
    async def test_uses_retry_after_header(
        self, mock_sleep: AsyncMock, mock_credential: Credential
    ) -> None:
        """429 with Retry-After header should use that value as delay."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        resp_429 = _make_response(
            429, headers={"Retry-After": "30"}
        )
        resp_200 = _make_response(200, json_data={"ok": True})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.side_effect = [resp_429, resp_200]
        adapter._client = mock_client

        result = await adapter._request("GET", "https://api.example.com/test")
        assert result == {"ok": True}

        # Verify the sleep delay was 30 (from Retry-After header)
        mock_sleep.assert_awaited_once_with(30.0)

    @patch("admedi.adapters.levelplay.asyncio.sleep", new_callable=AsyncMock)
    async def test_backoff_increases_with_attempts(
        self, mock_sleep: AsyncMock, mock_credential: Credential
    ) -> None:
        """Backoff delays should increase with each retry attempt."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        resp_429 = _make_response(429)
        resp_200 = _make_response(200, json_data={"ok": True})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.side_effect = [resp_429, resp_429, resp_200]
        adapter._client = mock_client

        await adapter._request("GET", "https://api.example.com/test")

        # Two sleep calls with increasing delays
        assert mock_sleep.await_count == 2
        delay1 = mock_sleep.await_args_list[0][0][0]
        delay2 = mock_sleep.await_args_list[1][0][0]
        # delay1 should be ~2-3s (base=2, 2^0=1, *2 + jitter)
        # delay2 should be ~4-5s (base=2, 2^1=2, *2 + jitter)
        assert 2.0 <= delay1 <= 3.1  # 2*1 + [0,1)
        assert 4.0 <= delay2 <= 5.1  # 2*2 + [0,1)


# ---------------------------------------------------------------------------
# Tests: _request() -- 5xx Retry
# ---------------------------------------------------------------------------


class TestRequest5xxRetry:
    """Tests for _request() retry on 5xx server errors."""

    @patch("admedi.adapters.levelplay.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_500_and_succeeds(
        self, mock_sleep: AsyncMock, mock_credential: Credential
    ) -> None:
        """500 should retry and eventually succeed."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        resp_500 = _make_response(500)
        resp_200 = _make_response(200, json_data={"ok": True})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.side_effect = [resp_500, resp_200]
        adapter._client = mock_client

        result = await adapter._request("GET", "https://api.example.com/test")
        assert result == {"ok": True}
        mock_sleep.assert_awaited_once()

    @patch("admedi.adapters.levelplay.asyncio.sleep", new_callable=AsyncMock)
    async def test_raises_after_max_5xx_retries(
        self, mock_sleep: AsyncMock, mock_credential: Credential
    ) -> None:
        """Exceeding max 5xx retries should raise ApiError."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        resp_503 = _make_response(503)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = resp_503
        adapter._client = mock_client

        with pytest.raises(ApiError, match="Server error after 2 retries") as exc_info:
            await adapter._request("GET", "https://api.example.com/test")

        assert exc_info.value.status_code == 503
        # 1 initial + 2 retries = 3 total calls
        assert mock_client.request.call_count == 3
        assert mock_sleep.await_count == 2

    @patch("admedi.adapters.levelplay.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_502(
        self, mock_sleep: AsyncMock, mock_credential: Credential
    ) -> None:
        """502 should also trigger retry logic."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        resp_502 = _make_response(502)
        resp_200 = _make_response(200, json_data={"ok": True})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.side_effect = [resp_502, resp_200]
        adapter._client = mock_client

        result = await adapter._request("GET", "https://api.example.com/test")
        assert result == {"ok": True}


# ---------------------------------------------------------------------------
# Tests: _request() -- 4xx Errors
# ---------------------------------------------------------------------------


class TestRequest4xxErrors:
    """Tests for _request() immediate error on non-429 4xx responses."""

    async def test_400_raises_api_error(self, mock_credential: Credential) -> None:
        """400 Bad Request should immediately raise ApiError."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        resp_400 = _make_response(400, json_data={"error": "invalid appKey"})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = resp_400
        adapter._client = mock_client

        with pytest.raises(ApiError) as exc_info:
            await adapter._request("GET", "https://api.example.com/test")

        assert exc_info.value.status_code == 400
        assert exc_info.value.response_body == {"error": "invalid appKey"}

    async def test_403_raises_api_error(self, mock_credential: Credential) -> None:
        """403 Forbidden should immediately raise ApiError."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        resp_403 = _make_response(403)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = resp_403
        adapter._client = mock_client

        with pytest.raises(ApiError) as exc_info:
            await adapter._request("GET", "https://api.example.com/test")

        assert exc_info.value.status_code == 403

    async def test_404_raises_api_error(self, mock_credential: Credential) -> None:
        """404 Not Found should immediately raise ApiError."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        resp_404 = _make_response(404)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = resp_404
        adapter._client = mock_client

        with pytest.raises(ApiError) as exc_info:
            await adapter._request("GET", "https://api.example.com/test")

        assert exc_info.value.status_code == 404

    async def test_4xx_without_json_body(self, mock_credential: Credential) -> None:
        """4xx with non-JSON body should still raise ApiError (response_body=None)."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        response = MagicMock(spec=httpx.Response)
        response.status_code = 422
        response.headers = {"content-type": "text/plain"}
        response.json.side_effect = ValueError("Not JSON")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = response
        adapter._client = mock_client

        with pytest.raises(ApiError) as exc_info:
            await adapter._request("GET", "https://api.example.com/test")

        assert exc_info.value.status_code == 422
        assert exc_info.value.response_body is None


# ---------------------------------------------------------------------------
# Tests: _request() -- Params and JSON body forwarding
# ---------------------------------------------------------------------------


class TestRequestParamsForwarding:
    """Tests for _request() parameter forwarding to httpx."""

    async def test_forwards_params(self, mock_credential: Credential) -> None:
        """Query params should be forwarded to httpx.request."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = _make_response(
            200, json_data={"ok": True}
        )
        adapter._client = mock_client

        await adapter._request(
            "GET",
            "https://api.example.com/test",
            params={"appKey": "abc123"},
        )

        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["params"] == {"appKey": "abc123"}

    async def test_forwards_json_body(self, mock_credential: Credential) -> None:
        """JSON body should be forwarded to httpx.request."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = _make_response(
            200, json_data={"ok": True}
        )
        adapter._client = mock_client

        await adapter._request(
            "POST",
            "https://api.example.com/test",
            json_body={"name": "Test Group"},
        )

        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["json"] == {"name": "Test Group"}


# ---------------------------------------------------------------------------
# Tests: Abstract Method Stubs (remaining stubs -- authenticate is now real)
# ---------------------------------------------------------------------------


class TestAbstractMethodStubs:
    """Tests that unimplemented abstract methods raise AdapterNotSupportedError.

    Note: authenticate(), list_apps(), get_groups(), get_instances(),
    create_group(), update_group(), and delete_group() are real
    implementations, not stubs, so they are not tested here. The 5
    stubs tested here are deferred write/read operations that will be
    implemented in future tasks.
    """

    async def test_create_instances_raises(self, mock_credential: Credential) -> None:
        """create_instances() should raise AdapterNotSupportedError."""
        adapter = LevelPlayAdapter(mock_credential)
        with pytest.raises(AdapterNotSupportedError, match="create_instances"):
            await adapter.create_instances("test_key", [])

    async def test_update_instances_raises(self, mock_credential: Credential) -> None:
        """update_instances() should raise AdapterNotSupportedError."""
        adapter = LevelPlayAdapter(mock_credential)
        with pytest.raises(AdapterNotSupportedError, match="update_instances"):
            await adapter.update_instances("test_key", [])

    async def test_delete_instance_raises(self, mock_credential: Credential) -> None:
        """delete_instance() should raise AdapterNotSupportedError."""
        adapter = LevelPlayAdapter(mock_credential)
        with pytest.raises(AdapterNotSupportedError, match="delete_instance"):
            await adapter.delete_instance("test_key", 456)

    async def test_get_placements_raises(self, mock_credential: Credential) -> None:
        """get_placements() should raise AdapterNotSupportedError."""
        adapter = LevelPlayAdapter(mock_credential)
        with pytest.raises(AdapterNotSupportedError, match="get_placements"):
            await adapter.get_placements("test_key")

    async def test_get_reporting_raises(self, mock_credential: Credential) -> None:
        """get_reporting() should raise AdapterNotSupportedError."""
        adapter = LevelPlayAdapter(mock_credential)
        with pytest.raises(AdapterNotSupportedError, match="get_reporting"):
            await adapter.get_reporting(
                "test_key", "2025-01-01", "2025-01-31", ["revenue", "impressions"]
            )

    async def test_get_reporting_with_breakdowns_raises(
        self, mock_credential: Credential
    ) -> None:
        """get_reporting() with breakdowns should also raise AdapterNotSupportedError."""
        adapter = LevelPlayAdapter(mock_credential)
        with pytest.raises(AdapterNotSupportedError, match="get_reporting"):
            await adapter.get_reporting(
                "test_key",
                "2025-01-01",
                "2025-01-31",
                ["revenue"],
                breakdowns=["country", "network"],
            )

    async def test_stub_error_messages_are_descriptive(
        self, mock_credential: Credential
    ) -> None:
        """Each stub should include the method name and a deferral reason."""
        adapter = LevelPlayAdapter(mock_credential)

        # Test a sample of stubs for descriptive messages
        with pytest.raises(AdapterNotSupportedError) as exc_info:
            await adapter.delete_instance("test_key", 1)
        assert "delete_instance()" in str(exc_info.value)
        assert "not yet implemented" in str(exc_info.value)

        with pytest.raises(AdapterNotSupportedError) as exc_info:
            await adapter.get_placements("test_key")
        assert "get_placements()" in str(exc_info.value)
        assert "not yet implemented" in str(exc_info.value)

    def test_is_mediation_adapter_subclass(self) -> None:
        """LevelPlayAdapter should be a subclass of MediationAdapter."""
        from admedi.adapters.mediation import MediationAdapter

        assert issubclass(LevelPlayAdapter, MediationAdapter)


# ---------------------------------------------------------------------------
# Tests: RATE_LIMITS constant
# ---------------------------------------------------------------------------


class TestRateLimitsConstant:
    """Tests for the RATE_LIMITS constant dict."""

    def test_has_three_entries(self) -> None:
        """RATE_LIMITS should have exactly 3 entries."""
        assert len(RATE_LIMITS) == 3

    def test_groups_limit(self) -> None:
        """Groups should have 4000 requests per 1800 seconds."""
        assert RATE_LIMITS["groups"] == (4000, 1800)

    def test_instances_limit(self) -> None:
        """Instances should have 8000 requests per 1800 seconds."""
        assert RATE_LIMITS["instances"] == (8000, 1800)

    def test_reporting_limit(self) -> None:
        """Reporting should have 8000 requests per 3600 seconds."""
        assert RATE_LIMITS["reporting"] == (8000, 3600)


# ===========================================================================
# Step 2: OAuth Authentication and Token Management
# ===========================================================================


# ---------------------------------------------------------------------------
# Tests: authenticate()
# ---------------------------------------------------------------------------


class TestAuthenticate:
    """Tests for authenticate() -- token acquisition from auth endpoint."""

    async def test_token_acquired_from_auth_endpoint(
        self, mock_credential: Credential
    ) -> None:
        """authenticate() should acquire a token from the auth endpoint."""
        adapter = LevelPlayAdapter(mock_credential)
        token = _make_jwt_token(exp_offset_seconds=3600)
        auth_resp = _make_auth_response(token)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = auth_resp
        adapter._client = mock_client

        await adapter.authenticate()

        assert adapter._bearer_token == token
        assert adapter._token_expiry is not None
        assert adapter._token_expiry.tzinfo is not None  # Timezone-aware

    async def test_sends_correct_headers(
        self, mock_credential: Credential
    ) -> None:
        """authenticate() should send secretkey and refreshToken headers."""
        adapter = LevelPlayAdapter(mock_credential)
        token = _make_jwt_token()
        auth_resp = _make_auth_response(token)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = auth_resp
        adapter._client = mock_client

        await adapter.authenticate()

        call_args = mock_client.request.call_args
        assert call_args[0][0] == "GET"
        assert call_args[0][1] == AUTH_URL
        headers = call_args.kwargs.get("headers", {})
        assert headers["secretkey"] == mock_credential.secret_key
        assert headers["refreshToken"] == mock_credential.refresh_token

    async def test_strips_surrounding_quotes(
        self, mock_credential: Credential
    ) -> None:
        """authenticate() should strip surrounding quotes from JWT response."""
        adapter = LevelPlayAdapter(mock_credential)
        token = _make_jwt_token()

        # Auth endpoint returns token wrapped in quotes
        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        response.text = f'  "{token}"  '  # Extra whitespace + quotes
        response.headers = {"content-type": "text/plain"}
        response.json.side_effect = ValueError("Not JSON")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = response
        adapter._client = mock_client

        await adapter.authenticate()

        assert adapter._bearer_token == token  # No quotes or whitespace

    async def test_exp_in_seconds(self, mock_credential: Credential) -> None:
        """JWT with exp in seconds should be used as-is."""
        adapter = LevelPlayAdapter(mock_credential)
        exp_time = int(time.time()) + 3600  # 1 hour from now in seconds
        payload = {"sub": "test", "iat": int(time.time()), "exp": exp_time}
        token = jwt.encode(payload, _TEST_JWT_KEY, algorithm="HS256")
        auth_resp = _make_auth_response(token)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = auth_resp
        adapter._client = mock_client

        await adapter.authenticate()

        expected_expiry = datetime.fromtimestamp(exp_time, tz=UTC)
        assert adapter._token_expiry == expected_expiry

    async def test_exp_in_milliseconds(self, mock_credential: Credential) -> None:
        """JWT with exp > 1e12 should be treated as milliseconds."""
        adapter = LevelPlayAdapter(mock_credential)
        exp_seconds = int(time.time()) + 3600
        exp_ms = exp_seconds * 1000  # Milliseconds
        payload = {"sub": "test", "iat": int(time.time()), "exp": exp_ms}
        token = jwt.encode(payload, _TEST_JWT_KEY, algorithm="HS256")
        auth_resp = _make_auth_response(token)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = auth_resp
        adapter._client = mock_client

        await adapter.authenticate()

        expected_expiry = datetime.fromtimestamp(exp_seconds, tz=UTC)
        assert adapter._token_expiry == expected_expiry

    async def test_auth_failure_non_200_raises_auth_error(
        self, mock_credential: Credential
    ) -> None:
        """Non-200 from auth endpoint should raise AuthError."""
        adapter = LevelPlayAdapter(mock_credential)

        response = MagicMock(spec=httpx.Response)
        response.status_code = 401
        response.text = "Unauthorized"
        response.headers = {"content-type": "text/plain"}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = response
        adapter._client = mock_client

        with pytest.raises(AuthError, match="auth endpoint returned 401"):
            await adapter.authenticate()

    async def test_auth_failure_empty_token_raises_auth_error(
        self, mock_credential: Credential
    ) -> None:
        """Empty token from auth endpoint should raise AuthError."""
        adapter = LevelPlayAdapter(mock_credential)

        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        response.text = '""'  # Empty quoted string
        response.headers = {"content-type": "text/plain"}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = response
        adapter._client = mock_client

        with pytest.raises(AuthError, match="empty token received"):
            await adapter.authenticate()

    async def test_auth_failure_invalid_jwt_raises_auth_error(
        self, mock_credential: Credential
    ) -> None:
        """Invalid JWT (not decodable) should raise AuthError."""
        adapter = LevelPlayAdapter(mock_credential)

        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        response.text = '"not-a-valid-jwt"'
        response.headers = {"content-type": "text/plain"}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = response
        adapter._client = mock_client

        with pytest.raises(AuthError, match="could not decode JWT"):
            await adapter.authenticate()

    async def test_auth_failure_missing_exp_raises_auth_error(
        self, mock_credential: Credential
    ) -> None:
        """JWT without exp claim should raise AuthError."""
        adapter = LevelPlayAdapter(mock_credential)
        payload = {"sub": "test", "iat": int(time.time())}  # No exp
        token = jwt.encode(payload, _TEST_JWT_KEY, algorithm="HS256")
        auth_resp = _make_auth_response(token)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = auth_resp
        adapter._client = mock_client

        with pytest.raises(AuthError, match="missing 'exp' claim"):
            await adapter.authenticate()

    async def test_connection_error_raises_auth_error(
        self, mock_credential: Credential
    ) -> None:
        """Connection failure to auth endpoint should raise AuthError."""
        adapter = LevelPlayAdapter(mock_credential)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.side_effect = httpx.ConnectError("Connection refused")
        adapter._client = mock_client

        with pytest.raises(AuthError, match="Failed to connect"):
            await adapter.authenticate()


# ---------------------------------------------------------------------------
# Tests: _ensure_authenticated()
# ---------------------------------------------------------------------------


class TestEnsureAuthenticated:
    """Tests for _ensure_authenticated() -- token caching and refresh logic."""

    async def test_calls_authenticate_when_no_token(
        self, mock_credential: Credential
    ) -> None:
        """Should call authenticate() when _bearer_token is None."""
        adapter = LevelPlayAdapter(mock_credential)
        assert adapter._bearer_token is None

        with patch.object(adapter, "authenticate", new_callable=AsyncMock) as mock_auth:
            await adapter._ensure_authenticated()
            mock_auth.assert_awaited_once()

    async def test_skips_auth_when_token_valid(
        self, mock_credential: Credential
    ) -> None:
        """Should not call authenticate() when token is valid and far from expiry."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)  # Token expires in 1 hour

        with patch.object(adapter, "authenticate", new_callable=AsyncMock) as mock_auth:
            await adapter._ensure_authenticated()
            mock_auth.assert_not_awaited()

    async def test_refreshes_when_expiry_within_5_minutes(
        self, mock_credential: Credential
    ) -> None:
        """Should call authenticate() when token expires within 5 minutes."""
        adapter = LevelPlayAdapter(mock_credential)
        adapter._bearer_token = "expiring-token"
        adapter._token_expiry = datetime.now(UTC) + timedelta(minutes=4, seconds=59)

        with patch.object(adapter, "authenticate", new_callable=AsyncMock) as mock_auth:
            await adapter._ensure_authenticated()
            mock_auth.assert_awaited_once()

    async def test_skips_auth_when_expiry_exactly_5_minutes(
        self, mock_credential: Credential
    ) -> None:
        """Should NOT call authenticate() when token expires in exactly 5 minutes."""
        adapter = LevelPlayAdapter(mock_credential)
        adapter._bearer_token = "valid-token"
        # Add a small buffer to ensure we're at or beyond 5 minutes
        adapter._token_expiry = datetime.now(UTC) + timedelta(minutes=5, seconds=1)

        with patch.object(adapter, "authenticate", new_callable=AsyncMock) as mock_auth:
            await adapter._ensure_authenticated()
            mock_auth.assert_not_awaited()

    async def test_caching_second_call_skips_auth(
        self, mock_credential: Credential
    ) -> None:
        """Two consecutive calls should only authenticate once (token cached)."""
        adapter = LevelPlayAdapter(mock_credential)
        token = _make_jwt_token(exp_offset_seconds=3600)
        auth_resp = _make_auth_response(token)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = auth_resp
        adapter._client = mock_client

        # First call: should authenticate
        await adapter._ensure_authenticated()
        assert adapter._bearer_token == token
        assert mock_client.request.call_count == 1

        # Second call: should use cached token
        await adapter._ensure_authenticated()
        assert mock_client.request.call_count == 1  # No additional auth call


# ---------------------------------------------------------------------------
# Tests: Credential Fingerprinting
# ---------------------------------------------------------------------------


class TestCredentialFingerprinting:
    """Tests for credential change detection via fingerprinting."""

    def test_fingerprint_is_sha256(self, mock_credential: Credential) -> None:
        """Fingerprint should be SHA-256 of secret_key + refresh_token."""
        adapter = LevelPlayAdapter(mock_credential)
        expected = hashlib.sha256(
            (mock_credential.secret_key + mock_credential.refresh_token).encode()
        ).hexdigest()
        assert adapter._credential_fingerprint == expected

    def test_same_credentials_same_fingerprint(self) -> None:
        """Identical credentials should produce the same fingerprint."""
        cred1 = Credential(
            mediator=Mediator.LEVELPLAY,
            secret_key="key1",
            refresh_token="token1",
        )
        cred2 = Credential(
            mediator=Mediator.LEVELPLAY,
            secret_key="key1",
            refresh_token="token1",
        )
        assert (
            LevelPlayAdapter._compute_credential_fingerprint(cred1)
            == LevelPlayAdapter._compute_credential_fingerprint(cred2)
        )

    def test_different_credentials_different_fingerprint(self) -> None:
        """Different credentials should produce different fingerprints."""
        cred1 = Credential(
            mediator=Mediator.LEVELPLAY,
            secret_key="key1",
            refresh_token="token1",
        )
        cred2 = Credential(
            mediator=Mediator.LEVELPLAY,
            secret_key="key2",
            refresh_token="token1",
        )
        assert (
            LevelPlayAdapter._compute_credential_fingerprint(cred1)
            != LevelPlayAdapter._compute_credential_fingerprint(cred2)
        )

    async def test_credential_change_invalidates_token(
        self, mock_credential: Credential
    ) -> None:
        """Changing _credential should invalidate cached token on next _ensure_authenticated."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        # Change the credential
        new_cred = Credential(
            mediator=Mediator.LEVELPLAY,
            secret_key="new_secret_key_different",
            refresh_token="new_refresh_token_different",
        )
        adapter._credential = new_cred

        # _ensure_authenticated should detect the change and call authenticate
        with patch.object(adapter, "authenticate", new_callable=AsyncMock) as mock_auth:
            await adapter._ensure_authenticated()
            mock_auth.assert_awaited_once()

        # Token should have been cleared before authenticate was called
        # (authenticate mock doesn't set them, so they remain None)
        assert adapter._bearer_token is None
        assert adapter._token_expiry is None

    async def test_credential_change_updates_fingerprint(
        self, mock_credential: Credential
    ) -> None:
        """Credential change should update the stored fingerprint."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)
        old_fingerprint = adapter._credential_fingerprint

        new_cred = Credential(
            mediator=Mediator.LEVELPLAY,
            secret_key="changed_secret",
            refresh_token="changed_token",
        )
        adapter._credential = new_cred

        with patch.object(adapter, "authenticate", new_callable=AsyncMock):
            await adapter._ensure_authenticated()

        assert adapter._credential_fingerprint != old_fingerprint
        expected = LevelPlayAdapter._compute_credential_fingerprint(new_cred)
        assert adapter._credential_fingerprint == expected


# ---------------------------------------------------------------------------
# Tests: _request() auto-auth integration
# ---------------------------------------------------------------------------


class TestRequestAutoAuth:
    """Tests for _request() calling _ensure_authenticated automatically."""

    async def test_request_calls_ensure_authenticated(
        self, mock_credential: Credential
    ) -> None:
        """_request() should call _ensure_authenticated before HTTP call."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = _make_response(
            200, json_data={"ok": True}
        )
        adapter._client = mock_client

        with patch.object(
            adapter, "_ensure_authenticated", new_callable=AsyncMock
        ) as mock_ensure:
            await adapter._request("GET", "https://api.example.com/test")
            mock_ensure.assert_awaited()

    async def test_request_injects_bearer_token(
        self, mock_credential: Credential
    ) -> None:
        """_request() should inject Authorization header after _ensure_authenticated."""
        adapter = LevelPlayAdapter(mock_credential)
        token = _make_jwt_token(exp_offset_seconds=3600)
        auth_resp = _make_auth_response(token)
        api_resp = _make_response(200, json_data={"ok": True})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        # First call = auth, second call = API
        mock_client.request.side_effect = [auth_resp, api_resp]
        adapter._client = mock_client

        result = await adapter._request("GET", "https://api.example.com/test")

        assert result == {"ok": True}
        # Second call should have Authorization header
        api_call = mock_client.request.call_args_list[1]
        headers = api_call.kwargs.get("headers", {})
        assert headers.get("Authorization") == f"Bearer {token}"

    async def test_401_retry_calls_authenticate_directly(
        self, mock_credential: Credential
    ) -> None:
        """401 retry should call authenticate() (not _ensure_authenticated) to force fresh token."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        resp_401 = _make_response(401)
        resp_200 = _make_response(200, json_data={"ok": True})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.side_effect = [resp_401, resp_200]
        adapter._client = mock_client

        with patch.object(adapter, "authenticate", new_callable=AsyncMock) as mock_auth:
            result = await adapter._request("GET", "https://api.example.com/test")

        assert result == {"ok": True}
        # authenticate() should have been called once for the 401 retry
        mock_auth.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: Token Masking in Logs
# ---------------------------------------------------------------------------


class TestTokenMaskingInLogs:
    """Tests verifying tokens never appear in log output."""

    async def test_authenticate_logs_masked_token(
        self, mock_credential: Credential, caplog: pytest.LogCaptureFixture
    ) -> None:
        """authenticate() log messages should use masked token, not raw."""
        adapter = LevelPlayAdapter(mock_credential)
        token = _make_jwt_token(exp_offset_seconds=3600)
        auth_resp = _make_auth_response(token)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = auth_resp
        adapter._client = mock_client

        with caplog.at_level("DEBUG", logger="admedi.adapters.levelplay"):
            await adapter.authenticate()

        # Full token should NOT appear in any log message
        assert token not in caplog.text

        # Masked token should appear
        masked = adapter._mask_token(token)
        assert masked in caplog.text

    async def test_auth_error_does_not_contain_raw_token(
        self, mock_credential: Credential
    ) -> None:
        """AuthError raised on auth failure should not contain raw credential values."""
        adapter = LevelPlayAdapter(mock_credential)

        response = MagicMock(spec=httpx.Response)
        response.status_code = 403
        response.text = "Forbidden"
        response.headers = {"content-type": "text/plain"}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = response
        adapter._client = mock_client

        with pytest.raises(AuthError) as exc_info:
            await adapter.authenticate()

        error_msg = str(exc_info.value)
        assert mock_credential.secret_key not in error_msg
        assert mock_credential.refresh_token not in error_msg

    async def test_ensure_authenticated_logs_masked(
        self, mock_credential: Credential, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_ensure_authenticated log messages should not contain raw tokens."""
        adapter = LevelPlayAdapter(mock_credential)
        token = _make_jwt_token(exp_offset_seconds=3600)
        auth_resp = _make_auth_response(token)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.return_value = auth_resp
        adapter._client = mock_client

        with caplog.at_level("DEBUG", logger="admedi.adapters.levelplay"):
            await adapter._ensure_authenticated()

        # Full token should NOT appear
        assert token not in caplog.text

    async def test_401_retry_logs_do_not_contain_token(
        self, mock_credential: Credential, caplog: pytest.LogCaptureFixture
    ) -> None:
        """401 retry log messages should not contain the raw bearer token."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)
        original_token = adapter._bearer_token

        resp_401 = _make_response(401)
        resp_200 = _make_response(200, json_data={"ok": True})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request.side_effect = [resp_401, resp_200]
        adapter._client = mock_client

        with caplog.at_level("DEBUG", logger="admedi.adapters.levelplay"):
            with patch.object(adapter, "authenticate", new_callable=AsyncMock):
                await adapter._request("GET", "https://api.example.com/test")

        assert original_token not in caplog.text


# ---------------------------------------------------------------------------
# Tests: load_credential_from_env (Step 3)
# ---------------------------------------------------------------------------


class TestLoadCredentialFromEnv:
    """Tests for load_credential_from_env() module-level function."""

    def test_returns_credential_when_both_vars_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return a valid Credential when both env vars are present."""
        monkeypatch.setenv("LEVELPLAY_SECRET_KEY", "my_secret_key_123")
        monkeypatch.setenv("LEVELPLAY_REFRESH_TOKEN", "my_refresh_token_456")

        with patch("admedi.adapters.levelplay.dotenv.load_dotenv"):
            cred = load_credential_from_env()

        assert isinstance(cred, Credential)
        assert cred.mediator == Mediator.LEVELPLAY
        assert cred.secret_key == "my_secret_key_123"
        assert cred.refresh_token == "my_refresh_token_456"

    def test_secret_key_matches_env_value_exactly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returned secret_key should match the env var value exactly."""
        expected = "exact-secret-key-value-!@#$%"
        monkeypatch.setenv("LEVELPLAY_SECRET_KEY", expected)
        monkeypatch.setenv("LEVELPLAY_REFRESH_TOKEN", "some_token")

        with patch("admedi.adapters.levelplay.dotenv.load_dotenv"):
            cred = load_credential_from_env()

        assert cred.secret_key == expected

    def test_refresh_token_matches_env_value_exactly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returned refresh_token should match the env var value exactly."""
        expected = "exact-refresh-token-value-!@#$%"
        monkeypatch.setenv("LEVELPLAY_SECRET_KEY", "some_key")
        monkeypatch.setenv("LEVELPLAY_REFRESH_TOKEN", expected)

        with patch("admedi.adapters.levelplay.dotenv.load_dotenv"):
            cred = load_credential_from_env()

        assert cred.refresh_token == expected

    def test_mediator_is_levelplay(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returned Credential should always have mediator=Mediator.LEVELPLAY."""
        monkeypatch.setenv("LEVELPLAY_SECRET_KEY", "key")
        monkeypatch.setenv("LEVELPLAY_REFRESH_TOKEN", "token")

        with patch("admedi.adapters.levelplay.dotenv.load_dotenv"):
            cred = load_credential_from_env()

        assert cred.mediator == Mediator.LEVELPLAY

    def test_missing_secret_key_raises_auth_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise AuthError when LEVELPLAY_SECRET_KEY is missing."""
        monkeypatch.delenv("LEVELPLAY_SECRET_KEY", raising=False)
        monkeypatch.setenv("LEVELPLAY_REFRESH_TOKEN", "some_token")

        with pytest.raises(AuthError) as exc_info:
            with patch("admedi.adapters.levelplay.dotenv.load_dotenv"):
                load_credential_from_env()

        assert "LEVELPLAY_SECRET_KEY" in str(exc_info.value)

    def test_missing_refresh_token_raises_auth_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise AuthError when LEVELPLAY_REFRESH_TOKEN is missing."""
        monkeypatch.setenv("LEVELPLAY_SECRET_KEY", "some_key")
        monkeypatch.delenv("LEVELPLAY_REFRESH_TOKEN", raising=False)

        with pytest.raises(AuthError) as exc_info:
            with patch("admedi.adapters.levelplay.dotenv.load_dotenv"):
                load_credential_from_env()

        assert "LEVELPLAY_REFRESH_TOKEN" in str(exc_info.value)

    def test_both_missing_raises_auth_error_naming_both(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise AuthError naming both vars when both are missing."""
        monkeypatch.delenv("LEVELPLAY_SECRET_KEY", raising=False)
        monkeypatch.delenv("LEVELPLAY_REFRESH_TOKEN", raising=False)

        with pytest.raises(AuthError) as exc_info:
            with patch("admedi.adapters.levelplay.dotenv.load_dotenv"):
                load_credential_from_env()

        error_msg = str(exc_info.value)
        assert "LEVELPLAY_SECRET_KEY" in error_msg
        assert "LEVELPLAY_REFRESH_TOKEN" in error_msg

    def test_empty_secret_key_raises_auth_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise AuthError when LEVELPLAY_SECRET_KEY is empty string."""
        monkeypatch.setenv("LEVELPLAY_SECRET_KEY", "")
        monkeypatch.setenv("LEVELPLAY_REFRESH_TOKEN", "some_token")

        with pytest.raises(AuthError) as exc_info:
            with patch("admedi.adapters.levelplay.dotenv.load_dotenv"):
                load_credential_from_env()

        assert "LEVELPLAY_SECRET_KEY" in str(exc_info.value)

    def test_empty_refresh_token_raises_auth_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise AuthError when LEVELPLAY_REFRESH_TOKEN is empty string."""
        monkeypatch.setenv("LEVELPLAY_SECRET_KEY", "some_key")
        monkeypatch.setenv("LEVELPLAY_REFRESH_TOKEN", "")

        with pytest.raises(AuthError) as exc_info:
            with patch("admedi.adapters.levelplay.dotenv.load_dotenv"):
                load_credential_from_env()

        assert "LEVELPLAY_REFRESH_TOKEN" in str(exc_info.value)

    def test_both_empty_raises_auth_error_naming_both(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise AuthError naming both vars when both are empty strings."""
        monkeypatch.setenv("LEVELPLAY_SECRET_KEY", "")
        monkeypatch.setenv("LEVELPLAY_REFRESH_TOKEN", "")

        with pytest.raises(AuthError) as exc_info:
            with patch("admedi.adapters.levelplay.dotenv.load_dotenv"):
                load_credential_from_env()

        error_msg = str(exc_info.value)
        assert "LEVELPLAY_SECRET_KEY" in error_msg
        assert "LEVELPLAY_REFRESH_TOKEN" in error_msg

    def test_token_expiry_is_none_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returned Credential should have token_expiry=None (not set from env)."""
        monkeypatch.setenv("LEVELPLAY_SECRET_KEY", "key")
        monkeypatch.setenv("LEVELPLAY_REFRESH_TOKEN", "token")

        with patch("admedi.adapters.levelplay.dotenv.load_dotenv"):
            cred = load_credential_from_env()

        assert cred.token_expiry is None


# ---------------------------------------------------------------------------
# Tests: list_apps() -- Step 4
# ---------------------------------------------------------------------------


class TestListApps:
    """Tests for LevelPlayAdapter.list_apps() -- Step 4.

    All tests mock the HTTP client to return fixture data, exercising
    the list_apps() parsing and validation logic without network calls.
    """

    @pytest.fixture
    def apps_fixture_data(self) -> list[dict]:
        """Load the levelplay_apps.json fixture file."""
        fixture_path = _FIXTURES_DIR / "levelplay_apps.json"
        with open(fixture_path) as f:
            return json.load(f)

    @pytest.fixture
    def adapter_with_mock_client(
        self, mock_credential: Credential
    ) -> LevelPlayAdapter:
        """Return a pre-authenticated adapter with a mocked httpx client."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)
        return adapter

    async def test_fixture_file_exists(self) -> None:
        """Fixture JSON file should exist at tests/fixtures/levelplay_apps.json."""
        fixture_path = _FIXTURES_DIR / "levelplay_apps.json"
        assert fixture_path.exists(), f"Fixture file not found: {fixture_path}"

    async def test_fixture_has_multiple_apps(
        self, apps_fixture_data: list[dict]
    ) -> None:
        """Fixture should contain at least 2 apps with different platforms."""
        assert len(apps_fixture_data) >= 2
        platforms = {item["platform"] for item in apps_fixture_data}
        assert len(platforms) >= 2, "Fixture should have apps on different platforms"

    async def test_fixture_has_ad_units(
        self, apps_fixture_data: list[dict]
    ) -> None:
        """At least one fixture app should have adUnits nested data."""
        apps_with_ad_units = [
            item for item in apps_fixture_data if item.get("adUnits")
        ]
        assert len(apps_with_ad_units) >= 1

    async def test_returns_app_models_from_fixture(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        apps_fixture_data: list[dict],
    ) -> None:
        """list_apps() should return App models from fixture data."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=apps_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        apps = await adapter.list_apps()

        assert len(apps) == 3
        assert all(isinstance(app, App) for app in apps)

    async def test_correct_app_key(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        apps_fixture_data: list[dict],
    ) -> None:
        """Returned apps should have correct app_key values from fixture."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=apps_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        apps = await adapter.list_apps()

        assert apps[0].app_key == "1a2b3c4d5"
        assert apps[1].app_key == "5e6f7g8h9"
        assert apps[2].app_key == "9i0j1k2l3"

    async def test_correct_app_name(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        apps_fixture_data: list[dict],
    ) -> None:
        """Returned apps should have correct app_name values from fixture."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=apps_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        apps = await adapter.list_apps()

        assert apps[0].app_name == "Shelf Sort"
        assert apps[1].app_name == "Shelf Sort iOS"
        assert apps[2].app_name == "Shelf Sort Amazon"

    async def test_correct_platform(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        apps_fixture_data: list[dict],
    ) -> None:
        """Returned apps should have correct platform values from fixture."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=apps_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        apps = await adapter.list_apps()

        assert apps[0].platform == Platform.ANDROID
        assert apps[1].platform == Platform.IOS
        assert apps[2].platform == Platform.AMAZON

    async def test_correct_bundle_id(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        apps_fixture_data: list[dict],
    ) -> None:
        """Returned apps should have correct bundle_id values from fixture."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=apps_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        apps = await adapter.list_apps()

        assert apps[0].bundle_id == "com.mochibits.shelfsort"
        assert apps[1].bundle_id == "com.mochibits.shelfsort"
        assert apps[2].bundle_id == "com.mochibits.shelfsort.amazon"

    async def test_mediator_defaults_to_levelplay(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        apps_fixture_data: list[dict],
    ) -> None:
        """Each returned App should have mediator=Mediator.LEVELPLAY (default)."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=apps_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        apps = await adapter.list_apps()

        for app in apps:
            assert app.mediator == Mediator.LEVELPLAY

    async def test_ad_units_parsed(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        apps_fixture_data: list[dict],
    ) -> None:
        """Apps with adUnits should have ad_units dict populated."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=apps_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        apps = await adapter.list_apps()

        # First app has banner, interstitial, rewardedVideo ad units
        assert apps[0].ad_units is not None
        assert "banner" in apps[0].ad_units
        assert "interstitial" in apps[0].ad_units
        assert "rewardedVideo" in apps[0].ad_units
        assert apps[0].ad_units["banner"]["active"] is True

    async def test_optional_fields_populated(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        apps_fixture_data: list[dict],
    ) -> None:
        """Optional fields like coppa, taxonomy, ccpa should be populated when present."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=apps_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        apps = await adapter.list_apps()

        # First app has all optional fields
        assert apps[0].coppa is False
        assert apps[0].taxonomy == "games_puzzle"
        assert apps[0].ccpa == "do_not_sell"
        assert apps[0].network_reporting_api is True
        assert apps[0].creation_date == "2025-06-15"
        assert apps[0].icon == "https://cdn.example.com/icons/shelfsort.png"

    async def test_optional_fields_default_when_absent(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        apps_fixture_data: list[dict],
    ) -> None:
        """Optional fields should use defaults when not present in API response."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=apps_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        apps = await adapter.list_apps()

        # Third app (Amazon) has minimal fields
        assert apps[2].ccpa is None
        assert apps[2].network_reporting_api is None
        assert apps[2].taxonomy is None
        assert apps[2].icon is None

    async def test_empty_response_returns_empty_list(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """Empty API response ([]) should return empty list."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=[])
        adapter._client.request = AsyncMock(return_value=response)

        apps = await adapter.list_apps()

        assert apps == []
        assert isinstance(apps, list)

    async def test_unknown_platform_skipped_with_warning(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """App with unknown platform (e.g., 'WebGL') should be skipped with warning."""
        adapter = adapter_with_mock_client
        data = [
            {
                "appKey": "valid_key",
                "appName": "Valid App",
                "platform": "Android",
                "bundleId": "com.example.valid",
            },
            {
                "appKey": "webgl_key",
                "appName": "WebGL App",
                "platform": "WebGL",
                "bundleId": "com.example.webgl",
            },
        ]
        response = _make_response(json_data=data)
        adapter._client.request = AsyncMock(return_value=response)

        with caplog.at_level(logging.WARNING, logger="admedi.adapters.levelplay"):
            apps = await adapter.list_apps()

        # Valid app returned, WebGL app skipped
        assert len(apps) == 1
        assert apps[0].app_key == "valid_key"

        # Warning logged for skipped app
        assert any("WebGL App" in msg for msg in caplog.messages)
        assert any("webgl_key" in msg for msg in caplog.messages)

    async def test_multiple_unknown_platforms_all_skipped(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Multiple apps with unknown platforms should all be skipped."""
        adapter = adapter_with_mock_client
        data = [
            {
                "appKey": "webgl_key",
                "appName": "WebGL App",
                "platform": "WebGL",
                "bundleId": "com.example.webgl",
            },
            {
                "appKey": "ps5_key",
                "appName": "PS5 App",
                "platform": "PlayStation5",
                "bundleId": "com.example.ps5",
            },
        ]
        response = _make_response(json_data=data)
        adapter._client.request = AsyncMock(return_value=response)

        with caplog.at_level(logging.WARNING, logger="admedi.adapters.levelplay"):
            apps = await adapter.list_apps()

        assert len(apps) == 0
        # Both should have warnings
        warning_messages = " ".join(caplog.messages)
        assert "WebGL App" in warning_messages
        assert "PS5 App" in warning_messages

    async def test_mixed_valid_and_invalid_apps(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """Valid apps should be returned even when some apps have invalid platforms."""
        adapter = adapter_with_mock_client
        data = [
            {
                "appKey": "android_key",
                "appName": "Android App",
                "platform": "Android",
                "bundleId": "com.example.android",
            },
            {
                "appKey": "bad_key",
                "appName": "Bad Platform",
                "platform": "SteamDeck",
                "bundleId": "com.example.bad",
            },
            {
                "appKey": "ios_key",
                "appName": "iOS App",
                "platform": "iOS",
                "bundleId": "com.example.ios",
            },
        ]
        response = _make_response(json_data=data)
        adapter._client.request = AsyncMock(return_value=response)

        apps = await adapter.list_apps()

        assert len(apps) == 2
        assert apps[0].app_key == "android_key"
        assert apps[1].app_key == "ios_key"

    async def test_calls_request_with_correct_url_and_endpoint_key(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """list_apps() should call _request with APPS_URL and endpoint_key='apps'."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=[])
        adapter._client.request = AsyncMock(return_value=response)

        await adapter.list_apps()

        # Verify the HTTP call was made to the correct URL
        call_args = adapter._client.request.call_args
        assert call_args[0][0] == "GET"  # method
        assert call_args[0][1] == APPS_URL  # url

    async def test_app_status_defaults_to_active(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """App without appStatus field should default to 'active'."""
        adapter = adapter_with_mock_client
        data = [
            {
                "appKey": "no_status",
                "appName": "No Status App",
                "platform": "Android",
                "bundleId": "com.example.nostatus",
            },
        ]
        response = _make_response(json_data=data)
        adapter._client.request = AsyncMock(return_value=response)

        apps = await adapter.list_apps()

        assert len(apps) == 1
        assert apps[0].app_status == "active"


# ===========================================================================
# Step 5: get_groups() Endpoint
# ===========================================================================


# ---------------------------------------------------------------------------
# Tests: get_groups() -- Step 5
# ---------------------------------------------------------------------------


class TestGetGroups:
    """Tests for LevelPlayAdapter.get_groups() -- Step 5.

    All tests mock the HTTP client to return fixture data, exercising
    the get_groups() parsing, validation, and A/B test detection logic
    without network calls.
    """

    @pytest.fixture
    def groups_fixture_data(self) -> list[dict]:
        """Load the levelplay_groups.json fixture file."""
        fixture_path = _FIXTURES_DIR / "levelplay_groups.json"
        with open(fixture_path) as f:
            return json.load(f)

    @pytest.fixture
    def adapter_with_mock_client(
        self, mock_credential: Credential
    ) -> LevelPlayAdapter:
        """Return a pre-authenticated adapter with a mocked httpx client."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)
        return adapter

    # -- Fixture validation tests --

    async def test_fixture_file_exists(self) -> None:
        """Fixture JSON file should exist at tests/fixtures/levelplay_groups.json."""
        fixture_path = _FIXTURES_DIR / "levelplay_groups.json"
        assert fixture_path.exists(), f"Fixture file not found: {fixture_path}"

    async def test_fixture_has_at_least_three_groups(
        self, groups_fixture_data: list[dict]
    ) -> None:
        """Fixture should contain at least 3 groups with different ad formats."""
        assert len(groups_fixture_data) >= 3
        ad_formats = {item["adFormat"] for item in groups_fixture_data}
        assert len(ad_formats) >= 3, "Fixture should have groups with different ad formats"

    async def test_fixture_has_ab_test_group(
        self, groups_fixture_data: list[dict]
    ) -> None:
        """At least one fixture group should have abTest value other than N/A."""
        ab_test_groups = [
            item for item in groups_fixture_data
            if item.get("abTest") not in (None, "N/A")
        ]
        assert len(ab_test_groups) >= 1, "Fixture should have at least 1 group with active A/B test"

    async def test_fixture_has_instances_with_countries_rate(
        self, groups_fixture_data: list[dict]
    ) -> None:
        """At least one fixture group should have instances with countriesRate."""
        found = False
        for group in groups_fixture_data:
            for inst in group.get("instances", []):
                if inst.get("countriesRate"):
                    found = True
                    break
            if found:
                break
        assert found, "Fixture should have at least 1 instance with countriesRate data"

    # -- Core parsing tests --

    async def test_returns_group_models_from_fixture(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
    ) -> None:
        """get_groups() should return Group models from fixture data."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        assert len(groups) == 3
        assert all(isinstance(g, Group) for g in groups)

    async def test_correct_group_name(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
    ) -> None:
        """Returned groups should have correct group_name values."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        assert groups[0].group_name == "US Tier 1"
        assert groups[1].group_name == "APAC Tier 2"
        assert groups[2].group_name == "Global Rewarded"

    async def test_correct_group_id(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
    ) -> None:
        """Returned groups should have correct group_id values."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        assert groups[0].group_id == 12345
        assert groups[1].group_id == 12346
        assert groups[2].group_id == 12347

    async def test_correct_ad_format(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
    ) -> None:
        """Returned groups should have correct ad_format enum values."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        assert groups[0].ad_format == AdFormat.INTERSTITIAL
        assert groups[1].ad_format == AdFormat.BANNER
        assert groups[2].ad_format == AdFormat.REWARDED_VIDEO

    async def test_correct_countries(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
    ) -> None:
        """Returned groups should have correct countries lists."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        assert groups[0].countries == ["US"]
        assert groups[1].countries == ["JP", "KR", "TW"]
        assert groups[2].countries == []  # Empty countries list is valid

    async def test_correct_position(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
    ) -> None:
        """Returned groups should have correct position values."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        assert groups[0].position == 1
        assert groups[1].position == 2
        assert groups[2].position == 3

    async def test_correct_floor_price(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
    ) -> None:
        """Returned groups should have correct floor_price values (including null)."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        assert groups[0].floor_price == 15.0
        assert groups[1].floor_price == 5.0
        assert groups[2].floor_price is None

    async def test_correct_mediation_ad_unit_fields(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
    ) -> None:
        """Returned groups should have correct mediation ad unit fields."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        assert groups[0].mediation_ad_unit_id == "mau_001"
        assert groups[0].mediation_ad_unit_name == "Interstitial US"
        assert groups[2].mediation_ad_unit_id is None
        assert groups[2].mediation_ad_unit_name is None

    # -- Embedded Instance tests --

    async def test_embedded_instances_parsed(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
    ) -> None:
        """Groups with instances should have Instance model objects."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        # First group has 2 instances
        assert groups[0].instances is not None
        assert len(groups[0].instances) == 2
        assert all(isinstance(inst, Instance) for inst in groups[0].instances)

    async def test_instance_correct_fields(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
    ) -> None:
        """Embedded instances should have correct field values."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        inst = groups[0].instances[0]
        assert inst.instance_id == 101
        assert inst.instance_name == "ironSource Default"
        assert inst.network_name == "ironSource"
        assert inst.is_bidder is False
        assert inst.group_rate == 12.5

    async def test_instance_is_bidder_flag(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
    ) -> None:
        """Instance is_bidder should be correctly parsed."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        # First group: instance 0 is not a bidder, instance 1 is
        assert groups[0].instances[0].is_bidder is False
        assert groups[0].instances[1].is_bidder is True

    async def test_instance_countries_rate_parsed(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
    ) -> None:
        """Instance countriesRate should be parsed as list[CountryRate]."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        # First group, first instance has countriesRate
        inst = groups[0].instances[0]
        assert inst.countries_rate is not None
        assert len(inst.countries_rate) == 1
        assert isinstance(inst.countries_rate[0], CountryRate)
        assert inst.countries_rate[0].country_code == "US"
        assert inst.countries_rate[0].rate == 18.0

    async def test_instance_countries_rate_null(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
    ) -> None:
        """Instance with null countriesRate should parse as None."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        # First group, second instance (AdMob Bidding) has null countriesRate
        inst = groups[0].instances[1]
        assert inst.countries_rate is None

    async def test_instance_multiple_countries_rate(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
    ) -> None:
        """Instance with multiple countriesRate entries should all be parsed."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        # Third group, first instance (Liftoff Bidding RV) has 3 country rates
        inst = groups[2].instances[0]
        assert inst.countries_rate is not None
        assert len(inst.countries_rate) == 3
        codes = [cr.country_code for cr in inst.countries_rate]
        assert codes == ["US", "GB", "DE"]
        rates = [cr.rate for cr in inst.countries_rate]
        assert rates == [25.0, 20.0, 18.0]

    # -- A/B test detection tests --

    async def test_ab_test_detected_logs_warning(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        groups_fixture_data: list[dict],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Group with abTest value (not N/A, not null) should trigger warning."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=groups_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        with caplog.at_level(logging.WARNING, logger="admedi.adapters.levelplay"):
            groups = await adapter.get_groups("test_key")

        # APAC Tier 2 has abTest: "A"
        assert any("APAC Tier 2" in msg for msg in caplog.messages)
        assert any("A/B test detected" in msg for msg in caplog.messages)

    async def test_ab_test_na_no_warning(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Group with abTest 'N/A' should not trigger A/B test warning."""
        adapter = adapter_with_mock_client
        data = [
            {
                "groupId": 1,
                "groupName": "No AB Test",
                "adFormat": "banner",
                "countries": ["US"],
                "position": 1,
                "abTest": "N/A",
            },
        ]
        response = _make_response(json_data=data)
        adapter._client.request = AsyncMock(return_value=response)

        with caplog.at_level(logging.WARNING, logger="admedi.adapters.levelplay"):
            await adapter.get_groups("test_key")

        assert not any("A/B test detected" in msg for msg in caplog.messages)

    async def test_ab_test_null_no_warning(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Group with abTest null should not trigger A/B test warning."""
        adapter = adapter_with_mock_client
        data = [
            {
                "groupId": 2,
                "groupName": "Null AB Test",
                "adFormat": "interstitial",
                "countries": ["GB"],
                "position": 1,
                "abTest": None,
            },
        ]
        response = _make_response(json_data=data)
        adapter._client.request = AsyncMock(return_value=response)

        with caplog.at_level(logging.WARNING, logger="admedi.adapters.levelplay"):
            await adapter.get_groups("test_key")

        assert not any("A/B test detected" in msg for msg in caplog.messages)

    # -- Edge case tests --

    async def test_empty_response_returns_empty_list(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """Empty API response ([]) should return empty list."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=[])
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        assert groups == []
        assert isinstance(groups, list)

    async def test_unknown_ad_format_skipped_with_warning(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Group with unknown adFormat (e.g., 'offerWall') should be skipped."""
        adapter = adapter_with_mock_client
        data = [
            {
                "groupId": 100,
                "groupName": "Valid Banner Group",
                "adFormat": "banner",
                "countries": ["US"],
                "position": 1,
            },
            {
                "groupId": 200,
                "groupName": "OfferWall Group",
                "adFormat": "offerWall",
                "countries": ["US"],
                "position": 2,
            },
        ]
        response = _make_response(json_data=data)
        adapter._client.request = AsyncMock(return_value=response)

        with caplog.at_level(logging.WARNING, logger="admedi.adapters.levelplay"):
            groups = await adapter.get_groups("test_key")

        # Valid group returned, offerWall group skipped
        assert len(groups) == 1
        assert groups[0].group_name == "Valid Banner Group"

        # Warning logged for skipped group
        assert any("OfferWall Group" in msg for msg in caplog.messages)
        assert any("offerWall" in msg for msg in caplog.messages)

    async def test_mixed_valid_and_invalid_groups(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """Valid groups should be returned even when some groups have invalid adFormat."""
        adapter = adapter_with_mock_client
        data = [
            {
                "groupId": 1,
                "groupName": "Banner OK",
                "adFormat": "banner",
                "countries": ["US"],
                "position": 1,
            },
            {
                "groupId": 2,
                "groupName": "Bad Format",
                "adFormat": "popunder",
                "countries": ["US"],
                "position": 2,
            },
            {
                "groupId": 3,
                "groupName": "Rewarded OK",
                "adFormat": "rewardedVideo",
                "countries": ["GB"],
                "position": 3,
            },
        ]
        response = _make_response(json_data=data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        assert len(groups) == 2
        assert groups[0].group_name == "Banner OK"
        assert groups[1].group_name == "Rewarded OK"

    async def test_group_with_empty_countries_is_valid(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """Group with empty countries list should be parsed successfully."""
        adapter = adapter_with_mock_client
        data = [
            {
                "groupId": 999,
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": [],
                "position": 1,
            },
        ]
        response = _make_response(json_data=data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        assert len(groups) == 1
        assert groups[0].countries == []

    async def test_group_with_no_instances(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """Group without instances field should have instances as None."""
        adapter = adapter_with_mock_client
        data = [
            {
                "groupId": 1,
                "groupName": "No Instances",
                "adFormat": "banner",
                "countries": ["US"],
                "position": 1,
            },
        ]
        response = _make_response(json_data=data)
        adapter._client.request = AsyncMock(return_value=response)

        groups = await adapter.get_groups("test_key")

        assert len(groups) == 1
        assert groups[0].instances is None

    # -- URL and endpoint_key tests --

    async def test_calls_request_with_correct_url_and_endpoint_key(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """get_groups() should call _request with GROUPS_V4_URL/{app_key} and endpoint_key='groups'."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=[])
        adapter._client.request = AsyncMock(return_value=response)

        await adapter.get_groups("my_app_key_123")

        # Verify the HTTP call was made to the correct URL
        call_args = adapter._client.request.call_args
        assert call_args[0][0] == "GET"  # method
        assert call_args[0][1] == f"{GROUPS_V4_URL}/my_app_key_123"  # url


# ===========================================================================
# Step 6: get_instances() Endpoint
# ===========================================================================


# ---------------------------------------------------------------------------
# Tests: _normalize_instance_response()
# ---------------------------------------------------------------------------


class TestNormalizeInstanceResponse:
    """Tests for LevelPlayAdapter._normalize_instance_response() -- Step 6.

    Verifies that alternate field names from the Instances API are
    defensively remapped to canonical model aliases before validation.
    """

    def test_providerName_maps_to_networkName(
        self, mock_credential: Credential
    ) -> None:
        """providerName should be remapped to networkName."""
        adapter = LevelPlayAdapter(mock_credential)
        raw = {"providerName": "Meta", "name": "Test", "id": 1}
        result = adapter._normalize_instance_response(raw)
        assert result["networkName"] == "Meta"
        assert "providerName" not in result

    def test_instanceName_maps_to_name(
        self, mock_credential: Credential
    ) -> None:
        """instanceName should be remapped to name."""
        adapter = LevelPlayAdapter(mock_credential)
        raw = {"instanceName": "Meta Audience Network", "networkName": "Meta", "id": 1}
        result = adapter._normalize_instance_response(raw)
        assert result["name"] == "Meta Audience Network"
        assert "instanceName" not in result

    def test_instanceId_maps_to_id(
        self, mock_credential: Credential
    ) -> None:
        """instanceId should be remapped to id."""
        adapter = LevelPlayAdapter(mock_credential)
        raw = {"instanceId": 99, "name": "Test", "networkName": "Meta"}
        result = adapter._normalize_instance_response(raw)
        assert result["id"] == 99
        assert "instanceId" not in result

    def test_globalPricing_maps_to_groupRate(
        self, mock_credential: Credential
    ) -> None:
        """globalPricing should be remapped to groupRate."""
        adapter = LevelPlayAdapter(mock_credential)
        raw = {"globalPricing": 12.5, "name": "Test", "networkName": "Meta", "id": 1}
        result = adapter._normalize_instance_response(raw)
        assert result["groupRate"] == 12.5
        assert "globalPricing" not in result

    def test_countriesPricing_maps_to_countriesRate(
        self, mock_credential: Credential
    ) -> None:
        """countriesPricing should be remapped to countriesRate with sub-field normalization."""
        adapter = LevelPlayAdapter(mock_credential)
        raw = {
            "countriesPricing": [
                {"country": "US", "eCPM": 15.0},
                {"country": "GB", "eCPM": 12.0},
            ],
            "name": "Test",
            "networkName": "Meta",
            "id": 1,
        }
        result = adapter._normalize_instance_response(raw)
        assert "countriesRate" in result
        assert "countriesPricing" not in result
        assert len(result["countriesRate"]) == 2
        assert result["countriesRate"][0]["countryCode"] == "US"
        assert result["countriesRate"][0]["rate"] == 15.0
        assert result["countriesRate"][1]["countryCode"] == "GB"
        assert result["countriesRate"][1]["rate"] == 12.0

    def test_countriesPricing_subfield_country_to_countryCode(
        self, mock_credential: Credential
    ) -> None:
        """Each countriesPricing entry's 'country' should map to 'countryCode'."""
        adapter = LevelPlayAdapter(mock_credential)
        raw = {
            "countriesPricing": [{"country": "JP", "eCPM": 10.0}],
            "name": "Test",
            "networkName": "Meta",
            "id": 1,
        }
        result = adapter._normalize_instance_response(raw)
        entry = result["countriesRate"][0]
        assert entry["countryCode"] == "JP"
        assert "country" not in entry

    def test_countriesPricing_subfield_eCPM_to_rate(
        self, mock_credential: Credential
    ) -> None:
        """Each countriesPricing entry's 'eCPM' should map to 'rate'."""
        adapter = LevelPlayAdapter(mock_credential)
        raw = {
            "countriesPricing": [{"country": "CA", "eCPM": 8.5}],
            "name": "Test",
            "networkName": "Meta",
            "id": 1,
        }
        result = adapter._normalize_instance_response(raw)
        entry = result["countriesRate"][0]
        assert entry["rate"] == 8.5
        assert "eCPM" not in entry

    def test_isLive_string_active_maps_to_true(
        self, mock_credential: Credential
    ) -> None:
        """isLive string 'active' should be normalized to True."""
        adapter = LevelPlayAdapter(mock_credential)
        raw = {"isLive": "active", "name": "Test", "networkName": "Meta", "id": 1}
        result = adapter._normalize_instance_response(raw)
        assert result["isLive"] is True

    def test_isLive_string_inactive_maps_to_false(
        self, mock_credential: Credential
    ) -> None:
        """isLive string 'inactive' should be normalized to False."""
        adapter = LevelPlayAdapter(mock_credential)
        raw = {"isLive": "inactive", "name": "Test", "networkName": "Meta", "id": 1}
        result = adapter._normalize_instance_response(raw)
        assert result["isLive"] is False

    def test_isLive_string_Active_case_insensitive(
        self, mock_credential: Credential
    ) -> None:
        """isLive normalization should be case-insensitive."""
        adapter = LevelPlayAdapter(mock_credential)
        raw = {"isLive": "Active", "name": "Test", "networkName": "Meta", "id": 1}
        result = adapter._normalize_instance_response(raw)
        assert result["isLive"] is True

    def test_isLive_bool_not_changed(
        self, mock_credential: Credential
    ) -> None:
        """isLive that is already a bool should not be changed."""
        adapter = LevelPlayAdapter(mock_credential)
        raw = {"isLive": True, "name": "Test", "networkName": "Meta", "id": 1}
        result = adapter._normalize_instance_response(raw)
        assert result["isLive"] is True

    def test_defensive_no_overwrite_existing_canonical_key(
        self, mock_credential: Credential
    ) -> None:
        """If both alternate and canonical keys exist, canonical is preserved."""
        adapter = LevelPlayAdapter(mock_credential)
        raw = {
            "providerName": "Alt Network",
            "networkName": "Canonical Network",
            "name": "Test",
            "id": 1,
        }
        result = adapter._normalize_instance_response(raw)
        assert result["networkName"] == "Canonical Network"
        # providerName should still be in the dict since it wasn't remapped
        assert result.get("providerName") == "Alt Network"

    def test_countriesPricing_null_preserves_null(
        self, mock_credential: Credential
    ) -> None:
        """countriesPricing that is null should be normalized to null countriesRate."""
        adapter = LevelPlayAdapter(mock_credential)
        raw = {
            "countriesPricing": None,
            "name": "Test",
            "networkName": "Meta",
            "id": 1,
        }
        result = adapter._normalize_instance_response(raw)
        assert result["countriesRate"] is None
        assert "countriesPricing" not in result

    def test_all_alternate_fields_at_once(
        self, mock_credential: Credential
    ) -> None:
        """All alternate field names should be normalized in a single pass."""
        adapter = LevelPlayAdapter(mock_credential)
        raw = {
            "providerName": "Meta",
            "instanceName": "Meta AN",
            "instanceId": 100,
            "globalPricing": 5.0,
            "countriesPricing": [{"country": "US", "eCPM": 10.0}],
            "isLive": "active",
        }
        result = adapter._normalize_instance_response(raw)
        assert result["networkName"] == "Meta"
        assert result["name"] == "Meta AN"
        assert result["id"] == 100
        assert result["groupRate"] == 5.0
        assert result["countriesRate"][0]["countryCode"] == "US"
        assert result["countriesRate"][0]["rate"] == 10.0
        assert result["isLive"] is True


# ---------------------------------------------------------------------------
# Tests: get_instances() -- Step 6
# ---------------------------------------------------------------------------


class TestGetInstances:
    """Tests for LevelPlayAdapter.get_instances() -- Step 6.

    All tests mock the HTTP client to return fixture data, exercising
    the get_instances() parsing, normalization, v3/v1 fallback, and
    validation logic without network calls.
    """

    @pytest.fixture
    def instances_fixture_data(self) -> list[dict]:
        """Load the levelplay_instances.json fixture file."""
        fixture_path = _FIXTURES_DIR / "levelplay_instances.json"
        with open(fixture_path) as f:
            return json.load(f)

    @pytest.fixture
    def adapter_with_mock_client(
        self, mock_credential: Credential
    ) -> LevelPlayAdapter:
        """Return a pre-authenticated adapter with a mocked httpx client."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)
        return adapter

    # -- Fixture validation tests --

    async def test_fixture_file_exists(self) -> None:
        """Fixture JSON file should exist at tests/fixtures/levelplay_instances.json."""
        fixture_path = _FIXTURES_DIR / "levelplay_instances.json"
        assert fixture_path.exists(), f"Fixture file not found: {fixture_path}"

    async def test_fixture_has_at_least_three_instances(
        self, instances_fixture_data: list[dict]
    ) -> None:
        """Fixture should contain at least 3 instances."""
        assert len(instances_fixture_data) >= 3

    async def test_fixture_has_mix_of_bidder_and_non_bidder(
        self, instances_fixture_data: list[dict]
    ) -> None:
        """Fixture should have a mix of bidder and non-bidder instances."""
        bidders = [i for i in instances_fixture_data if i.get("isBidder")]
        non_bidders = [i for i in instances_fixture_data if not i.get("isBidder")]
        assert len(bidders) >= 1, "Fixture should have at least 1 bidder"
        assert len(non_bidders) >= 1, "Fixture should have at least 1 non-bidder"

    async def test_fixture_has_alternate_field_names(
        self, instances_fixture_data: list[dict]
    ) -> None:
        """Fixture should have at least 1 instance with alternate field names."""
        alt_fields = {"providerName", "instanceName", "instanceId", "globalPricing", "countriesPricing"}
        found_alt = False
        for inst in instances_fixture_data:
            if alt_fields & set(inst.keys()):
                found_alt = True
                break
        assert found_alt, "Fixture should have at least 1 instance with alternate field names"

    # -- Core parsing tests (v3 response) --

    async def test_returns_instance_models_from_fixture(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
    ) -> None:
        """get_instances() should return Instance models from fixture data."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=instances_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        assert len(instances) == 4
        assert all(isinstance(inst, Instance) for inst in instances)

    async def test_correct_instance_name(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
    ) -> None:
        """Returned instances should have correct instance_name values."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=instances_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        assert instances[0].instance_name == "ironSource Default"
        assert instances[1].instance_name == "AdMob Bidding"
        # Alternate field name: instanceName -> name
        assert instances[2].instance_name == "Meta Audience Network"
        assert instances[3].instance_name == "Liftoff Monetize"

    async def test_correct_network_name(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
    ) -> None:
        """Returned instances should have correct network_name values."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=instances_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        assert instances[0].network_name == "ironSource"
        assert instances[1].network_name == "AdMob"
        # Alternate field name: providerName -> networkName
        assert instances[2].network_name == "Meta"
        assert instances[3].network_name == "Liftoff"

    async def test_correct_instance_id(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
    ) -> None:
        """Returned instances should have correct instance_id values."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=instances_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        assert instances[0].instance_id == 201
        assert instances[1].instance_id == 202
        # Alternate field name: instanceId -> id
        assert instances[2].instance_id == 203
        assert instances[3].instance_id == 204

    async def test_correct_is_bidder(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
    ) -> None:
        """Returned instances should have correct is_bidder values."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=instances_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        assert instances[0].is_bidder is False
        assert instances[1].is_bidder is True
        assert instances[2].is_bidder is True
        assert instances[3].is_bidder is False

    async def test_correct_group_rate(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
    ) -> None:
        """Returned instances should have correct group_rate values."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=instances_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        assert instances[0].group_rate == 10.5
        assert instances[1].group_rate == 8.0
        # Alternate field name: globalPricing -> groupRate
        assert instances[2].group_rate == 12.0
        assert instances[3].group_rate == 6.5

    async def test_correct_countries_rate_canonical(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
    ) -> None:
        """Instance with canonical countriesRate should have CountryRate objects."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=instances_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        # First instance: canonical countriesRate
        inst = instances[0]
        assert inst.countries_rate is not None
        assert len(inst.countries_rate) == 2
        assert isinstance(inst.countries_rate[0], CountryRate)
        assert inst.countries_rate[0].country_code == "US"
        assert inst.countries_rate[0].rate == 15.0
        assert inst.countries_rate[1].country_code == "GB"
        assert inst.countries_rate[1].rate == 12.0

    async def test_correct_countries_rate_normalized(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
    ) -> None:
        """Instance with countriesPricing (alternate) should be normalized to CountryRate."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=instances_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        # Third instance: alternate countriesPricing -> countriesRate
        inst = instances[2]
        assert inst.countries_rate is not None
        assert len(inst.countries_rate) == 3
        assert inst.countries_rate[0].country_code == "US"
        assert inst.countries_rate[0].rate == 20.0
        assert inst.countries_rate[1].country_code == "CA"
        assert inst.countries_rate[1].rate == 16.5
        assert inst.countries_rate[2].country_code == "JP"
        assert inst.countries_rate[2].rate == 14.0

    async def test_countries_rate_null(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
    ) -> None:
        """Instance with null countriesRate should parse as None."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=instances_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        # Second instance: countriesRate is null
        assert instances[1].countries_rate is None

    async def test_is_live_bool_preserved(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
    ) -> None:
        """Instance with bool isLive should be preserved."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=instances_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        assert instances[0].is_live is True
        assert instances[1].is_live is True

    async def test_is_live_string_active_normalized(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
    ) -> None:
        """Instance with isLive='active' should be normalized to True."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=instances_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        # Third instance: isLive was "active"
        assert instances[2].is_live is True

    async def test_is_live_string_inactive_normalized(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
    ) -> None:
        """Instance with isLive='inactive' should be normalized to False."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=instances_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        # Fourth instance: isLive was "inactive"
        assert instances[3].is_live is False

    async def test_ad_unit_parsed(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
    ) -> None:
        """Instance adUnit should be parsed as AdFormat enum."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=instances_fixture_data)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        assert instances[0].ad_unit == AdFormat.INTERSTITIAL
        assert instances[1].ad_unit == AdFormat.BANNER
        assert instances[2].ad_unit == AdFormat.REWARDED_VIDEO

    # -- v3/v1 fallback tests --

    async def test_v3_404_falls_back_to_v1(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When v3 returns 404, should fall back to v1 and return instances."""
        adapter = adapter_with_mock_client

        # First call (v3) raises ApiError 404, second call (v1) returns data
        v1_response = _make_response(json_data=instances_fixture_data)

        call_count = 0
        original_request = adapter._client.request

        async def side_effect(*args: Any, **kwargs: Any) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # v3 returns 404
                return _make_response(404)
            else:
                return v1_response

        adapter._client.request = AsyncMock(side_effect=side_effect)

        with caplog.at_level(logging.WARNING, logger="admedi.adapters.levelplay"):
            instances = await adapter.get_instances("test_key")

        assert len(instances) == 4
        assert any("falling back to v1" in msg for msg in caplog.messages)

    async def test_v1_fallback_uses_correct_url(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """v1 fallback should use INSTANCES_V1_URL."""
        adapter = adapter_with_mock_client

        v1_response = _make_response(json_data=[])

        call_count = 0

        async def side_effect(*args: Any, **kwargs: Any) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_response(404)
            else:
                return v1_response

        adapter._client.request = AsyncMock(side_effect=side_effect)

        await adapter.get_instances("test_key")

        # Second call should be to v1 URL
        second_call = adapter._client.request.call_args_list[1]
        assert second_call[0][1] == INSTANCES_V1_URL

    async def test_v3_410_falls_back_to_v1(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        instances_fixture_data: list[dict],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When v3 returns 410 Gone, should fall back to v1."""
        adapter = adapter_with_mock_client

        v1_response = _make_response(json_data=instances_fixture_data)

        call_count = 0

        async def side_effect(*args: Any, **kwargs: Any) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_response(410)
            else:
                return v1_response

        adapter._client.request = AsyncMock(side_effect=side_effect)

        with caplog.at_level(logging.WARNING, logger="admedi.adapters.levelplay"):
            instances = await adapter.get_instances("test_key")

        assert len(instances) == 4
        assert any("falling back to v1" in msg for msg in caplog.messages)

    async def test_both_v3_and_v1_410_returns_empty(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When both v3 and v1 return 410, should return empty list gracefully."""
        adapter = adapter_with_mock_client

        adapter._client.request = AsyncMock(return_value=_make_response(410))

        with caplog.at_level(logging.WARNING, logger="admedi.adapters.levelplay"):
            instances = await adapter.get_instances("test_key")

        assert instances == []
        assert any("unavailable" in msg for msg in caplog.messages)

    async def test_non_404_error_raises(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """Non-404/410 ApiError (e.g., 403) should be raised, not caught for fallback."""
        adapter = adapter_with_mock_client
        resp_403 = _make_response(403)

        adapter._client.request = AsyncMock(return_value=resp_403)

        with pytest.raises(ApiError) as exc_info:
            await adapter.get_instances("test_key")

        assert exc_info.value.status_code == 403

    # -- Object wrapper extraction tests --

    async def test_dict_with_instances_key_unwrapped(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """Response wrapped in a dict with 'instances' key should be unwrapped."""
        adapter = adapter_with_mock_client
        wrapped = {
            "instances": [
                {"id": 1, "name": "Test", "networkName": "ironSource", "isBidder": False},
            ]
        }
        response = _make_response(json_data=wrapped)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        assert len(instances) == 1
        assert instances[0].instance_name == "Test"

    async def test_dict_without_instances_key_returns_empty(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Dict without 'instances' key should log warning and return empty list."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data={"data": "unexpected"})
        adapter._client.request = AsyncMock(return_value=response)

        with caplog.at_level(logging.WARNING, logger="admedi.adapters.levelplay"):
            instances = await adapter.get_instances("test_key")

        assert instances == []
        assert any("expected a list or dict" in msg for msg in caplog.messages)

    # -- Edge case tests --

    async def test_empty_response_returns_empty_list(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """Empty API response ([]) should return empty list."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=[])
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        assert instances == []
        assert isinstance(instances, list)

    async def test_instance_missing_optional_fields_still_parses(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """Instance with only required fields should parse with defaults."""
        adapter = adapter_with_mock_client
        data = [
            {"name": "Minimal Instance", "networkName": "TestNetwork"},
        ]
        response = _make_response(json_data=data)
        adapter._client.request = AsyncMock(return_value=response)

        instances = await adapter.get_instances("test_key")

        assert len(instances) == 1
        inst = instances[0]
        assert inst.instance_name == "Minimal Instance"
        assert inst.network_name == "TestNetwork"
        assert inst.instance_id is None
        assert inst.is_bidder is False
        assert inst.group_rate is None
        assert inst.countries_rate is None
        assert inst.ad_unit is None
        assert inst.is_live is None
        assert inst.is_optimized is None

    async def test_calls_v3_with_correct_params(
        self,
        adapter_with_mock_client: LevelPlayAdapter,
    ) -> None:
        """get_instances() should call v3 URL with appKey query param."""
        adapter = adapter_with_mock_client
        response = _make_response(json_data=[])
        adapter._client.request = AsyncMock(return_value=response)

        await adapter.get_instances("my_app_key_123")

        call_args = adapter._client.request.call_args
        assert call_args[0][0] == "GET"
        assert call_args[0][1] == INSTANCES_V3_URL
        assert call_args.kwargs["params"] == {"appKey": "my_app_key_123"}


# ===========================================================================
# Step 7: Concurrency Safety and Unimplemented Stubs
# ===========================================================================


# ---------------------------------------------------------------------------
# Tests: Capabilities property
# ---------------------------------------------------------------------------


class TestCapabilities:
    """Tests for the capabilities property on LevelPlayAdapter."""

    def test_capabilities_returns_correct_set(
        self, mock_credential: Credential
    ) -> None:
        """capabilities should return exactly {AUTHENTICATE, LIST_APPS, READ_GROUPS, READ_INSTANCES, WRITE_GROUPS}."""
        adapter = LevelPlayAdapter(mock_credential)
        expected = {
            AdapterCapability.AUTHENTICATE,
            AdapterCapability.LIST_APPS,
            AdapterCapability.READ_GROUPS,
            AdapterCapability.READ_INSTANCES,
            AdapterCapability.WRITE_GROUPS,
        }
        assert adapter.capabilities == expected

    def test_capabilities_has_five_entries(
        self, mock_credential: Credential
    ) -> None:
        """capabilities should contain exactly 5 capabilities."""
        adapter = LevelPlayAdapter(mock_credential)
        assert len(adapter.capabilities) == 5

    def test_capabilities_contains_authenticate(
        self, mock_credential: Credential
    ) -> None:
        """capabilities should include AUTHENTICATE."""
        adapter = LevelPlayAdapter(mock_credential)
        assert AdapterCapability.AUTHENTICATE in adapter.capabilities

    def test_capabilities_contains_list_apps(
        self, mock_credential: Credential
    ) -> None:
        """capabilities should include LIST_APPS."""
        adapter = LevelPlayAdapter(mock_credential)
        assert AdapterCapability.LIST_APPS in adapter.capabilities

    def test_capabilities_contains_read_groups(
        self, mock_credential: Credential
    ) -> None:
        """capabilities should include READ_GROUPS."""
        adapter = LevelPlayAdapter(mock_credential)
        assert AdapterCapability.READ_GROUPS in adapter.capabilities

    def test_capabilities_contains_read_instances(
        self, mock_credential: Credential
    ) -> None:
        """capabilities should include READ_INSTANCES."""
        adapter = LevelPlayAdapter(mock_credential)
        assert AdapterCapability.READ_INSTANCES in adapter.capabilities

    def test_capabilities_contains_write_groups(
        self, mock_credential: Credential
    ) -> None:
        """capabilities should include WRITE_GROUPS."""
        adapter = LevelPlayAdapter(mock_credential)
        assert AdapterCapability.WRITE_GROUPS in adapter.capabilities

    def test_capabilities_does_not_contain_write_instances(
        self, mock_credential: Credential
    ) -> None:
        """capabilities should NOT include WRITE_INSTANCES (deferred)."""
        adapter = LevelPlayAdapter(mock_credential)
        assert AdapterCapability.WRITE_INSTANCES not in adapter.capabilities

    def test_capabilities_does_not_contain_read_placements(
        self, mock_credential: Credential
    ) -> None:
        """capabilities should NOT include READ_PLACEMENTS (deferred)."""
        adapter = LevelPlayAdapter(mock_credential)
        assert AdapterCapability.READ_PLACEMENTS not in adapter.capabilities

    def test_capabilities_does_not_contain_read_reporting(
        self, mock_credential: Credential
    ) -> None:
        """capabilities should NOT include READ_REPORTING (deferred)."""
        adapter = LevelPlayAdapter(mock_credential)
        assert AdapterCapability.READ_REPORTING not in adapter.capabilities


# ---------------------------------------------------------------------------
# Tests: ensure_capability()
# ---------------------------------------------------------------------------


class TestEnsureCapability:
    """Tests for ensure_capability() on LevelPlayAdapter."""

    def test_ensure_capability_passes_for_authenticate(
        self, mock_credential: Credential
    ) -> None:
        """ensure_capability(AUTHENTICATE) should not raise."""
        adapter = LevelPlayAdapter(mock_credential)
        adapter.ensure_capability(AdapterCapability.AUTHENTICATE)

    def test_ensure_capability_passes_for_list_apps(
        self, mock_credential: Credential
    ) -> None:
        """ensure_capability(LIST_APPS) should not raise."""
        adapter = LevelPlayAdapter(mock_credential)
        adapter.ensure_capability(AdapterCapability.LIST_APPS)

    def test_ensure_capability_passes_for_read_groups(
        self, mock_credential: Credential
    ) -> None:
        """ensure_capability(READ_GROUPS) should not raise."""
        adapter = LevelPlayAdapter(mock_credential)
        adapter.ensure_capability(AdapterCapability.READ_GROUPS)

    def test_ensure_capability_passes_for_read_instances(
        self, mock_credential: Credential
    ) -> None:
        """ensure_capability(READ_INSTANCES) should not raise."""
        adapter = LevelPlayAdapter(mock_credential)
        adapter.ensure_capability(AdapterCapability.READ_INSTANCES)

    def test_ensure_capability_passes_for_write_groups(
        self, mock_credential: Credential
    ) -> None:
        """ensure_capability(WRITE_GROUPS) should not raise."""
        adapter = LevelPlayAdapter(mock_credential)
        adapter.ensure_capability(AdapterCapability.WRITE_GROUPS)

    def test_ensure_capability_raises_for_write_instances(
        self, mock_credential: Credential
    ) -> None:
        """ensure_capability(WRITE_INSTANCES) should raise AdapterNotSupportedError."""
        adapter = LevelPlayAdapter(mock_credential)
        with pytest.raises(AdapterNotSupportedError, match="write_instances"):
            adapter.ensure_capability(AdapterCapability.WRITE_INSTANCES)

    def test_ensure_capability_raises_for_read_placements(
        self, mock_credential: Credential
    ) -> None:
        """ensure_capability(READ_PLACEMENTS) should raise AdapterNotSupportedError."""
        adapter = LevelPlayAdapter(mock_credential)
        with pytest.raises(AdapterNotSupportedError, match="read_placements"):
            adapter.ensure_capability(AdapterCapability.READ_PLACEMENTS)

    def test_ensure_capability_raises_for_read_reporting(
        self, mock_credential: Credential
    ) -> None:
        """ensure_capability(READ_REPORTING) should raise AdapterNotSupportedError."""
        adapter = LevelPlayAdapter(mock_credential)
        with pytest.raises(AdapterNotSupportedError, match="read_reporting"):
            adapter.ensure_capability(AdapterCapability.READ_REPORTING)


# ---------------------------------------------------------------------------
# Tests: Concurrent rate limit counter access
# ---------------------------------------------------------------------------


class TestConcurrentRateLimitAccess:
    """Tests that concurrent coroutines can safely access rate limit counters.

    Verifies asyncio.Lock prevents data corruption when multiple coroutines
    call _check_rate_limit() simultaneously on a shared adapter instance.
    """

    async def test_three_concurrent_coroutines_produce_three_timestamps(
        self, mock_credential: Credential
    ) -> None:
        """3 coroutines calling _check_rate_limit('groups') should produce exactly 3 timestamps."""
        adapter = LevelPlayAdapter(mock_credential)

        # Launch 3 concurrent rate limit checks
        await asyncio.gather(
            adapter._check_rate_limit("groups"),
            adapter._check_rate_limit("groups"),
            adapter._check_rate_limit("groups"),
        )

        assert len(adapter._rate_counters["groups"]) == 3

    async def test_ten_concurrent_coroutines_produce_ten_timestamps(
        self, mock_credential: Credential
    ) -> None:
        """10 coroutines should produce exactly 10 timestamps with no lost updates."""
        adapter = LevelPlayAdapter(mock_credential)

        await asyncio.gather(
            *[adapter._check_rate_limit("instances") for _ in range(10)]
        )

        assert len(adapter._rate_counters["instances"]) == 10

    async def test_concurrent_different_endpoints_no_cross_contamination(
        self, mock_credential: Credential
    ) -> None:
        """Concurrent checks on different endpoints should track independently."""
        adapter = LevelPlayAdapter(mock_credential)

        await asyncio.gather(
            adapter._check_rate_limit("groups"),
            adapter._check_rate_limit("groups"),
            adapter._check_rate_limit("instances"),
            adapter._check_rate_limit("instances"),
            adapter._check_rate_limit("instances"),
        )

        assert len(adapter._rate_counters["groups"]) == 2
        assert len(adapter._rate_counters["instances"]) == 3

    async def test_concurrent_rate_limit_timestamps_are_monotonic(
        self, mock_credential: Credential
    ) -> None:
        """Timestamps recorded by concurrent coroutines should be non-decreasing."""
        adapter = LevelPlayAdapter(mock_credential)

        await asyncio.gather(
            *[adapter._check_rate_limit("groups") for _ in range(5)]
        )

        timestamps = list(adapter._rate_counters["groups"])
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1]


# ---------------------------------------------------------------------------
# Tests: Semaphore limits concurrent requests
# ---------------------------------------------------------------------------


class TestSemaphoreConcurrency:
    """Tests that asyncio.Semaphore(10) limits concurrent HTTP requests.

    Uses a mock httpx client that tracks the number of in-flight requests
    to verify the semaphore enforces the concurrency cap.
    """

    async def test_semaphore_limits_concurrent_requests_to_10(
        self, mock_credential: Credential
    ) -> None:
        """Launching 15 concurrent _request() calls should have at most 10 in-flight at once."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        # Track concurrent in-flight requests
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def slow_request(*args: Any, **kwargs: Any) -> httpx.Response:
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent

            # Simulate a slow request
            await asyncio.sleep(0.05)

            async with lock:
                current_concurrent -= 1

            return _make_response(json_data={"ok": True})

        adapter._client.request = AsyncMock(side_effect=slow_request)

        # Launch 15 concurrent _request calls
        tasks = [
            adapter._request("GET", "https://example.com/test", endpoint_key="default")
            for _ in range(15)
        ]
        await asyncio.gather(*tasks)

        # Semaphore(10) should ensure at most 10 in-flight
        assert max_concurrent <= 10
        # With 15 requests and a semaphore of 10, we should have hit the cap
        assert max_concurrent == 10

    async def test_all_requests_complete_despite_semaphore(
        self, mock_credential: Credential
    ) -> None:
        """All 15 requests should complete even though only 10 can run concurrently."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        call_count = 0
        lock = asyncio.Lock()

        async def counting_request(*args: Any, **kwargs: Any) -> httpx.Response:
            nonlocal call_count
            await asyncio.sleep(0.01)
            async with lock:
                call_count += 1
            return _make_response(json_data={"ok": True})

        adapter._client.request = AsyncMock(side_effect=counting_request)

        tasks = [
            adapter._request("GET", "https://example.com/test", endpoint_key="default")
            for _ in range(15)
        ]
        results = await asyncio.gather(*tasks)

        assert call_count == 15
        assert len(results) == 15

    async def test_semaphore_releases_after_request_completes(
        self, mock_credential: Credential
    ) -> None:
        """After a request completes, the semaphore slot should be released for others."""
        adapter = LevelPlayAdapter(mock_credential)
        _pre_authenticate(adapter)

        response = _make_response(json_data={"ok": True})
        adapter._client.request = AsyncMock(return_value=response)

        # Run 10 requests to fill semaphore, then 5 more -- all should complete
        for _ in range(15):
            result = await adapter._request(
                "GET", "https://example.com/test", endpoint_key="default"
            )
            assert result == {"ok": True}

        assert adapter._client.request.call_count == 15
