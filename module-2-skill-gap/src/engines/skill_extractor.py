"""Hybrid skill extraction and taxonomy normalisation, shared by resumes and job descriptions.

Pass 1 matches data/taxonomy/skills.json with symbol-aware word boundaries.
Pass 2 (optional) asks Groq for skills the dictionary missed. It never raises: no key,
timeout, rate limit or bad JSON all fall back to the pass-1 result.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path

from src.models.schemas import SkillHit

MODULE_ROOT = Path(__file__).resolve().parents[2]
TAXONOMY_PATH = MODULE_ROOT / "data" / "taxonomy" / "skills.json"
LLM_CACHE_DIR = MODULE_ROOT / "data" / "cache" / "llm_skills"
LLM_MODEL = "llama-3.3-70b-versatile"
LLM_TIMEOUT_S = 10
LLM_MAX_CHARS = 6000
PROMPT_VERSION = 1

log = logging.getLogger(__name__)

# Symbols count as part of a token so "C" doesn't match inside "C++", "C#" or "R&D".
_LEFT = r"(?<![\w+#&.])"
_RIGHT = r"(?![\w+#&])"
_SEP = r"[\s\-_]{0,2}"  # "machine learning" also matches "machine-learning" / "machinelearning"
_STRIP = re.compile(
    r"\S+@\S+|(?:https?://|www\.)\S+|\b(?:linkedin|github|gitlab|kaggle|leetcode|medium)[.\s]com\S*",
    re.IGNORECASE,
)
_ITEM_SPLIT = re.compile(r"\s*(?:[,|/;•·▪●]|\band\b|\bor\b)\s*", re.IGNORECASE)
_SENTENCE_START = set(".!?\n•*·▪●-–—")

# Replica of module 1's normalize_skill (data/scripts/02_etl_clean_load.py), so its
# top_skills ids resolve here. Replicated, not imported: modules don't import each other.
M1_ALIASES = {
    "javascript": "javascript", "js": "javascript", "node.js": "nodejs", "nodejs": "nodejs",
    "react.js": "react", "reactjs": "react", "vue.js": "vue", "vuejs": "vue",
    "python3": "python", "py": "python",
    "machine learning": "machine_learning", "ml": "machine_learning",
    "deep learning": "deep_learning", "dl": "deep_learning",
    "artificial intelligence": "ai", "a.i.": "ai", "genai": "generative_ai",
    "generative ai": "generative_ai", "large language models": "llm", "llms": "llm",
    "amazon web services": "aws", "google cloud": "gcp",
    "google cloud platform": "gcp", "microsoft azure": "azure",
    "postgres": "postgresql", "mongo": "mongodb", "k8s": "kubernetes",
    "c#": "csharp", "c++": "cpp", "golang": "go",
}


def m1_slug(skill: str) -> str:
    """Module 1's skill id for a raw skill string ('Power BI' -> 'power_bi')."""
    s = skill.lower().strip()
    cleaned = re.sub(r"[^a-z0-9_]", "_", s).strip("_")
    return M1_ALIASES.get(s, M1_ALIASES.get(cleaned, cleaned))


def _key(s: str) -> str:
    return re.sub(r"[\s\-_]+", " ", s.lower()).strip(" .")


def _alias_regex(alias: str) -> str:
    return _SEP.join(re.escape(p) for p in re.split(r"[\s\-_]+", alias) if p)


@lru_cache(maxsize=1)
def taxonomy() -> dict:
    """Load the taxonomy and precompile matchers once per process."""
    data = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    by_id, by_alias, by_slug = {}, {}, {}
    matchers = []  # (compiled, entry, rule) where rule is None, "list" or "case"
    for entry in data["skills"]:
        by_id[entry["id"]] = entry
        context = entry.get("context", {})
        for alias in [entry["display"].lower(), *entry["aliases"]]:
            by_alias.setdefault(_key(alias), entry)
            by_slug.setdefault(m1_slug(alias), entry)
        plain = [a for a in entry["aliases"] if a not in context]
        if plain:
            alts = "|".join(_alias_regex(a) for a in sorted(plain, key=len, reverse=True))
            matchers.append((re.compile(f"{_LEFT}(?:{alts}){_RIGHT}", re.IGNORECASE), entry, None))
        for alias, rule in context.items():
            matchers.append((re.compile(f"{_LEFT}{_alias_regex(alias)}{_RIGHT}", re.IGNORECASE), entry, rule))
    return {"skills": data["skills"], "by_id": by_id, "by_alias": by_alias, "by_slug": by_slug, "matchers": matchers}


def resolve_skill(name: str) -> dict | None:
    """Taxonomy entry for an id, display name, alias or module-1 id; None if unknown.

    'machine_learning', 'ML', 'm.l.', 'apache_spark' and 'Postgres' all resolve.
    """
    tax = taxonomy()
    collapsed = re.sub(r"_+", "_", m1_slug(name))
    return (tax["by_id"].get(name) or tax["by_slug"].get(m1_slug(name)) or tax["by_id"].get(collapsed)
            or tax["by_alias"].get(_key(name)))


def coarser_ids(skill_id: str) -> list[str]:
    """maps_to chain upwards: 'amazon_s3' -> ['aws', 'cloud']."""
    by_id, out = taxonomy()["by_id"], []
    cur = by_id.get(skill_id, {}).get("maps_to")
    while cur and cur not in out:
        out.append(cur)
        cur = by_id.get(cur, {}).get("maps_to")
    return out


