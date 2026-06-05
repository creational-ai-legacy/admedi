"""Live integration test: pin the LevelPlay Instances v4 endpoint contract.

This test hits the LIVE LevelPlay API. It is read-only / non-destructive:
- a GET of the instances list (which is also validated through the ``Instance``
  domain model), and
- a single DELETE probe with a deliberately-INVALID id (999999999), which the
  API atomically rejects with HTTP 400 and deletes nothing.

Both probes exercise the production adapter surface (``get_instances`` /
``delete_instance``) so the live contract is pinned against the real code path,
not a hand-rolled ``httpx`` request. It is skipped entirely when LevelPlay
credentials are absent from the environment, so it is safe in CI without
secrets.
"""

from __future__ import annotations

import os

import httpx
import pytest

from admedi.adapters.levelplay import LevelPlayAdapter, load_credential_from_env
from admedi.constants import LEVELPLAY_BASE_URL
from admedi.exceptions import ApiError
from admedi.models.enums import AdFormat
from admedi.models.instance import Instance

pytestmark = pytest.mark.skipif(
    not os.getenv("LEVELPLAY_SECRET_KEY"),
    reason="LevelPlay credentials not present (LEVELPLAY_SECRET_KEY unset) — skipping live API test",
)

APP_KEY = "1f93aca35"  # ss-google

# Keys present on EVERY instance record (captured from the live v4 response).
REQUIRED_INSTANCE_KEYS = {
    "instanceId",
    "instanceName",
    "adUnit",
    "adFormat",
    "networkName",
    "isBidder",
    "isLive",
    "groups",
}

# Bogus id: invalid, so DELETE is atomically rejected with HTTP 400 — deletes nothing.
INVALID_INSTANCE_ID = 999999999

# v4 GET returns BOTH `adUnit` (rewardedVideo) and `adFormat` (rewarded) on a
# rewarded record — two DISTINCT keys (see docs/references/levelplay-api-v4-migration.md
# lines 53-54). `adUnit` is a real `Instance` model field (alias `adUnit`); `adFormat`
# is a v4-only key the model IGNORES (extra="ignore", Option B) and is therefore asserted
# against the RAW payload, never via `instance.adFormat` (no such attribute).
EXPECTED_REWARDED_AD_UNIT = "rewardedVideo"
EXPECTED_REWARDED_AD_FORMAT = "rewarded"


def _endpoint() -> str:
    return f"{LEVELPLAY_BASE_URL}/levelPlay/network/instances/v4/{APP_KEY}/"


async def _bearer_token() -> str:
    async with LevelPlayAdapter(load_credential_from_env()) as adapter:
        await adapter.authenticate()
        token = adapter._bearer_token
    assert token, "authenticate() did not produce a bearer token"
    return token


