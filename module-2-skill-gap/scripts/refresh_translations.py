"""Translate the non-English sentences of every calibration profile with Groq and store them as a fixture.

Run from module-2-skill-gap/ with GROQ_API_KEY available (root .env, or --env path/to/.env):
  python scripts/refresh_translations.py
Writes tests/calibration/translations.json (original sentence -> English), which calibration and tests use through
FixtureTranslator so they stay offline and deterministic. Sentences Groq can't translate are left out.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from src.general.calibration import PROFILES_DIR  # noqa: E402
from src.general.evidence import from_text  # noqa: E402
from src.general.translate import FIXTURE_TRANSLATIONS, GroqTranslator  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", default=None, help=".env file to load GROQ_API_KEY from (default: search upwards)")
    args = ap.parse_args()
    load_dotenv(args.env) if args.env else load_dotenv()
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        sys.exit("GROQ_API_KEY is not set.")
    groq = GroqTranslator(key)
    table: dict[str, str] = {}

    def record(texts: list[str]) -> list[str | None]:
        out = groq(texts)
        table.update({t: e for t, e in zip(texts, out) if e})
        return out

    files = sorted(PROFILES_DIR.glob("*/*.txt"))
    for f in files:
        from_text(f.read_text(encoding="utf-8"), translator=record)
    FIXTURE_TRANSLATIONS.write_text(json.dumps(dict(sorted(table.items())), ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")
    print(f"{len(table)} sentences from {len(files)} profiles -> {FIXTURE_TRANSLATIONS}")


if __name__ == "__main__":
    main()
