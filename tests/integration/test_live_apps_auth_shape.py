"""Live shape tests for the LevelPlay Auth and Applications v6 endpoints.

These tests hit the live LevelPlay API with **GET requests only** to lock in
the exact response shape that a code migration depends on:

- Auth (``/partners/publisher/auth``) returns a raw JWT string (JSON-quoted)
  that decodes to a payload containing an ``exp`` claim (Unix seconds).
- Applications v6 (``/partners/publisher/applications/v6``) returns a JSON
  array of app-record dicts whose key set is asserted below.

The whole module skips when credentials are absent. ``.env`` is loaded first so
that credentials stored there make ``LEVELPLAY_SECRET_KEY`` visible to the
module-level ``skipif`` (mirroring ``load_credential_from_env``).

No custom pytest markers are registered (so this test is NOT deselected by the
``-m 'not integration'`` default); the only gate is the env-var skip.
"""

from __future__ import annotations

import os

import dotenv
import httpx
import jwt
import pytest

# Load .env so credentials stored there are visible to the skipif gate below.
dotenv.load_dotenv()

from admedi.adapters.levelplay import LevelPlayAdapter, load_credential_from_env
from admedi.constants import APPS_URL, AUTH_URL

pytestmark = pytest.mark.skipif(
    not os.getenv("LEVELPLAY_SECRET_KEY"),
    reason="LEVELPLAY_SECRET_KEY not set; live Auth/Applications shape tests skipped",
)

# Key set captured from live Applications v6 records (2026-06-04).
# ``trackId`` is the only optional key (absent on some records); every other
# key is present on every record.
REQUIRED_APP_KEYS: frozenset[str] = frozenset(
    {
        "adUnits",
        "appKey",
        "appName",
        "appStatus",
        "bundleId",
        "bundleRefId",
        "ccpa",
        "coppa",
        "creationDate",
        "icon",
        "networkReportingApi",
        "platform",
        "taxonomy",
    }
)
OPTIONAL_APP_KEYS: frozenset[str] = frozenset({"trackId"})
ALL_APP_KEYS: frozenset[str] = REQUIRED_APP_KEYS | OPTIONAL_APP_KEYS


@pytest.mark.asyncio
async def test_auth_returns_decodable_jwt_with_exp() -> None:
    """Auth GET returns HTTP 200 and a raw JWT decodable to an ``exp`` claim."""
    cred = load_credential_from_env()

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.request(
            "GET",
            AUTH_URL,
            headers={
                "secretkey": cred.secret_key,
                "refreshToken": cred.refresh_token,
            },
        )

    assert resp.status_code == 200

    # Body is a JWT string, JSON-quoted by the server -> strip surrounding quotes.
    token = resp.text.strip().strip('"')
    assert token, "auth response body was empty"
    # A JWT has exactly three dot-separated segments (header.payload.signature).
    assert token.count(".") == 2

    payload = jwt.decode(
        token, options={"verify_signature": False}, algorithms=["HS256"]
    )
    assert "exp" in payload
    # exp is Unix seconds (10 digits, < 1e12), not milliseconds.
    assert float(payload["exp"]) < 1e12


@pytest.mark.asyncio
async def test_applications_v6_returns_list_of_dicts_with_expected_keys() -> None:
    """Applications v6 GET returns HTTP 200 and a list of dicts with the
    captured key set."""
    cred = load_credential_from_env()

    async with LevelPlayAdapter(cred) as adapter:
        await adapter.authenticate()
        token = adapter._bearer_token
        assert token is not None

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.request(
                "GET",
                APPS_URL,
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200

    body = resp.json()
    assert isinstance(body, list)
    assert body, "applications v6 returned an empty list"

    for record in body:
        assert isinstance(record, dict)
        keys = set(record.keys())
        # Every required key must be present...
        assert REQUIRED_APP_KEYS <= keys
        # ...and no unexpected keys beyond the known union.
        assert keys <= ALL_APP_KEYS