@pytest.mark.asyncio
async def test_get_instances_v4_is_list_of_dicts_with_expected_keys() -> None:
    """The raw live v4 GET is a list of dicts carrying the required keys."""
    token = await _bearer_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(_endpoint(), headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Top level is a list.
    assert isinstance(body, list), f"expected list, got {type(body).__name__}"
    assert body, "expected at least one instance record"

    for rec in body:
        assert isinstance(rec, dict), f"record is not a dict: {type(rec).__name__}"
        missing = REQUIRED_INSTANCE_KEYS - set(rec.keys())
        assert not missing, f"record missing required keys {missing}: {rec}"
        # Type spot-checks on the always-present keys.
        assert isinstance(rec["instanceId"], int)
        assert isinstance(rec["instanceName"], str)
        assert isinstance(rec["isBidder"], bool)
        assert isinstance(rec["isLive"], bool)
        assert isinstance(rec["groups"], list)


@pytest.mark.asyncio
async def test_get_instances_v4_validates_to_instance_model_with_distinct_ad_fields() -> None:
    """A live v4 GET validates through ``get_instances`` to ``Instance`` models,
    and a rewarded record carries the two DISTINCT v4 ad fields:
    raw ``adUnit == "rewardedVideo"`` AND raw ``adFormat == "rewarded"``.

    This pins the Option-B contract: ``adUnit`` IS a model field (and validates
    to ``AdFormat.REWARDED_VIDEO``), while ``adFormat`` is a v4-only key the
    model IGNORES — so the ``rewarded``/``rewardedVideo`` distinction is asserted
    against the RAW payload, guarding against a future drift that collapses the
    two fields.
    """
    token = await _bearer_token()
    headers = {"Authorization": f"Bearer {token}"}

    # 1. RAW payload: find a rewarded record and assert the two DISTINCT ad fields.
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(_endpoint(), headers=headers)
    assert resp.status_code == 200, resp.text
    raw_records = resp.json()
    assert isinstance(raw_records, list) and raw_records

    rewarded_raw = next(
        (
            rec
            for rec in raw_records
            if rec.get("adUnit") == EXPECTED_REWARDED_AD_UNIT
        ),
        None,
    )
    assert rewarded_raw is not None, (
        "expected at least one record with adUnit == "
        f"{EXPECTED_REWARDED_AD_UNIT!r} in the live v4 response"
    )
    # Two DISTINCT v4 keys — asserted against the RAW dict (adFormat is NOT a model field).
    assert rewarded_raw["adUnit"] == EXPECTED_REWARDED_AD_UNIT
    assert rewarded_raw["adFormat"] == EXPECTED_REWARDED_AD_FORMAT
    assert rewarded_raw["adUnit"] != rewarded_raw["adFormat"], (
        "adUnit and adFormat must remain DISTINCT v4 fields "
        f"(adUnit={rewarded_raw['adUnit']!r}, adFormat={rewarded_raw['adFormat']!r})"
    )

    # 2. Through the production adapter: every live record validates to Instance,
    #    and the rewarded record's `ad_unit` model field is AdFormat.REWARDED_VIDEO
    #    (v4-only `adFormat` is silently dropped by extra="ignore").
    async with LevelPlayAdapter(load_credential_from_env()) as adapter:
        await adapter.authenticate()
        instances = await adapter.get_instances(APP_KEY)

    assert instances, "get_instances() returned no instances for the live app"
    assert all(isinstance(inst, Instance) for inst in instances)
    # The rewarded record validates and maps adUnit → AdFormat.REWARDED_VIDEO.
    rewarded_models = [
        inst for inst in instances if inst.ad_unit == AdFormat.REWARDED_VIDEO
    ]
    assert rewarded_models, (
        "expected a validated Instance with ad_unit == AdFormat.REWARDED_VIDEO"
    )
    # The model does NOT carry adFormat (Option B — extra="ignore").
    assert not hasattr(rewarded_models[0], "ad_format"), (
        "Instance must NOT gain an ad_format field (Option B — model unchanged)"
    )


@pytest.mark.asyncio
async def test_delete_invalid_id_maps_to_err_1427_api_error() -> None:
    """``delete_instance`` with a bogus id raises the Step-3 mapped ``ApiError``
    whose underlying first errorsArray code is ``ERR-1427`` (invalid id).

    Non-destructive: the invalid id is atomically rejected (HTTP 400), deleting
    nothing. Asserts the adapter's MAPPED error (method-named message + preserved
    ``status_code``/``errorsArray``), not merely that an errorsArray exists.
    """
    async with LevelPlayAdapter(load_credential_from_env()) as adapter:
        await adapter.authenticate()
        with pytest.raises(ApiError) as exc_info:
            await adapter.delete_instance(APP_KEY, INVALID_INSTANCE_ID)

    err = exc_info.value
    # Mapped status code preserved from the 400.
    assert err.status_code == 400, f"expected status 400, got {err.status_code}"
    # The mapped message names the method and the mapped code.
    assert "delete_instance()" in str(err)
    assert "ERR-1427" in str(err)
    # The original errorsArray payload is preserved on the re-raised ApiError.
    assert isinstance(err.response_body, dict)
    errors = err.response_body.get("errorsArray")
    assert isinstance(errors, list) and errors, (
        f"no errorsArray in preserved response_body: {err.response_body}"
    )
    assert errors[0].get("code") == "ERR-1427", (
        f"expected first errorsArray code ERR-1427, got {errors[0].get('code')!r}"
    )
