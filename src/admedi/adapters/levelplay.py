"""LevelPlay mediation adapter for the ironSource/Unity LevelPlay platform.

Implements ``MediationAdapter`` with OAuth 2.0 authentication, persistent
HTTP client, rate limit tracking, and retry/backoff logic.

Example::

    from admedi.adapters.levelplay import LevelPlayAdapter
    from admedi.models import Credential, Mediator

    cred = Credential(mediator=Mediator.LEVELPLAY, secret_key="sk", refresh_token="rt")
    async with LevelPlayAdapter(cred) as adapter:
        await adapter.authenticate()
        apps = await adapter.list_apps()
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

import dotenv
import httpx
import jwt

from admedi import __version__
from admedi.adapters.mediation import AdapterCapability, MediationAdapter
from pydantic import ValidationError

from admedi.constants import APPS_URL, AUTH_URL, GROUPS_V4_URL, INSTANCES_V1_URL, INSTANCES_V3_URL
from admedi.exceptions import (
    AdapterNotSupportedError,
    ApiError,
    AuthError,
    RateLimitError,
)
from admedi.models.app import App
from admedi.models.credential import Credential
from admedi.models.enums import AdFormat, Mediator
from admedi.models.group import Group
from admedi.models.instance import Instance
from admedi.models.placement import Placement

logger = logging.getLogger(__name__)

# Rate limits per endpoint: (max_requests, window_seconds)
# Source: LevelPlay API documentation
RATE_LIMITS: dict[str, tuple[int, int]] = {
    "groups": (4000, 1800),       # 4000 requests per 30 minutes
    "instances": (8000, 1800),    # 8000 requests per 30 minutes
    "reporting": (8000, 3600),    # 8000 requests per hour
}

# Retry/backoff configuration
_BACKOFF_BASE: float = 2.0
_BACKOFF_MAX: float = 60.0
_MAX_429_RETRIES: int = 3
_MAX_5XX_RETRIES: int = 2
_RATE_LIMIT_WARNING_THRESHOLD: float = 0.9  # Warn at 90% of budget
_TOKEN_REFRESH_MARGIN: timedelta = timedelta(minutes=5)  # Refresh token when < 5 min remaining


def load_credential_from_env(dotenv_path: str | None = None) -> Credential:
    """Load LevelPlay credentials from environment variables.

    Reads ``LEVELPLAY_SECRET_KEY`` and ``LEVELPLAY_REFRESH_TOKEN`` from
    the environment (optionally loading a ``.env`` file first) and returns
    a ``Credential`` model with ``mediator=Mediator.LEVELPLAY``.

    Args:
        dotenv_path: Optional path to a ``.env`` file. When provided,
            ``dotenv.load_dotenv(dotenv_path)`` is called. When ``None``,
            ``dotenv.load_dotenv()`` uses default ``.env`` discovery.

    Returns:
        A ``Credential`` instance with the LevelPlay secret key and
        refresh token populated from environment variables.

    Raises:
        AuthError: If either ``LEVELPLAY_SECRET_KEY`` or
            ``LEVELPLAY_REFRESH_TOKEN`` is missing or empty.

    Example::

        from admedi.adapters.levelplay import load_credential_from_env

        cred = load_credential_from_env()
        # cred.mediator == Mediator.LEVELPLAY
        # cred.secret_key == os.environ["LEVELPLAY_SECRET_KEY"]
    """
    if dotenv_path is not None:
        dotenv.load_dotenv(dotenv_path)
    else:
        dotenv.load_dotenv()

    secret_key = os.environ.get("LEVELPLAY_SECRET_KEY", "")
    refresh_token = os.environ.get("LEVELPLAY_REFRESH_TOKEN", "")

    missing: list[str] = []
    if not secret_key:
        missing.append("LEVELPLAY_SECRET_KEY")
    if not refresh_token:
        missing.append("LEVELPLAY_REFRESH_TOKEN")

    if missing:
        raise AuthError(
            f"Missing required environment variable(s): {', '.join(missing)}"
        )

    logger.debug("Loaded LevelPlay credentials from environment")

    return Credential(
        mediator=Mediator.LEVELPLAY,
        secret_key=secret_key,
        refresh_token=refresh_token,
    )


class LevelPlayAdapter(MediationAdapter):
    """LevelPlay mediation platform adapter.

    Provides HTTP infrastructure with persistent connection pooling,
    per-endpoint rate limit tracking via sliding window deques, and a
    central ``_request()`` method with retry/backoff for 401, 429, and
    5xx responses.

    Args:
        credential: LevelPlay API credentials (secret key + refresh token).

    Example::

        cred = Credential(mediator=Mediator.LEVELPLAY, secret_key="sk", refresh_token="rt")
        async with LevelPlayAdapter(cred) as adapter:
            await adapter.authenticate()
            apps = await adapter.list_apps()
    """

    def __init__(self, credential: Credential) -> None:
        self._credential: Credential = credential
        self._bearer_token: str | None = None
        self._token_expiry: datetime | None = None

        # Credential fingerprint for detecting credential changes
        self._credential_fingerprint: str = self._compute_credential_fingerprint(
            credential
        )

        # Persistent HTTP client with connection pooling
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            timeout=60.0,
            headers={
                "Accept-Encoding": "gzip",
                "User-Agent": f"admedi/{__version__}",
            },
        )

        # Rate limit tracking: per-endpoint sliding window deque of timestamps
        self._rate_counters: dict[str, deque[float]] = {}
        self._rate_lock: asyncio.Lock = asyncio.Lock()

        # Concurrency limiter for HTTP requests (integrated in Step 7)
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(10)

    # -- Context manager support -----------------------------------------------

    async def close(self) -> None:
        """Close the persistent HTTP client and release resources."""
        await self._client.aclose()

    async def __aenter__(self) -> LevelPlayAdapter:
        """Enter async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit async context manager, closing the HTTP client."""
        await self.close()

    # -- Token masking ---------------------------------------------------------

    def _mask_token(self, token: str) -> str:
        """Mask a token for safe logging, showing first 8 and last 4 chars.

        Args:
            token: The raw token string to mask.

        Returns:
            Masked string in the format ``"abcdefgh...7890"``.

        Example::

            >>> adapter._mask_token("abcdefgh1234XYZ7890")
            'abcdefgh...7890'
        """
        if len(token) <= 12:
            return "***"
        return f"{token[:8]}...{token[-4:]}"

    # -- Rate limit tracking ---------------------------------------------------

    async def _check_rate_limit(self, endpoint_key: str) -> None:
        """Check and track rate limit for an endpoint.

        Uses a sliding window deque to track request timestamps per endpoint.
        If the endpoint has no configured limit (not in ``RATE_LIMITS``), the
        check passes immediately with no tracking.

        Logs a warning when usage reaches 90% of the budget. Raises
        ``RateLimitError`` when the budget is exhausted.

        Args:
            endpoint_key: Identifier for the API endpoint (e.g., "groups",
                "instances", "reporting").

        Raises:
            RateLimitError: When the rate limit budget is exhausted.
        """
        if endpoint_key not in RATE_LIMITS:
            return

        max_requests, window_seconds = RATE_LIMITS[endpoint_key]
        now = time.monotonic()

        async with self._rate_lock:
            # Initialize deque for this endpoint if needed
            if endpoint_key not in self._rate_counters:
                self._rate_counters[endpoint_key] = deque()

            counter = self._rate_counters[endpoint_key]

            # Prune timestamps outside the sliding window
            cutoff = now - window_seconds
            while counter and counter[0] < cutoff:
                counter.popleft()

            current_count = len(counter)

            # Check if budget is exhausted
            if current_count >= max_requests:
                retry_after = counter[0] - cutoff if counter else float(window_seconds)
                raise RateLimitError(
                    f"Rate limit exhausted for '{endpoint_key}': "
                    f"{current_count}/{max_requests} requests in {window_seconds}s window",
                    retry_after=retry_after,
                )

            # Warn at 90% threshold
            warning_threshold = int(max_requests * _RATE_LIMIT_WARNING_THRESHOLD)
            if current_count >= warning_threshold:
                logger.warning(
                    "Rate limit warning for '%s': %d/%d requests (%.0f%% of budget)",
                    endpoint_key,
                    current_count,
                    max_requests,
                    (current_count / max_requests) * 100,
                )

            # Record this request timestamp (counts attempts, not completions --
            # conservative: ensures we never exceed the server's limit even if
            # some requests fail before reaching the server)
            counter.append(now)

    # -- Credential fingerprinting --------------------------------------------

    @staticmethod
    def _compute_credential_fingerprint(credential: Credential) -> str:
        """Compute a SHA-256 hash of the credential's secret key and refresh token.

        Used to detect credential changes and invalidate cached tokens.

        Args:
            credential: The credential to fingerprint.

        Returns:
            Hex-encoded SHA-256 digest of the concatenated key and token.
        """
        return hashlib.sha256(
            (credential.secret_key + credential.refresh_token).encode()
        ).hexdigest()

    # -- Authentication --------------------------------------------------------

    async def _ensure_authenticated(self) -> None:
        """Ensure the adapter has a valid authentication token.

        Checks three conditions that trigger a fresh ``authenticate()`` call:

        1. No token cached (``_bearer_token is None``).
        2. Token expiry is within 5 minutes of now.
        3. The credential fingerprint has changed (credential was replaced).

        This method is called automatically by ``_request()`` before each
        HTTP call.
        """
        # Check for credential change
        current_fingerprint = self._compute_credential_fingerprint(self._credential)
        if current_fingerprint != self._credential_fingerprint:
            logger.info("Credential change detected, invalidating cached token")
            self._bearer_token = None
            self._token_expiry = None
            self._credential_fingerprint = current_fingerprint

        # Check if token exists and is not expiring soon
        if self._bearer_token is not None and self._token_expiry is not None:
            now = datetime.now(UTC)
            if (self._token_expiry - now) >= _TOKEN_REFRESH_MARGIN:
                return  # Token is still valid

        logger.info("Token missing or expiring soon, refreshing authentication")
        await self.authenticate()

    async def authenticate(self) -> None:
        """Authenticate with the LevelPlay platform via OAuth 2.0.

        Makes a ``GET`` request to the auth endpoint with ``secretkey``
        and ``refreshToken`` headers. The response is a raw JWT string
        (not JSON). The JWT's ``exp`` claim is decoded via PyJWT to
        determine token expiry.

        Handles millisecond timestamps: if ``exp > 1e12``, divides by 1000.

        Stores the token in ``_bearer_token`` and the expiry in
        ``_token_expiry`` (timezone-aware UTC).

        Raises:
            AuthError: If the auth endpoint returns a non-200 response
                or the JWT cannot be decoded.

        Example::

            async with LevelPlayAdapter(cred) as adapter:
                await adapter.authenticate()
                # adapter._bearer_token is now set
        """
        logger.info("Authenticating with LevelPlay OAuth endpoint")

        try:
            response = await self._client.request(
                "GET",
                AUTH_URL,
                headers={
                    "secretkey": self._credential.secret_key,
                    "refreshToken": self._credential.refresh_token,
                },
            )
        except Exception as exc:
            raise AuthError(
                "Failed to connect to LevelPlay auth endpoint"
            ) from exc

        if response.status_code != 200:
            raise AuthError(
                f"Authentication failed: auth endpoint returned {response.status_code}"
            )

        # Response is a raw JWT string, possibly surrounded by quotes
        raw_token = response.text.strip().strip('"')

        if not raw_token:
            raise AuthError("Authentication failed: empty token received")

        # Decode JWT to extract exp claim (no signature verification)
        try:
            payload = jwt.decode(
                raw_token,
                options={"verify_signature": False},
                algorithms=["HS256"],
            )
        except jwt.DecodeError as exc:
            raise AuthError(
                "Authentication failed: could not decode JWT"
            ) from exc

        exp_raw = payload.get("exp")
        if exp_raw is None:
            raise AuthError("Authentication failed: JWT missing 'exp' claim")

        # Handle millisecond timestamps (if exp > 1e12, divide by 1000)
        exp_seconds = float(exp_raw)
        if exp_seconds > 1e12:
            logger.debug(
                "JWT exp appears to be in milliseconds (%s), converting to seconds",
                exp_raw,
            )
            exp_seconds = exp_seconds / 1000

        self._bearer_token = raw_token
        self._token_expiry = datetime.fromtimestamp(exp_seconds, tz=UTC)

        logger.info(
            "Authentication successful, token=%s, expires=%s",
            self._mask_token(raw_token),
            self._token_expiry.isoformat(),
        )

    # -- Central HTTP request method -------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | list[Any] | None = None,
        endpoint_key: str = "default",
    ) -> dict[str, Any] | list[Any] | str:
        """Execute an HTTP request with rate limiting, auth, and retry logic.

        Handles:
        - Rate limit checking before each request
        - Auth header injection (when ``_bearer_token`` is set)
        - 401: refresh token + retry once
        - 429: exponential backoff with jitter, up to 3 retries
          (uses ``Retry-After`` header if present)
        - 5xx: retry up to 2 times
        - Other 4xx: immediate ``ApiError``

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            url: Full URL for the request.
            params: Optional query parameters.
            json_body: Optional JSON request body.
            endpoint_key: Rate limit endpoint key (e.g., "groups").

        Returns:
            Parsed JSON (dict or list) for JSON responses, raw text string
            for non-JSON responses.

        Raises:
            RateLimitError: When rate limit budget is exhausted.
            AuthError: When authentication fails after retry.
            ApiError: For non-retryable HTTP errors (4xx).
        """
        await self._check_rate_limit(endpoint_key)

        # Build headers
        headers: dict[str, str] = {}
        if self._bearer_token is not None:
            headers["Authorization"] = f"Bearer {self._bearer_token}"

        attempt_429 = 0
        attempt_5xx = 0
        has_retried_401 = False

        while True:
            # Re-check auth on each iteration: during 429/5xx retries with
            # long backoff sleeps, the token may approach expiry. Better to
            # refresh before retrying than to fail with a stale token.
            await self._ensure_authenticated()

            # Update auth header in case token was refreshed
            if self._bearer_token is not None:
                headers["Authorization"] = f"Bearer {self._bearer_token}"

            async with self._semaphore:
                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                )

            status = response.status_code

            # -- Success (2xx) --
            if 200 <= status < 300:
                # Handle empty body (e.g., POST 200 with content-length: 0,
                # or 204 No Content)
                body_text = response.text.strip()
                if not body_text or body_text == "null":
                    return {}

                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type:
                    return response.json()  # type: ignore[no-any-return]
                # Try JSON parsing even without content-type header
                try:
                    return response.json()  # type: ignore[no-any-return]
                except Exception:
                    return body_text

            # -- 401 Unauthorized: force fresh token and retry once --
            if status == 401 and not has_retried_401:
                has_retried_401 = True
                logger.info("Received 401, forcing token refresh")
                await self.authenticate()
                continue

            # -- 429 Too Many Requests: exponential backoff with jitter --
            if status == 429:
                attempt_429 += 1
                if attempt_429 > _MAX_429_RETRIES:
                    retry_after_header = response.headers.get("Retry-After")
                    raise RateLimitError(
                        f"Rate limited after {_MAX_429_RETRIES} retries: "
                        f"{method} {url} returned 429",
                        retry_after=float(retry_after_header) if retry_after_header else None,
                    )

                # Use Retry-After header if present, otherwise compute backoff
                retry_after_header = response.headers.get("Retry-After")
                if retry_after_header is not None:
                    delay = float(retry_after_header)
                else:
                    delay = min(
                        _BACKOFF_BASE * (2 ** (attempt_429 - 1)) + random.uniform(0, 1),
                        _BACKOFF_MAX,
                    )

                logger.warning(
                    "Rate limited (429), retrying in %.1fs (attempt %d/%d): %s %s",
                    delay,
                    attempt_429,
                    _MAX_429_RETRIES,
                    method,
                    url,
                )
                await asyncio.sleep(delay)
                continue

            # -- 5xx Server Error: retry with limit --
            if 500 <= status <= 599:
                attempt_5xx += 1
                if attempt_5xx > _MAX_5XX_RETRIES:
                    raise ApiError(
                        f"Server error after {_MAX_5XX_RETRIES} retries: "
                        f"{method} {url} returned {status}",
                        status_code=status,
                    )

                delay = min(
                    _BACKOFF_BASE * (2 ** (attempt_5xx - 1)) + random.uniform(0, 1),
                    _BACKOFF_MAX,
                )
                logger.warning(
                    "Server error (%d), retrying in %.1fs (attempt %d/%d): %s %s",
                    status,
                    delay,
                    attempt_5xx,
                    _MAX_5XX_RETRIES,
                    method,
                    url,
                )
                await asyncio.sleep(delay)
                continue

            # -- Persistent 401: credentials are broken --
            if status == 401:
                raise AuthError(
                    f"Authentication failed after token refresh: "
                    f"{method} {url} returned 401"
                )

            # -- Other 4xx: immediate error --
            if 400 <= status <= 499:
                # Try to extract response body for debugging
                try:
                    body = response.json()
                except Exception:
                    body = None

                raise ApiError(
                    f"Client error: {method} {url} returned {status}",
                    status_code=status,
                    response_body=body,
                )

            # -- Unexpected status code --
            raise ApiError(
                f"Unexpected status code: {method} {url} returned {status}",
                status_code=status,
            )

    # -- Capabilities ----------------------------------------------------------

    @property
    def capabilities(self) -> set[AdapterCapability]:
        """Return the set of capabilities this adapter supports.

        The LevelPlay adapter supports authentication, listing apps,
        reading groups and instances, and writing groups. Instance write
        operations and placements/reporting are deferred to future tasks.

        Returns:
            Set containing ``AUTHENTICATE``, ``LIST_APPS``,
            ``READ_GROUPS``, ``READ_INSTANCES``, and ``WRITE_GROUPS``.
        """
        return {
            AdapterCapability.AUTHENTICATE,
            AdapterCapability.LIST_APPS,
            AdapterCapability.READ_GROUPS,
            AdapterCapability.READ_INSTANCES,
            AdapterCapability.WRITE_GROUPS,
        }

    # -- Abstract method stubs (raise AdapterNotSupportedError) ----------------

    async def list_apps(self) -> list[App]:
        """List all apps registered on LevelPlay.

        Fetches the full application list from the LevelPlay Applications
        API v6 and returns normalized ``App`` models. Apps with unrecognized
        ``platform`` values are skipped with a warning log.

        Returns:
            List of ``App`` models, one per registered application.

        Raises:
            ApiError: If the API returns a non-retryable HTTP error.
            RateLimitError: If the apps endpoint rate budget is exhausted.

        Example::

            async with LevelPlayAdapter(cred) as adapter:
                apps = await adapter.list_apps()
                for app in apps:
                    print(f"{app.app_name} ({app.platform})")
        """
        response = await self._request("GET", APPS_URL, endpoint_key="apps")

        if not response:
            return []

        # Response is a JSON array of app objects
        if not isinstance(response, list):
            logger.warning(
                "list_apps() expected a list response, got %s",
                type(response).__name__,
            )
            return []

        apps: list[App] = []
        for item in response:
            try:
                app = App.model_validate(item)
                apps.append(app)
            except ValidationError:
                # Unknown platform or other validation failure -- skip
                app_name = item.get("appName", item.get("appKey", "unknown"))
                app_key = item.get("appKey", "unknown")
                logger.warning(
                    "Skipping app '%s' (key=%s): validation failed "
                    "(likely unknown platform '%s')",
                    app_name,
                    app_key,
                    item.get("platform", "N/A"),
                )

        logger.debug("list_apps() returned %d apps", len(apps))
        return apps

    async def get_groups(self, app_key: str) -> list[Group]:
        """Get all mediation groups for an app from the Groups API v4.

        Fetches the mediation groups for a specific app, returning
        normalized ``Group`` models with embedded ``Instance`` data.
        Detects active A/B tests and logs a warning (does not raise).

        Groups with unrecognized ``adFormat`` values are skipped with
        a warning log.

        Args:
            app_key: The LevelPlay app key to fetch groups for.

        Returns:
            List of ``Group`` models, one per mediation group.

        Raises:
            ApiError: If the API returns a non-retryable HTTP error.
            RateLimitError: If the groups endpoint rate budget is exhausted.

        Example::

            async with LevelPlayAdapter(cred) as adapter:
                groups = await adapter.get_groups("1a2b3c4d5")
                for group in groups:
                    print(f"{group.group_name} ({group.ad_format})")
        """
        response = await self._request(
            "GET", f"{GROUPS_V4_URL}/{app_key}", endpoint_key="groups"
        )

        if not response:
            return []

        # Response is a JSON array of group objects
        if not isinstance(response, list):
            logger.warning(
                "get_groups() expected a list response, got %s",
                type(response).__name__,
            )
            return []

        groups: list[Group] = []
        for item in response:
            try:
                group = Group.model_validate(item)
                groups.append(group)
            except ValidationError:
                # Unknown adFormat or other validation failure -- skip
                group_name = item.get("groupName", item.get("groupId", "unknown"))
                group_id = item.get("groupId", "unknown")
                logger.warning(
                    "Skipping group '%s' (id=%s): validation failed "
                    "(likely unknown adFormat '%s')",
                    group_name,
                    group_id,
                    item.get("adFormat", "N/A"),
                )

        # Detect A/B tests -- warn but do not raise
        for group in groups:
            if group.ab_test is not None and group.ab_test != "N/A":
                logger.warning(
                    "A/B test detected on group '%s' (id=%s): abTest='%s' "
                    "for app '%s'. Mediation Management API may fail while "
                    "A/B test is active.",
                    group.group_name,
                    group.group_id,
                    group.ab_test,
                    app_key,
                )

        logger.debug("get_groups(%s) returned %d groups", app_key, len(groups))
        return groups

    async def create_group(self, app_key: str, group: Group) -> Group:
        """Create a new mediation group for an app.

        Sends a POST request to the LevelPlay Groups API v4 with
        GET-style field names (``groupName``, ``adFormat``, ``countries``,
        ``position``). The API returns HTTP 200 with an empty body, so a
        follow-up ``get_groups()`` call is made to fetch the created group
        by matching on both ``groupName`` and ``adFormat``.

        Args:
            app_key: The LevelPlay app key.
            group: Group configuration to create. Must not use
                ``AdFormat.REWARDED_VIDEO``.

        Returns:
            The created ``Group`` with server-assigned ``group_id``.

        Raises:
            ValueError: If ``group.ad_format`` is ``AdFormat.REWARDED_VIDEO``.
            ApiError: If the API request fails.

        Example::

            group = Group.model_validate({
                "groupName": "US Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            })
            created = await adapter.create_group("1a2b3c4d5", group)
            print(created.group_id)  # Server-assigned ID
        """
        if group.ad_format == AdFormat.REWARDED_VIDEO:
            raise ValueError(
                "create_group() does not support AdFormat.REWARDED_VIDEO. "
                "Use AdFormat.REWARDED ('rewarded') for Groups v4 API."
            )

        # POST uses GET-style field names
        payload: dict[str, Any] = {
            "groupName": group.group_name,
            "adFormat": group.ad_format.value,
            "countries": group.countries,
            "position": group.position,
        }

        logger.info(
            "Creating group '%s' (%s) for app '%s'",
            group.group_name,
            group.ad_format.value,
            app_key,
        )

        await self._request(
            "POST",
            f"{GROUPS_V4_URL}/{app_key}",
            json_body=[payload],
            endpoint_key="groups",
        )

        # POST returns empty body -- fetch created group by name + format
        all_groups = await self.get_groups(app_key)
        for g in all_groups:
            if g.group_name == group.group_name and g.ad_format == group.ad_format:
                logger.info(
                    "Created group '%s' (id=%s) for app '%s'",
                    g.group_name,
                    g.group_id,
                    app_key,
                )
                return g

        raise ApiError(
            f"Group '{group.group_name}' ({group.ad_format.value}) was not found "
            f"after creation for app '{app_key}'",
            status_code=200,
        )

    async def update_group(
        self, app_key: str, group_id: int, group: Group
    ) -> Group:
        """Update an existing mediation group.

        Sends a PUT request to the LevelPlay Groups API v4 with
        prefixed field names (``groupId``, ``groupName``,
        ``groupCountries``, ``groupPosition``). The ``adFormat`` field
        is omitted from the PUT payload. Partial PUT preserves all
        unincluded fields on the server side.

        Args:
            app_key: The LevelPlay app key.
            group_id: ID of the group to update.
            group: Updated group configuration. Must not use
                ``AdFormat.REWARDED_VIDEO``.

        Returns:
            The updated ``Group`` fetched from the server after the PUT.

        Raises:
            ValueError: If ``group.ad_format`` is ``AdFormat.REWARDED_VIDEO``.
            ApiError: If the API request fails.

        Example::

            updated = await adapter.update_group("1a2b3c4d5", 12345, group)
            print(updated.countries)
        """
        if group.ad_format == AdFormat.REWARDED_VIDEO:
            raise ValueError(
                "update_group() does not support AdFormat.REWARDED_VIDEO. "
                "Use AdFormat.REWARDED ('rewarded') for Groups v4 API."
            )

        # PUT v4 uses same field names as GET (not prefixed)
        payload: dict[str, Any] = {
            "groupId": group_id,
            "adFormat": group.ad_format.value,
            "groupName": group.group_name,
            "countries": group.countries,
            "position": group.position,
        }

        logger.info(
            "Updating group %d ('%s') for app '%s'",
            group_id,
            group.group_name,
            app_key,
        )

        await self._request(
            "PUT",
            f"{GROUPS_V4_URL}/{app_key}",
            json_body=[payload],
            endpoint_key="groups",
        )

        # Fetch updated group to return current server state
        all_groups = await self.get_groups(app_key)
        for g in all_groups:
            if g.group_id == group_id:
                logger.info(
                    "Updated group %d ('%s') for app '%s'",
                    group_id,
                    g.group_name,
                    app_key,
                )
                return g

        raise ApiError(
            f"Group {group_id} was not found after update for app '{app_key}'",
            status_code=200,
        )

    async def delete_group(self, app_key: str, group_id: int) -> None:
        """Delete a mediation group.

        Sends a DELETE request to the LevelPlay Groups API v4 with a
        JSON body containing ``{"ids": [group_id]}``. The API returns
        HTTP 200 with an empty body on success.

        Args:
            app_key: The LevelPlay app key.
            group_id: ID of the group to delete.

        Raises:
            ApiError: If the API request fails.

        Example::

            await adapter.delete_group("1a2b3c4d5", 12345)
        """
        logger.info(
            "Deleting group %d for app '%s'",
            group_id,
            app_key,
        )

        await self._request(
            "DELETE",
            f"{GROUPS_V4_URL}/{app_key}",
            json_body={"ids": [group_id]},
            endpoint_key="groups",
        )

    async def get_instances(self, app_key: str) -> list[Instance]:
        """Get all ad network instances for an app from the Instances API.

        Uses v3 as the primary endpoint and falls back to v1 if v3 returns
        a 404 (``ApiError`` with ``status_code=404``). Field names from
        the API response are normalized to match the ``Instance`` model
        aliases before validation.

        Args:
            app_key: The LevelPlay app key to fetch instances for.

        Returns:
            List of ``Instance`` models, one per ad network instance.

        Raises:
            ApiError: If the API returns a non-retryable HTTP error
                (other than 404 during v3/v1 fallback).
            RateLimitError: If the instances endpoint rate budget is exhausted.

        Example::

            async with LevelPlayAdapter(cred) as adapter:
                instances = await adapter.get_instances("1a2b3c4d5")
                for inst in instances:
                    print(f"{inst.instance_name} ({inst.network_name})")
        """
        try:
            response = await self._request(
                "GET",
                INSTANCES_V3_URL,
                params={"appKey": app_key},
                endpoint_key="instances",
            )
        except ApiError as exc:
            if exc.status_code in (404, 410):
                logger.warning(
                    "Instances v3 returned %d for app '%s', falling back to v1",
                    exc.status_code,
                    app_key,
                )
                try:
                    response = await self._request(
                        "GET",
                        INSTANCES_V1_URL,
                        params={"appKey": app_key},
                        endpoint_key="instances",
                    )
                except ApiError as v1_exc:
                    if v1_exc.status_code in (404, 410):
                        logger.warning(
                            "Instances v1 also returned %d for app '%s'. "
                            "Standalone Instances API is unavailable — "
                            "instance data is only available embedded in "
                            "Groups v4 responses.",
                            v1_exc.status_code,
                            app_key,
                        )
                        return []
                    raise
            else:
                raise

        if not response:
            return []

        # Unwrap object wrapper: if response is a dict with an "instances" key,
        # extract the list from that key
        if isinstance(response, dict):
            if "instances" in response:
                response = response["instances"]
            else:
                logger.warning(
                    "get_instances() expected a list or dict with 'instances' key, "
                    "got dict with keys: %s",
                    list(response.keys()),
                )
                return []

        if not isinstance(response, list):
            logger.warning(
                "get_instances() expected a list response, got %s",
                type(response).__name__,
            )
            return []

        instances: list[Instance] = []
        for item in response:
            try:
                normalized = self._normalize_instance_response(item)
                instance = Instance.model_validate(normalized)
                instances.append(instance)
            except ValidationError:
                instance_name = item.get(
                    "name", item.get("instanceName", item.get("id", "unknown"))
                )
                logger.warning(
                    "Skipping instance '%s' for app '%s': validation failed",
                    instance_name,
                    app_key,
                )

        logger.debug("get_instances(%s) returned %d instances", app_key, len(instances))
        return instances

    def _normalize_instance_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize alternate field names from the Instances API to model aliases.

        Performs defensive remapping: only renames a field if the alternate key
        exists and the canonical key does not. This avoids data loss if the API
        returns both keys.

        Field mappings:
        - ``providerName`` -> ``networkName``
        - ``instanceName`` -> ``name``
        - ``instanceId`` -> ``id``
        - ``globalPricing`` -> ``groupRate``
        - ``countriesPricing`` -> ``countriesRate`` (with sub-field
          normalization: ``country`` -> ``countryCode``, ``eCPM`` -> ``rate``)
        - ``isLive`` string normalization: ``"active"`` -> ``True``,
          ``"inactive"`` -> ``False``

        Args:
            raw: Raw instance dict from the API response.

        Returns:
            Normalized dict ready for ``Instance.model_validate()``.
        """
        result = dict(raw)

        # Top-level field renames (defensive: only if alternate exists and canonical does not)
        field_renames: dict[str, str] = {
            "providerName": "networkName",
            "instanceName": "name",
            "instanceId": "id",
            "globalPricing": "groupRate",
        }
        for alt_key, canonical_key in field_renames.items():
            if alt_key in result and canonical_key not in result:
                result[canonical_key] = result.pop(alt_key)

        # countriesPricing -> countriesRate with sub-field normalization
        if "countriesPricing" in result and "countriesRate" not in result:
            countries_pricing = result.pop("countriesPricing")
            if countries_pricing is not None and isinstance(countries_pricing, list):
                normalized_entries: list[dict[str, Any]] = []
                for entry in countries_pricing:
                    normalized_entry = dict(entry)
                    if "country" in normalized_entry and "countryCode" not in normalized_entry:
                        normalized_entry["countryCode"] = normalized_entry.pop("country")
                    if "eCPM" in normalized_entry and "rate" not in normalized_entry:
                        normalized_entry["rate"] = normalized_entry.pop("eCPM")
                    normalized_entries.append(normalized_entry)
                result["countriesRate"] = normalized_entries
            else:
                result["countriesRate"] = countries_pricing

        # isLive string normalization: "active" -> True, "inactive" -> False
        if "isLive" in result and isinstance(result["isLive"], str):
            is_live_str = result["isLive"].lower()
            if is_live_str == "active":
                result["isLive"] = True
            elif is_live_str == "inactive":
                result["isLive"] = False

        return result

    async def create_instances(
        self, app_key: str, instances: list[Instance]
    ) -> list[Instance]:
        """Create ad network instances in batch.

        .. note::
            Not yet implemented. Deferred to the ConfigEngine write task.
            Will use the LevelPlay Instances API POST endpoint.
            LevelPlay rejects the entire batch if any single item fails.

        Args:
            app_key: The LevelPlay app key.
            instances: List of instances to create.

        Raises:
            AdapterNotSupportedError: Always -- method not yet implemented.
        """
        raise AdapterNotSupportedError(
            "create_instances() is not yet implemented in the LevelPlay adapter. "
            "Write operations are deferred to the ConfigEngine task."
        )

    async def update_instances(
        self, app_key: str, instances: list[Instance]
    ) -> list[Instance]:
        """Update ad network instances in batch.

        .. note::
            Not yet implemented. Deferred to the ConfigEngine write task.
            Will use the LevelPlay Instances API PUT endpoint.

        Args:
            app_key: The LevelPlay app key.
            instances: List of instances to update.

        Raises:
            AdapterNotSupportedError: Always -- method not yet implemented.
        """
        raise AdapterNotSupportedError(
            "update_instances() is not yet implemented in the LevelPlay adapter. "
            "Write operations are deferred to the ConfigEngine task."
        )

    async def delete_instance(
        self, app_key: str, instance_id: int
    ) -> None:
        """Delete an ad network instance.

        .. note::
            Not yet implemented. Deferred to the ConfigEngine write task.
            Will use the LevelPlay Instances API DELETE endpoint.

        Args:
            app_key: The LevelPlay app key.
            instance_id: ID of the instance to delete.

        Raises:
            AdapterNotSupportedError: Always -- method not yet implemented.
        """
        raise AdapterNotSupportedError(
            "delete_instance() is not yet implemented in the LevelPlay adapter. "
            "Write operations are deferred to the ConfigEngine task."
        )

    async def get_placements(self, app_key: str) -> list[Placement]:
        """Get all placements for an app.

        .. note::
            Not yet implemented. Deferred to the ConfigEngine read-placements task.
            Will use the LevelPlay Placements API v1 endpoint.

        Args:
            app_key: The LevelPlay app key.

        Raises:
            AdapterNotSupportedError: Always -- method not yet implemented.
        """
        raise AdapterNotSupportedError(
            "get_placements() is not yet implemented in the LevelPlay adapter. "
            "Placement reads are deferred to a future task."
        )

    async def get_reporting(
        self,
        app_key: str,
        start_date: str,
        end_date: str,
        metrics: list[str],
        breakdowns: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch performance reporting data for an app.

        .. note::
            Not yet implemented. Deferred to the ConfigEngine reporting task.
            Will use the LevelPlay Reporting API v1 endpoint.

        Args:
            app_key: The LevelPlay app key.
            start_date: Start date in ``YYYY-MM-DD`` format.
            end_date: End date in ``YYYY-MM-DD`` format.
            metrics: List of metric names (e.g., ``["revenue", "impressions"]``).
            breakdowns: Optional breakdown dimensions (e.g., ``["country", "network"]``).

        Raises:
            AdapterNotSupportedError: Always -- method not yet implemented.
        """
        raise AdapterNotSupportedError(
            "get_reporting() is not yet implemented in the LevelPlay adapter. "
            "Reporting reads are deferred to a future task."
        )
