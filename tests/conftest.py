"""Shared test fixtures for admedi test suite.

Provides reusable fixtures for mocking credentials and JWT tokens
across all test modules.

Examples:
    Using mock_credential in a test::

        def test_adapter_init(mock_credential):
            adapter = LevelPlayAdapter(mock_credential)
            assert adapter is not None

    Using mock_jwt_token in a test::

        def test_token_parsing(mock_jwt_token):
            token = mock_jwt_token
            assert isinstance(token, str)
"""

from __future__ import annotations

import time

import jwt
import pytest

from admedi.models import Credential, Mediator


@pytest.fixture
def mock_credential() -> Credential:
    """Return a Credential with dummy values for unit testing.

    The credential uses fake keys that are never sent to any real API.
    Suitable for testing adapter instantiation, credential loading logic,
    and any code path that requires a ``Credential`` instance.
    """
    return Credential(
        mediator=Mediator.LEVELPLAY,
        secret_key="test_secret_key_abc123",
        refresh_token="test_refresh_token_xyz789",
    )


@pytest.fixture
def mock_jwt_token() -> str:
    """Return a known JWT string with a predictable ``exp`` claim.

    The token is encoded with HS256 using the signing key ``"test-secret-key-at-least-32-bytes-long"``.
    The ``exp`` claim is set to 1 hour from the current time in seconds.
    This fixture is useful for testing JWT decoding, expiry detection,
    and token caching logic.

    The token payload contains:
        - ``sub``: ``"test-publisher"``
        - ``iat``: current time (seconds)
        - ``exp``: current time + 3600 (1 hour from now, in seconds)
    """
    now = int(time.time())
    payload = {
        "sub": "test-publisher",
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, "test-secret-key-at-least-32-bytes-long", algorithm="HS256")
