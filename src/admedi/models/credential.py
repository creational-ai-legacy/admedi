"""Credential model for mediator API authentication.

Stores the secret key, refresh token, and optional JWT expiry for a
mediator platform.

Example:
    >>> from admedi.models.credential import Credential
    >>> cred = Credential(
    ...     mediator="levelplay",
    ...     secret_key="sk_abc",
    ...     refresh_token="rt_xyz",
    ... )
    >>> cred.mediator
    <Mediator.LEVELPLAY: 'levelplay'>
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from admedi.models.enums import Mediator


class Credential(BaseModel):
    """API credentials for a mediator platform.

    Attributes:
        mediator: Which mediation platform these credentials are for.
        secret_key: The platform's secret/API key.
        refresh_token: OAuth refresh token for obtaining access tokens.
        token_expiry: When the current access token expires, if known.
    """

    model_config = ConfigDict(populate_by_name=True)

    mediator: Mediator
    secret_key: str
    refresh_token: str
    token_expiry: datetime | None = Field(default=None)
    """When the current access token expires, if known.

    Note: The LevelPlay adapter tracks token expiry internally
    (``_token_expiry``) rather than on the Credential. This field
    exists for optional serialization by storage adapters.
    """
