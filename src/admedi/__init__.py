"""Admedi: Config-driven ad mediation management tool."""

__version__ = "0.1.0"

from admedi.exceptions import (
    AdapterNotSupportedError,
    AdmediError,
    ApiError,
    AuthError,
    ConfigValidationError,
    RateLimitError,
)

__all__ = [
    "AdmediError",
    "AuthError",
    "RateLimitError",
    "ApiError",
    "ConfigValidationError",
    "AdapterNotSupportedError",
]
