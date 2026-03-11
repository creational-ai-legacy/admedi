"""App model representing a LevelPlay application.

Maps to the LevelPlay Applications API v6 response shape with camelCase
alias support for seamless JSON round-trips.

Example:
    >>> from admedi.models.app import App
    >>> app = App.model_validate({
    ...     "appKey": "abc123",
    ...     "appName": "My App",
    ...     "platform": "Android",
    ...     "bundleId": "com.example.app",
    ... })
    >>> app.app_key
    'abc123'
    >>> app.model_dump(by_alias=True)["appKey"]
    'abc123'
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from admedi.models.enums import Mediator, Platform


class App(BaseModel):
    """A LevelPlay application.

    Fields map to the LevelPlay Applications API v6 response. The
    ``mediator`` field is an admedi-internal addition for multi-mediator
    tracking and is not present in the API response.

    Attributes:
        app_key: Unique application identifier from LevelPlay.
        app_name: Display name of the application.
        platform: Target mobile platform (Android, iOS, Amazon).
        bundle_id: Platform-specific bundle identifier (e.g., com.example.app).
        app_status: Application status string, defaults to "active".
        mediator: Admedi-internal mediator identifier, defaults to LEVELPLAY.
        coppa: Whether the app is COPPA-compliant, defaults to False.
        taxonomy: App taxonomy category, if set.
        creation_date: Date string when the app was created (e.g., "2025-06-15").
        ad_units: Nested dict of ad unit configuration from the API.
        ccpa: CCPA compliance string, if set.
        network_reporting_api: Whether network reporting API is enabled.
        bundle_ref_id: Bundle reference identifier, if set.
        icon: App icon URL, if set.
    """

    model_config = ConfigDict(populate_by_name=True)

    app_key: str = Field(alias="appKey")
    app_name: str = Field(alias="appName")
    platform: Platform
    bundle_id: str = Field(alias="bundleId")
    app_status: str = Field(default="active", alias="appStatus")
    mediator: Mediator = Field(default=Mediator.LEVELPLAY)
    coppa: bool = Field(default=False)
    taxonomy: str | None = Field(default=None)
    creation_date: str | None = Field(default=None, alias="creationDate")
    ad_units: dict[str, Any] | None = Field(default=None, alias="adUnits")
    ccpa: str | None = Field(default=None)
    network_reporting_api: bool | None = Field(default=None, alias="networkReportingApi")
    bundle_ref_id: str | None = Field(default=None, alias="bundleRefId")
    icon: str | None = Field(default=None)
