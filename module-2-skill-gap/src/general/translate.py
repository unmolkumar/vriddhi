"""English rewrites of non-English evidence (Hinglish, Hindi in Devanagari) for matching with an English model.

GroqTranslator: one batched call per request (GROQ_API_KEY, GROQ_MODEL), cached on disk by text hash; fail-safe:
any failure returns None for that text and the caller matches it as written, with a warning.
FixtureTranslator: committed rewrites (tests/calibration/translations.json) so calibration and tests are offline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from src.engines.skill_extractor import llm_model

log = logging.getLogger(__name__)

MODULE_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = MODULE_ROOT / "data" / "cache" / "translations"
FIXTURE_TRANSLATIONS = MODULE_ROOT / "tests" / "calibration" / "translations.json"
TIMEOUT_S = 15
MAX_BATCH = 40
PROMPT_VERSION = 1
SYSTEM_PROMPT = (
    "You rewrite short resume or work-description sentences written in Hinglish (romanised Hindi mixed with "
    "English) or Hindi into plain English. Keep the meaning exactly; do not add, remove or embellish anything; keep "
    "names, numbers, tools and technical terms as they are. Reply with JSON only: "
    '{"translations": ["...", ...]} with one entry per input, in the same order.')


def _key(text: str) -> str:
    return hashlib.sha1(f"{PROMPT_VERSION}|{llm_model()}|{text}".encode("utf-8")).hexdigest()


class GroqTranslator:
    def __init__(self, api_key: str, cache_dir: Path | None = CACHE_DIR):
        self.api_key, self.cache_dir = api_key, cache_dir

    def _cached(self, text: str) -> str | None:
        f = self.cache_dir / f"{_key(text)}.json" if self.cache_dir else None
        return json.loads(f.read_text(encoding="utf-8"))["en"] if f and f.exists() else None

    def _store(self, text: str, english: str) -> None:
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{_key(text)}.json").write_text(json.dumps({"en": english}), encoding="utf-8")

    def __call__(self, texts: list[str]) -> list[str | None]:
        out = [self._cached(t) for t in texts]
        todo = [i for i, t in enumerate(out) if t is None][:MAX_BATCH]
        if not todo:
            return out
        try:
            from groq import Groq

            client = Groq(api_key=self.api_key, timeout=TIMEOUT_S, max_retries=0)
            resp = client.chat.completions.create(
                model=llm_model(), temperature=0, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": json.dumps({"sentences": [texts[i] for i in todo]},
                                                                 ensure_ascii=False)}])
            got = json.loads(resp.choices[0].message.content or "{}").get("translations", [])
        except Exception as e:  # timeout, rate limit, auth, bad JSON: keep the originals
            log.warning("translation skipped: %s", type(e).__name__)
            return out
        if len(got) != len(todo):
            log.warning("translation skipped: %d answers for %d sentences", len(got), len(todo))
            return out
        for i, english in zip(todo, got):
            if isinstance(english, str) and english.strip():
                out[i] = english.strip()
                self._store(texts[i], out[i])
        return out


class FixtureTranslator:
    """Committed rewrites, text -> English; unknown texts return None."""

    def __init__(self, path: Path = FIXTURE_TRANSLATIONS):
        self.table = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def __call__(self, texts: list[str]) -> list[str | None]:
        return [self.table.get(t) for t in texts]


def default_translator() -> GroqTranslator | None:
    """Groq when GROQ_API_KEY is set; otherwise None (non-English sentences are matched as written, with a warning)."""
    from dotenv import load_dotenv

    load_dotenv()
    key = os.getenv("GROQ_API_KEY", "").strip()
    return GroqTranslator(key) if key else None
