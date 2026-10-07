"""Informal shorthand in a matching copy of the evidence (A4).

Two steps, both keep the text as written (original_text) and list what changed (rewrites):
  expand()        the deterministic map in data/general/shorthand.json: literal expansions of common Indian workplace
                  abbreviations (BP -> blood pressure, MCB -> miniature circuit breaker), with context guards for
                  ambiguous ones (DB -> distribution board only when the input also mentions MCB, wiring, panel ...)
  GroqNormaliser  optional: very short informal units rewritten as a plain English phrase; batched, cached on disk,
                  fail-safe (None keeps the unit as it is). See WORKING.md section 15 for when it is on.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

MODULE_ROOT = Path(__file__).resolve().parents[2]
MAP_PATH = MODULE_ROOT / "data" / "general" / "shorthand.json"


@dataclass(frozen=True)
class Entry:
    abbr: str
    expansion: str
    pattern: re.Pattern
    near: tuple[str, ...] = ()
    not_near: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def entries() -> list[Entry]:
    data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    out = []
    for e in data["entries"]:
        flags = re.IGNORECASE if e.get("case", "any") == "any" else 0
        pattern = re.compile(rf"(?<![\w/&-]){re.escape(e['abbr'])}(s?)(?![\w/&-])", flags)
        out.append(Entry(e["abbr"], e["expansion"], pattern, tuple(e.get("near", ())), tuple(e.get("not_near", ()))))
    return out


def expand(text: str, context: str | None = None) -> tuple[str, list[str]]:
    """(text with the map's abbreviations expanded, ["BP -> blood pressure", ...]). Guards are checked against
    `context` (the whole input), so a short line still sees the rest of the description."""
    ctx = (context if context is not None else text).lower()
    rewrites = []
    for e in entries():
        if (e.near and not any(w in ctx for w in e.near)) or any(w in ctx for w in e.not_near):
            continue
        text, n = e.pattern.subn(lambda m, e=e: e.expansion + m.group(1), text)
        if n:
            rewrites.append(f"{e.abbr} -> {e.expansion}")
    return text, rewrites


# --- optional: Groq normalisation of very short informal units ---------------------------------------------
NORMALISE_MAX_WORDS = 4         # units of at most this many words are "very short"
NORMALISE_CACHE = MODULE_ROOT / "data" / "cache" / "normalised"
NORMALISE_PROMPT_VERSION = 1
NORMALISE_PROMPT = (
    "Below is someone's description of their work, then a list of short fragments from it. Rewrite each fragment as "
    "a short plain-English phrase saying what the person does, using the description for context (e.g. 'dressing' in "
    "a nurse's text -> 'changing wound dressings'). Stay literal: do not add tasks, tools or details that the "
    "fragment doesn't say. If a fragment is already clear, return it unchanged. Reply with JSON only: "
    '{"phrases": ["...", ...]} with one entry per fragment, in the same order.')


def is_very_short(text: str) -> bool:
    return len(text.split()) <= NORMALISE_MAX_WORDS


class GroqNormaliser:
    """(fragments, whole text) -> plain phrases or None each. Cached per (prompt, model, context, fragment)."""

    def __init__(self, api_key: str, cache_dir: Path | None = NORMALISE_CACHE):
        self.api_key, self.cache_dir = api_key, cache_dir

    def _file(self, fragment: str, context: str) -> Path | None:
        from src.engines.skill_extractor import llm_model

        key = hashlib.sha1(f"{NORMALISE_PROMPT_VERSION}|{llm_model()}|{context}|{fragment}".encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json" if self.cache_dir else None

    def __call__(self, fragments: list[str], context: str) -> list[str | None]:
        files = [self._file(f, context) for f in fragments]
        out = [json.loads(f.read_text(encoding="utf-8"))["phrase"] if f and f.exists() else None for f in files]
        todo = [i for i, o in enumerate(out) if o is None]
        if not todo:
            return out
        try:
            from groq import Groq

            from src.engines.skill_extractor import llm_model

            client = Groq(api_key=self.api_key, timeout=15, max_retries=0)
            resp = client.chat.completions.create(
                model=llm_model(), temperature=0, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": NORMALISE_PROMPT},
                          {"role": "user", "content": json.dumps({"description": context[:2000],
                                                                  "fragments": [fragments[i] for i in todo]},
                                                                 ensure_ascii=False)}])
            got = json.loads(resp.choices[0].message.content or "{}").get("phrases", [])
        except Exception as e:  # no network, rate limit, bad JSON: keep the units as they are
            log.warning("normalisation skipped: %s", type(e).__name__)
            return out
        if len(got) != len(todo):
            log.warning("normalisation skipped: %d answers for %d fragments", len(got), len(todo))
            return out
        for i, phrase in zip(todo, got):
            if isinstance(phrase, str) and phrase.strip():
                out[i] = phrase.strip()
                if files[i]:
                    files[i].parent.mkdir(parents=True, exist_ok=True)
                    files[i].write_text(json.dumps({"phrase": out[i]}), encoding="utf-8")
        return out


def default_normaliser() -> GroqNormaliser | None:
    """Groq when GROQ_API_KEY is set and M2_NORMALISE_SHORT is on; otherwise None."""
    from dotenv import load_dotenv

    load_dotenv()
    key = os.getenv("GROQ_API_KEY", "").strip()
    on = os.getenv("M2_NORMALISE_SHORT", "0").strip().lower() in ("1", "true", "yes")
    return GroqNormaliser(key) if key and on else None