def _mid_sentence(text: str, start: int) -> bool:
    i = start - 1
    while i >= 0 and text[i] in " \t":
        i -= 1
    return i >= 0 and text[i] not in _SENTENCE_START


def _is_list_item(text: str, start: int, end: int, skills_context: bool) -> bool:
    """True when the match is a whole item of a delimited list ('Python, Go, SQL')."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start:len(text) if line_end == -1 else line_end]
    colon = line.rfind(":", 0, start - line_start)
    segment = line[colon + 1:] if colon != -1 else line
    items = [i.strip(" \t()[]*-") for i in _ITEM_SPLIT.split(segment)]
    items = [i for i in items if i]
    if len(items) < 2 and not skills_context:
        return False
    target = _key(text[start:end])
    return any(_key(i) == target for i in items)


def _context_ok(rule: str | None, text: str, start: int, end: int, skills_context: bool) -> bool:
    if rule is None:
        return True
    if _is_list_item(text, start, end, skills_context):
        return True
    surface = text[start:end]
    return rule == "case" and any(c.isupper() for c in surface) and _mid_sentence(text, start)


def _hit(entry: dict, source: str, surface: str) -> SkillHit:
    return SkillHit(id=entry["id"], display=entry["display"], category=entry["category"],
                    in_taxonomy=True, source=source, matches=[surface])


def _dictionary_pass(text: str, skills_context: bool) -> dict[str, SkillHit]:
    spans = []
    for rx, entry, rule in taxonomy()["matchers"]:
        for m in rx.finditer(text):
            if _context_ok(rule, text, m.start(), m.end(), skills_context):
                spans.append((m.start(), m.end(), entry))
    # Longest match wins on overlap: "React Native" over "React", "Spring Boot" over "Spring".
    spans.sort(key=lambda s: (s[0] - s[1], s[0]))
    taken: list[tuple[int, int]] = []
    hits: dict[str, SkillHit] = {}
    for start, end, entry in spans:
        if any(start < e and s < end for s, e in taken):
            continue
        taken.append((start, end))
        surface = text[start:end]
        if entry["id"] in hits:
            if surface not in hits[entry["id"]].matches:
                hits[entry["id"]].matches.append(surface)
        else:
            hits[entry["id"]] = _hit(entry, "dictionary", surface)
    return hits


def _call_llm(text: str, found: list[str], api_key: str) -> list[str]:
    from groq import Groq  # lazy: the dictionary pass needs no network dependency

    client = Groq(api_key=api_key, timeout=LLM_TIMEOUT_S, max_retries=0)
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": (
                "You extract technical skills from job descriptions and resumes. "
                'Reply with JSON only: {"skills": ["..."]}. Include concrete technical skills, '
                "tools, technologies and methods that appear in the text. Exclude soft skills, "
                "job titles, company names, degrees and anything not in the text. Use short names.")},
            {"role": "user", "content": (
                f"Already found: {', '.join(found) or 'none'}.\n"
                f"List the other technical skills in this text.\n\nTEXT:\n{text[:LLM_MAX_CHARS]}")},
        ],
    )
    skills = json.loads(resp.choices[0].message.content or "{}").get("skills", [])
    return [s for s in skills if isinstance(s, str)]


def _llm_names(text: str, found: list[str]) -> list[str]:
    """Raw skill names from Groq, cached on disk by text hash. [] on any failure."""
    from dotenv import load_dotenv

    load_dotenv()
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return []
    digest = hashlib.sha1(f"{PROMPT_VERSION}|{LLM_MODEL}|{text}".encode("utf-8")).hexdigest()
    cache_file = LLM_CACHE_DIR / f"{digest}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    try:
        names = _call_llm(text, found, api_key)
    except Exception as e:  # timeout, rate limit, auth, bad JSON: never break extraction
        log.warning("LLM skill pass skipped: %s", type(e).__name__)
        return []
    LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(names), encoding="utf-8")
    return names


def _find_surface(text: str, candidates: list[str]) -> str | None:
    for c in sorted(candidates, key=len, reverse=True):
        m = re.search(f"{_LEFT}{_alias_regex(c)}{_RIGHT}", text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def extract_skills(text: str, use_llm: bool = True, skills_context: bool = False) -> list[SkillHit]:
    """Skills in `text`, normalised to taxonomy ids, sorted by display name.

    skills_context=True means the text is a skills list (resume Skills section, typed input),
    so ambiguous aliases like "Go" or "C" may match as single items.
    """
    text = _STRIP.sub(" ", text or "")
    hits = _dictionary_pass(text, skills_context)
    if use_llm and text.strip():
        for raw in _llm_names(text, sorted(h.display for h in hits.values())):
            name = raw.strip()[:60]
            if not name or name.isdigit():
                continue
            entry = resolve_skill(name)
            skill_id = entry["id"] if entry else re.sub(r"_+", "_", m1_slug(name))
            if not skill_id or skill_id in hits:
                continue
            # Keep only skills actually present in the text, so the LLM can't invent any.
            surface = _find_surface(text, (entry["aliases"] if entry else []) + [name])
            if surface is None:
                continue
            hits[skill_id] = _hit(entry, "llm", surface) if entry else SkillHit(
                id=skill_id, display=name, category=None, in_taxonomy=False, source="llm", matches=[surface])
    return sorted(hits.values(), key=lambda h: h.display.casefold())
