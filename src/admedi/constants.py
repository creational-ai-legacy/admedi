"""API URL constants for LevelPlay and other mediation platforms.

All URLs are built from ``LEVELPLAY_BASE_URL`` to ensure consistency
and easy updates if the base URL changes.

Examples:
    >>> from admedi.constants import AUTH_URL, GROUPS_V4_URL
    >>> AUTH_URL
    'https://platform.ironsrc.com/partners/publisher/auth'
    >>> GROUPS_V4_URL
    'https://platform.ironsrc.com/levelPlay/groups/v4'
"""

LEVELPLAY_BASE_URL: str = "https://platform.ironsrc.com"
"""Base URL for all LevelPlay / ironSource platform API endpoints."""

AUTH_URL: str = f"{LEVELPLAY_BASE_URL}/partners/publisher/auth"
"""OAuth 2.0 authentication endpoint (secretKey + refreshToken -> JWT)."""

APPS_URL: str = f"{LEVELPLAY_BASE_URL}/partners/publisher/applications/v6"
"""Applications listing endpoint (v6)."""

GROUPS_V4_URL: str = f"{LEVELPLAY_BASE_URL}/levelPlay/groups/v4"
"""LevelPlay Groups API v4 endpoint (per-app mediation groups)."""

INSTANCES_V4_URL: str = f"{LEVELPLAY_BASE_URL}/levelPlay/network/instances/v4"
"""LevelPlay Instances API v4 endpoint (the supported standalone instances API).

The ``appKey`` is passed as a **path segment** with a trailing slash:
``f"{INSTANCES_V4_URL}/{app_key}/"`` (NOT a ``?appKey=`` query param). Supersedes
the sunset v3/v1 endpoints (now ``410 Gone``)."""

PLACEMENTS_URL: str = f"{LEVELPLAY_BASE_URL}/partners/publisher/placements/v1"
"""Placements API v1 endpoint."""

REPORTING_URL: str = f"{LEVELPLAY_BASE_URL}/levelPlay/reporting/v1"
"""LevelPlay Reporting API v1 endpoint."""
