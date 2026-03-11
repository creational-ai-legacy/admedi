"""Typed exception hierarchy for admedi.

All admedi exceptions inherit from AdmediError, enabling both targeted
and broad exception handling:

    try:
        await adapter.authenticate()
    except AuthError:
        # Handle authentication specifically
        ...
    except AdmediError:
        # Catch any admedi error
        ...
"""

from __future__ import annotations

from typing import Any


class AdmediError(Exception):
    """Base exception for all admedi errors.

    Attributes:
        message: Human-readable error description.
        detail: Optional additional context (e.g., raw API response excerpt).

    Example:
        >>> raise AdmediError("Something went wrong", detail="see logs")
    """

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.message: str = message
        self.detail: str | None = detail


class AuthError(AdmediError):
    """Authentication or authorization failure.

    Raised when LevelPlay OAuth token refresh fails, credentials are
    invalid, or the JWT has expired and cannot be renewed.

    Example:
        >>> raise AuthError("Invalid refresh token")
    """


class RateLimitError(AdmediError):
    """API rate limit exceeded.

    Raised when a LevelPlay API endpoint returns HTTP 429 or the
    request count exceeds known rate-limit thresholds.

    Attributes:
        retry_after: Seconds to wait before retrying, if provided by the API.

    Example:
        >>> raise RateLimitError("Groups API limit hit", retry_after=30.0)
    """

    def __init__(
        self,
        message: str,
        retry_after: float | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message, detail=detail)
        self.retry_after: float | None = retry_after


class ApiError(AdmediError):
    """Non-rate-limit API error (4xx/5xx responses).

    Raised for HTTP errors from the LevelPlay API that are not
    authentication or rate-limit issues.

    Attributes:
        status_code: HTTP status code from the API response.
        response_body: Parsed JSON response body, if available.

    Example:
        >>> raise ApiError("Bad request", status_code=400, response_body={"error": "invalid appKey"})
    """

    def __init__(
        self,
        message: str,
        status_code: int,
        response_body: dict[str, Any] | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message, detail=detail)
        self.status_code: int = status_code
        self.response_body: dict[str, Any] | None = response_body


class ConfigValidationError(AdmediError):
    """YAML config or tier template validation failure.

    Raised when a tier template has duplicate countries, a missing
    default tier, or other structural issues detected by model validators.

    Example:
        >>> raise ConfigValidationError("Duplicate country 'US' in tier definitions")
    """


class AdapterNotSupportedError(AdmediError):
    """Adapter does not support the requested capability.

    Raised by ensure_capability() when a MediationAdapter or
    StorageAdapter lacks a required feature (e.g., bidding management
    on a mediator that doesn't support it).

    Example:
        >>> raise AdapterNotSupportedError("AdMob adapter does not support bidding configuration")
    """
