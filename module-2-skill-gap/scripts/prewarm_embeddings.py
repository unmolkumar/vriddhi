"""Prepare occupations ahead of time so the first v2 request isn't slow (~20 s cold per occupation and its related ones).

Run from module-2-skill-gap/:
  python scripts/prewarm_embeddings.py 29-1141.00 47-2111.00     # these SOCs (+ related) from module 1 (M1_BASE_URL)
  python scripts/prewarm_embeddings.py --all-fixture             # every occupation in the test fixtures, offline
Requirement vectors land in data/cache/embeddings (and module 1 responses in data/cache/m1), which a running
service reads; the service can also warm itself at startup with M2_PREWARM_SOCS="29-1141.00,47-2111.00".
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.general.m1_client import EXTRA_FIXTURE_PATH, FIXTURE_PATH, FixtureM1Client, M1Client  # noqa: E402
from src.general.service import GeneralEngine  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("socs", nargs="*", help="O*NET-SOC codes")
    ap.add_argument("--all-fixture", action="store_true", help="all occupations in tests/mocks (no module 1 needed)")
    ap.add_argument("--no-related", action="store_true", help="skip the related occupations")
    args = ap.parse_args()
    if args.all_fixture:
        client = FixtureM1Client(FIXTURE_PATH, EXTRA_FIXTURE_PATH)
        socs = list(client.occupations)
    else:
        client, socs = M1Client(), args.socs
    if not socs:
        sys.exit("Give SOC codes or --all-fixture.")
    engine = GeneralEngine(client=client, translator=None)
    start = time.perf_counter()
    done = engine.prewarm(socs, with_related=not args.no_related)
    print(f"warmed {len(set(done))} occupation(s) in {time.perf_counter() - start:.1f} s")


if __name__ == "__main__":
    main()
