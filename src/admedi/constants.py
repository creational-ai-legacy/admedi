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

MEDIATION_MGMT_V2_URL: str = f"{LEVELPLAY_BASE_URL}/partners/publisher/mediation/management/v2"
"""Legacy Mediation Management v2 endpoint."""

INSTANCES_V1_URL: str = f"{LEVELPLAY_BASE_URL}/partners/publisher/instances/v1"
"""Standalone Instances API v1 endpoint (primary for admedi)."""

INSTANCES_V3_URL: str = f"{LEVELPLAY_BASE_URL}/partners/publisher/instances/v3"
"""Standalone Instances API v3 endpoint (reference only -- ironSource lib uses v3; v1 is primary)."""

PLACEMENTS_URL: str = f"{LEVELPLAY_BASE_URL}/partners/publisher/placements/v1"
"""Placements API v1 endpoint."""

REPORTING_URL: str = f"{LEVELPLAY_BASE_URL}/levelPlay/reporting/v1"
"""LevelPlay Reporting API v1 endpoint."""
