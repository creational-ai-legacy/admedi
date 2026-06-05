"""De-risk spike (G1): can the LevelPlay v4 API remove instances on ss-google?

READ-ONLY + NON-DESTRUCTIVE probe only. Does NOT delete any real instance.
  1. Auth via the real adapter.
  2. GET v4 instances for ss-google -> list InMobi instances (id / bidder / default).
  3. Cross-check instances embedded in Groups v4.
  4. DELETE probe with a bogus id -> expect HTTP 400 atomic-rejection (nothing deleted).
"""
import asyncio
import json

import httpx

from admedi.adapters.levelplay import LevelPlayAdapter, load_credential_from_env
from admedi.constants import GROUPS_V4_URL, LEVELPLAY_BASE_URL

APP = "1f93aca35"  # ss-google
V4 = f"{LEVELPLAY_BASE_URL}/levelPlay/network/instances/v4"
BOGUS_ID = 999999999  # 9 digits; real ids are ~4 digits. Atomic batch => safe.


def _find_inmobi(obj, found):
    """Recursively collect dict nodes that look like an InMobi instance."""
    if isinstance(obj, dict):
        blob = json.dumps(obj).lower()
        if "inmobi" in blob and ("id" in obj or "instanceid" in obj or "instanceId" in obj):
            found.append(obj)
        for v in obj.values():
            _find_inmobi(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _find_inmobi(v, found)


async def main():
    cred = load_credential_from_env()
    async with LevelPlayAdapter(cred) as adapter:
        await adapter.authenticate()
        token = adapter._bearer_token
        print(f"[auth] OK, token len={len(token)}")

        async with httpx.AsyncClient(timeout=60.0) as c:
            hdr = {"Authorization": f"Bearer {token}"}

            # 1. v4 instances GET (the endpoint that owns DELETE)
            url = f"{V4}/{APP}/"
            print(f"\n[1] GET {url}")
            r = await c.get(url, headers=hdr)
            print(f"    -> HTTP {r.status_code}")
            v4_body = None
            if r.status_code == 200:
                v4_body = r.json()
                n = len(v4_body) if isinstance(v4_body, list) else "?"
                print(f"    v4 instances GET works; payload type={type(v4_body).__name__} count={n}")
            else:
                print(f"    body (first 400 chars): {r.text[:400]}")

            # 2. Groups v4 cross-check (where instances are known to be embedded)
            gurl = f"{GROUPS_V4_URL}/{APP}"
            print(f"\n[2] GET {gurl}  (Groups v4 cross-check)")
            rg = await c.get(gurl, headers=hdr)
            print(f"    -> HTTP {rg.status_code}")
            inmobi = []
            if rg.status_code == 200:
                _find_inmobi(rg.json(), inmobi)
            if v4_body is not None:
                _find_inmobi(v4_body, inmobi)

            # de-dup by id
            seen, uniq = set(), []
            for it in inmobi:
                iid = it.get("id") or it.get("instanceId") or it.get("instanceid")
                if iid not in seen:
                    seen.add(iid)
                    uniq.append(it)
            print(f"\n[InMobi instances found on ss-google: {len(uniq)}]")
            for it in uniq:
                iid = it.get("id") or it.get("instanceId") or it.get("instanceid")
                bidder = it.get("isBidder", it.get("bidding", "?"))
                default = it.get("isDefault", it.get("default", "?"))
                name = it.get("instanceName") or it.get("name") or "?"
                fmt = it.get("adUnit") or it.get("adFormat") or it.get("format") or "?"
                print(f"    id={iid}  fmt={fmt}  bidder={bidder}  default={default}  name={name}")

            # 3. NON-DESTRUCTIVE DELETE probe (bogus id -> atomic 400, nothing deleted)
            print(f"\n[3] DELETE probe {url}  body={{'ids':[{BOGUS_ID}]}}  (bogus id; expect 400)")
            rd = await c.request("DELETE", url, headers=hdr, json={"ids": [BOGUS_ID]})
            print(f"    -> HTTP {rd.status_code}")
            print(f"    body (first 600 chars): {rd.text[:600]}")
            print("\n[interpretation]")
            if rd.status_code == 400:
                print("    400 with error array => endpoint LIVE, validates ids, would delete valid ones. Feature CONFIRMED, nothing deleted.")
            elif rd.status_code in (401, 403):
                print("    auth/permission issue on DELETE — endpoint exists but our token lacks delete scope.")
            elif rd.status_code == 404:
                print("    404 => v4 instances DELETE path not found as constructed; URL form needs adjustment.")
            elif rd.status_code == 200:
                print("    200 on a bogus id is unexpected (non-atomic?) — review body above.")
            else:
                print("    unexpected — review body above.")


if __name__ == "__main__":
    asyncio.run(main())
