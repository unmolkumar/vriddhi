"""Refetch the extra calibration occupations from a running module 1 (over its REST API, never its database).

Run from module-2-skill-gap/ with module 1 up (M1_BASE_URL, default http://localhost:8001):
  python scripts/refetch_extra_occupations.py            # rewrite tests/mocks/m1_heldout_occupations.json
  python scripts/refetch_extra_occupations.py --dry-run  # show what module 1 returns per occupation

The SOC list is read from the current fixture, so the same 10 occupations are refreshed. The metadata records
module 1's version as the client sees it (GET /api/v1/meta when available, else the OpenAPI version).
Afterwards re-run scripts/calibrate.py; held-out results on new data are not comparable with older reports.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.general.m1_client import EXTRA_FIXTURE_PATH, M1Client  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="fetch and summarise, don't write")
    args = ap.parse_args()

    socs = list(json.loads(EXTRA_FIXTURE_PATH.read_text(encoding="utf-8"))["occupations"])
    client = M1Client(cache_dir=None)
    version = client.version()
    occupations = {}
    for soc in socs:
        profile, rows = client.profile(soc), client.requirements(soc)
        occupations[soc] = {"soc_code": soc, "title": profile["title"], "description": profile.get("description"),
                            "domain": profile.get("domain"), "job_zone": profile.get("job_zone"),
                            "total_requirements": len(rows), "requirements": rows}
        types = collections.Counter((r["item_type"], r.get("source")) for r in rows)
        print(f"{soc} {profile['title'][:40]:40} {len(rows):5} rows  {dict(types)}")
    if args.dry_run:
        return
    has_dwa = any(r["item_type"] == "dwa" for o in occupations.values() for r in o["requirements"])
    meta = {"version": version, "data_version": version, "generated_at": datetime.now(timezone.utc).isoformat(),
            "description": "Module 1 REST responses (/occupations/{soc}/profile and /requirements) for occupations "
                           "outside the 15-occupation export, for held-out calibration.",
            "data_label": f"module 1 {version}" + ("" if has_dwa else " (no DWA rows: database older than v2.1)"),
            "target_occupations_count": len(occupations)}
    EXTRA_FIXTURE_PATH.write_text(json.dumps({"metadata": meta, "occupations": occupations}, indent=1), encoding="utf-8")
    print(f"wrote {EXTRA_FIXTURE_PATH} (module 1 {version})")


if __name__ == "__main__":
    main()
