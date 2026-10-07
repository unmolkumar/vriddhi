"""Capture module 1's live search results for the past job titles in the calibration profiles.

Run from module-2-skill-gap/ with module 1 up (M1_BASE_URL, default http://localhost:8001):
  python scripts/capture_title_search.py      # rewrite tests/mocks/m1_title_search.json
The fixture client answers these titles from the file (offline), so role history and the verdict's other_role
signal behave in calibration and tests as they do against a live module 1.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.general.calibration import PROFILES_DIR  # noqa: E402
from src.general.evidence import role_history  # noqa: E402
from src.general.m1_client import TITLE_SEARCH_PATH, M1Client  # noqa: E402

K = 3


def main() -> None:
    titles = sorted({t.strip().lower() for f in PROFILES_DIR.glob("*/*.txt")
                     for t, _ in role_history(f.read_text(encoding="utf-8")) if t.strip()})
    client = M1Client()
    searches = {t: [m.model_dump() for m in client.search(t, K).matches] for t in titles}
    out = {"metadata": {"m1_version": client.version(), "captured_at": datetime.now(timezone.utc).isoformat(),
                        "k": K, "titles": len(titles)}, "searches": searches}
    TITLE_SEARCH_PATH.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(titles)} titles from module 1 {out['metadata']['m1_version']} -> {TITLE_SEARCH_PATH}")


if __name__ == "__main__":
    main()
