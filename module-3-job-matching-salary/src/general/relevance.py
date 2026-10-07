"""Query building for any occupation, and how relevant each listing is to the target occupation.

Queries (at most MAX_QUERIES per city, deduplicated): the user's phrase, the O*NET title as a job-board phrase
('Chefs and Head Cooks' -> 'chef'), and module 1's best alias / alternate title for the target SOC.

Listing relevance. Designed on the tuning searches only (WORKING.md §15): module 1 resolves most unfamiliar
listing titles by token match at 0.70 ('Senior Manager' -> Spa Managers), so only resolutions at
>= RESOLVE_MIN_CONFIDENCE count, and the title's own words carry the rest:
  on_target   resolves confidently to the target or a close related SOC, or contains the target's head word
              ('ICU Staff Nurse' for staff nurse)
  adjacent    resolves confidently to another related SOC, or shares a word stem with the head word
              ('Engineer - Electrical' for electrician)
  off_target  neither: resolves confidently to something unrelated, or only weakly / not at all. Dropped, with
              the reason kept
The word rules come before 'confidently elsewhere', which guards against misresolutions ('teller' -> Cashiers).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.general.m1 import Match

MAX_QUERIES = 3
KEEP_PLURAL = frozenset({"sales", "news", "series", "species", "goods", "operations", "accounts"})
ALIAS_METHODS = ("india_alias", "onet_alt_title")
ALIAS_MAX_WORDS = 4
# Tuning searches only (staff nurse - Pune, electrician - Delhi NCR, data scientist - Bengaluru).
RESOLVE_MIN_CONFIDENCE = 0.8         # module 1 title matches below this are token-match noise
MIN_STEM = 4                         # word stems compared over at least this many letters
CLOSE_RELATED_TOP = 5                # module 1's first related occupations (by index_val) when tiers are missing
CLOSE_TIERS = {"Primary-Short", "Primary-Long"}
STOPWORDS = frozenset("""
a an and the of for in at to with on by or senior junior jr sr lead head chief assistant associate trainee intern
executive officer manager specialist staff required urgent hiring opening job jobs vacancy wanted needed immediate
joiner joiners male female fresher freshers experienced level grade i ii iii iv india pvt ltd limited private
""".split())


def singular(word: str) -> str:
    w = word.lower()
    if w in KEEP_PLURAL or w.endswith(("ss", "us", "is", "ics")):
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def board_title(onet_title: str) -> str:
    """'Chefs and Head Cooks' -> 'chef'; 'Heavy and Tractor-Trailer Truck Drivers' -> 'tractor-trailer truck driver';
    'Secondary School Teachers, Except ...' -> 'secondary school teacher'."""
    t = re.sub(r"\s*--\s*", " ", onet_title.split(",")[0]).strip()
    parts = [p.strip() for p in re.split(r"\s+and\s+", t) if p.strip()]
    if len(parts) > 1:
        t = parts[0] if parts[0].split()[-1].lower().endswith("s") else parts[-1]
    return " ".join(singular(w) for w in t.split())


def build_queries(phrase: str | None, onet_title: str | None, alias_matches: list[Match], soc: str | None) -> list[str]:
    """User phrase, O*NET board title, best alias for the SOC; lower-case, deduplicated, at most MAX_QUERIES."""
    out = [q for q in (phrase, board_title(onet_title) if onet_title else None) if q]
    aliases = sorted((m for m in alias_matches if m.soc_code == soc and m.matched_term and m.method
                      and m.method.startswith(ALIAS_METHODS) and len(m.matched_term.split()) <= ALIAS_MAX_WORDS),
                     key=lambda m: -m.confidence)
    out += [m.matched_term for m in aliases]
    seen, queries = set(), []
    for q in out:
        key = " ".join(singular(w) for w in re.findall(r"[a-z0-9]+", q.lower()))
        if key and key not in seen:
            seen.add(key)
            queries.append(re.sub(r"\s+", " ", q.strip().lower()))
    return queries[:MAX_QUERIES]


def words(text: str) -> set[str]:
    return {singular(w) for w in re.findall(r"[a-z]+", (text or "").lower())} - STOPWORDS


def close_socs(soc: str, related_rows: list[dict]) -> set[str]:
    """The target and its close related occupations: real tiers when module 1 sends any, else its first few."""
    if any(r.get("relatedness_tier") for r in related_rows):
        close = {r["related_soc_code"] for r in related_rows if r.get("relatedness_tier") in CLOSE_TIERS}
    else:
        ordered = sorted(related_rows, key=lambda r: r.get("index_val") or float("inf"))
        close = {r["related_soc_code"] for r in ordered[:CLOSE_RELATED_TOP]}
    return close | {soc}


@dataclass
class Relevance:
    label: str                  # on_target | adjacent | off_target | unknown
    soc_code: str | None
    title: str | None
    confidence: float
    reason: str


def stem(word: str) -> str:
    return word[:max(MIN_STEM, len(word) - 3)]


def stem_related(a: str, b: str) -> bool:
    """'electrical' ~ 'electrician', 'nursing' ~ 'nurse', 'science' ~ 'scientist' (shared leading stem)."""
    sa, sb = stem(a), stem(b)
    return sa.startswith(sb) or sb.startswith(sa)


def classify(listing_title: str, matches: list[Match], target: str | None, close: set[str], related: set[str],
             target_terms: set[str]) -> Relevance:
    """See the module docstring. target None (module 1 down or unresolved): 'unknown' for every listing."""
    confident = next((m for m in matches if m.confidence >= RESOLVE_MIN_CONFIDENCE), None)
    top = matches[0] if matches else None
    if target is None:
        return Relevance("unknown", top.soc_code if top else None, top.title if top else None,
                         top.confidence if top else 0.0, "No target occupation to compare with.")
    title_words = words(listing_title)
    exact = title_words & target_terms
    stems = {w for w in title_words for t in target_terms if w not in exact and stem_related(w, t)}
    if confident and confident.soc_code in close:
        near = "" if confident.soc_code == target else " (a close related occupation)"
        return Relevance("on_target", confident.soc_code, confident.title, confident.confidence,
                         f"Title resolves to {confident.title}{near}.")
    if exact:
        return Relevance("on_target", target, None, 1.0, f"Title contains '{sorted(exact)[0]}'.")
    if confident and confident.soc_code in related:
        return Relevance("adjacent", confident.soc_code, confident.title, confident.confidence,
                         f"Title resolves to {confident.title}, a related occupation.")
    if stems:
        return Relevance("adjacent", confident.soc_code if confident else None, confident.title if confident else None,
                         confident.confidence if confident else 0.0,
                         f"Title shares a word stem with the role ('{sorted(stems)[0]}').")
    if confident:
        return Relevance("off_target", confident.soc_code, confident.title, confident.confidence,
                         f"Title resolves to {confident.title} ({confident.confidence:.2f}), unrelated to the role.")
    weak = f"only weakly ({top.title}, {top.confidence:.2f})" if top else "not at all"
    return Relevance("off_target", top.soc_code if top else None, top.title if top else None,
                     top.confidence if top else 0.0, f"Title shares no word with the role and resolves {weak}.")


def target_terms(queries: list[str]) -> set[str]:
    """Each query's head word (its last non-generic word): 'staff nurse' -> nurse, 'data scientist' -> scientist.
    Head words only, so 'data' doesn't pull in Data Entry Operators."""
    out = set()
    for q in queries:
        ws = [singular(w) for w in re.findall(r"[a-z]+", q.lower()) if singular(w) not in STOPWORDS]
        if ws:
            out.add(ws[-1])
    return out
