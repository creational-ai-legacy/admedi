"""F1 blast-radius probe (READ-ONLY).

QA finding F1: an `instances[]`-bearing Groups v4 PUT might full-replace co-resident
fields. The proven partial-merge evidence (put-behavior-test-results.md) used a group
with segments=[] / floorPrice=None, so non-empty preservation was never exercised.

This probe is GET-ONLY. It enumerates, per ss-google group, the fields most at risk
if the server full-replaces on a membership PUT:
  - segments (non-empty?)
  - floor_price (set?)
  - mediation_ad_unit_id (the ad-unit binding — NOT echoed by update_group today)
  - instance count + whether InMobi is present (the removal target)

If no target group carries non-empty segments/floorPrice, F1's blast radius on those
two collapses to mediationAdUnitId only (which proven partial-merge preserves).
NO WRITES. NO MUTATIONS.
"""
import asyncio

from admedi.adapters.levelplay import LevelPlayAdapter, load_credential_from_env

APP = "1f93aca35"  # ss-google


async def main() -> None:
    cred = load_credential_from_env()
    async with LevelPlayAdapter(cred) as adapter:
        await adapter.authenticate()
        groups = await adapter.get_groups(APP)
        print(f"[ss-google] {len(groups)} groups\n")

        any_segments = False
        any_floor = False
        for g in sorted(groups, key=lambda x: (x.ad_format.value, x.position)):
            segs = g.segments or []
            seg_n = len(segs) if isinstance(segs, list) else "?"
            inst = g.instances or []
            networks = sorted({i.network_name for i in inst})
            has_inmobi = any("inmobi" in (i.network_name or "").lower() for i in inst)
            if seg_n not in (0, "?"):
                any_segments = True
            if g.floor_price is not None:
                any_floor = True
            print(
                f"  {g.ad_format.value:<13} pos={g.position} '{g.group_name}' "
                f"id={g.group_id}\n"
                f"      segments={seg_n}  floorPrice={g.floor_price}  "
                f"mediationAdUnitId={g.mediation_ad_unit_id!r}\n"
                f"      instances={len(inst)} networks={networks} "
                f"InMobi_present={has_inmobi}"
            )

        print("\n=== F1 blast-radius summary ===")
        print(f"  any group with non-empty segments? {any_segments}")
        print(f"  any group with a floorPrice set?   {any_floor}")
        print(
            "  => if both False, the only field an instances-PUT could wipe that "
            "update_group does NOT echo is mediationAdUnitId\n"
            "     (and proven partial-merge preserves it; put-behavior-test-results.md)."
        )


if __name__ == "__main__":
    asyncio.run(main())
