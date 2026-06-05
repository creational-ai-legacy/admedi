"""Live probe documenting current LevelPlay Placements v1 and Reporting v1 contracts.

These tests hit the real LevelPlay API to document observed reality for a code
migration (the legacy instances endpoints have moved to the ``levelPlay/…``
namespace and now return 410 Gone). They are read-only (GET only) and assert the
status codes observed on 2026-06-04 so regressions/endpoint sunsets are caught.

Skipped automatically when ``LEVELPLAY_SECRET_KEY`` is absent.

Observed 2026-06-04 (app ss-google, appKey=1f93aca35):
  - Placements v1 (/partners/publisher/placements/v1): 200, list of placement
    records. Endpoint still LIVE at legacy path — no namespace migration implied
    (unlike instances which moved to levelPlay/… v4).
  - Reporting v1 (/levelPlay/reporting/v1): 400 GET without date filter —
    requires ``startDate`` and ``endDate`` (errorsArray code ERR-5218).
"""

from __future__ import annotations

import os

import httpx
import pytest

from admedi.adapters.levelplay import LevelPlayAdapter, load_credential_from_env
from admedi.constants import PLACEMENTS_URL, REPORTING_URL

pytestmark = pytest.mark.skipif(
    not os.getenv("LEVELPLAY_SECRET_KEY"),
    reason="LEVELPLAY_SECRET_KEY not set; live API credentials required",
)

APP_KEY = "1f93aca35"


@pytest.fixture
async def bearer_token() -> str:
    async with LevelPlayAdapter(load_credential_from_env()) as adapter:
        await adapter.authenticate()
        token = adapter._bearer_token
        assert token is not None, "authenticate() did not set a bearer token"
        return token


@pytest.mark.asyncio
async def test_placements_v1_live(bearer_token: str) -> None:
    """Placements v1 is LIVE at the legacy path and returns a list of records."""
    headers = {"Authorization": f"Bearer {bearer_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{PLACEMENTS_URL}?appKey={APP_KEY}", headers=headers
        )

    # Endpoint still alive at legacy path (contrast: instances -> 410 Gone).
    assert resp.status_code == 200, (
        f"Placements v1 expected 200 (live), got {resp.status_code}: {resp.text[:500]}"
    )

    payload = resp.json()
    assert isinstance(payload, list), "Placements v1 should return a JSON list"
    if payload:
        record = payload[0]
        # Document the placement record contract.
        for key in ("name", "id", "adUnit", "adDelivery", "capping", "pacing"):
            assert key in record, f"placement record missing expected key {key!r}"
        assert isinstance(record["capping"], dict)
        assert isinstance(record["pacing"], dict)


@pytest.mark.asyncio
async def test_reporting_v1_requires_date_filter(bearer_token: str) -> None:
    """Reporting v1 is LIVE but a GET without a date filter returns 400.

    Documents the required-param contract: ``startDate`` and ``endDate`` are
    mandatory (errorsArray entries with code ERR-5218). We intentionally do NOT
    escalate to a POST; the 400 is the informative contract we want.
    """
    headers = {"Authorization": f"Bearer {bearer_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{REPORTING_URL}?appKey={APP_KEY}", headers=headers
        )

    # Not 404/410 -> endpoint exists; 400 -> missing required params.
    assert resp.status_code == 400, (
        f"Reporting v1 expected 400 (missing date filter), got "
        f"{resp.status_code}: {resp.text[:500]}"
    )

    body = resp.json()
    assert body.get("code") == 400
    errors = body.get("errorsArray", [])
    assert errors, "expected errorsArray describing required params"
    # startDate and endDate are the demanded params.
    demanded = {
        param
        for err in errors
        for param in err.get("params", {})
    }
    assert {"startDate", "endDate"} <= demanded, (
        f"expected startDate/endDate in required params, observed: {demanded}"
    )
