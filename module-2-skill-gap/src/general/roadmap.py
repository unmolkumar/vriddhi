"""General roadmap: missing and partial core requirements, heaviest first, with practice ideas from real work.

- Order: by weight; a taxonomy prerequisite of a later item moves ahead of it.
- Prerequisites: tech/tool/market items that resolve to v1 taxonomy ids reuse its prerequisites (those not already
  in the user's evidence).
- Practice ideas: the nearest unmet O*NET tasks/DWAs of the same occupation (cosine >= PRACTICE_MIN_SIM).
- Hours: estimated ranges. Taxonomy skills use v1's difficulty-tier hours; other items HOURS_PER_LEVEL[type] x the
  level still to reach x JOB_ZONE_FACTOR[job zone].
"""
from __future__ import annotations

import math
import re

import numpy as np

from src.engines.roadmap_generator import TIER_HOURS
from src.engines.skill_extractor import coarser_ids, taxonomy
from src.general.matcher import RequirementMatch, requirement_skill_id
from src.general.scoring import PARTIAL_CREDIT
from src.models.schemas import HourRange

ROADMAP_MAX_ITEMS = 8            # the rest go to `later`
ROADMAP_MAX_TECH = 3             # tech/tool items in the main roadmap; more go to `later`
HOURS_PER_LEVEL = {"task": 60, "dwa": 40, "market_skill": 80, "tech": 50, "tool": 20}   # hours for a full level 0 -> 1
JOB_ZONE_FACTOR = {1: 0.5, 2: 0.75, 3: 1.0, 4: 1.25, 5: 1.5}
HOURS_SPREAD = (0.7, 1.3)        # range around the estimate
HOURS_ROUND = 5
MIN_LEVEL_GAP = 0.2
PRACTICE_IDEAS = 2
PRACTICE_MIN_SIM = 0.35
PRACTICE_TYPES = ("task", "dwa")
ROADMAP_NOTE = ("Hours and weeks are estimated ranges from each requirement's type, the level still to reach and "
                "the occupation's preparation level (job zone), not guarantees. Practice ideas are real tasks from "
                "this occupation that your evidence doesn't show yet.")


def _round(x: float) -> int:
    return max(HOURS_ROUND, int(round(x / HOURS_ROUND)) * HOURS_ROUND)


def hours(m: RequirementMatch, job_zone: int | None) -> HourRange:
    sid = requirement_skill_id(m.item.name) if m.item.item_type in ("tech", "tool", "market_skill") else None
    entry = taxonomy()["by_id"].get(sid) if sid else None
    factor = 0.5 if m.status == "partial" else 1.0
    if entry and entry.get("difficulty_tier") in TIER_HOURS:
        low, high = TIER_HOURS[entry["difficulty_tier"]]
        return HourRange(low=_round(low * factor), high=_round(high * factor))
    achieved = PARTIAL_CREDIT * m.item.level if m.status == "partial" else 0.0
    gap = max(MIN_LEVEL_GAP, m.item.level - achieved)
    mid = HOURS_PER_LEVEL.get(m.item.item_type, 40) * gap * JOB_ZONE_FACTOR.get(job_zone or 3, 1.0)
    return HourRange(low=_round(mid * HOURS_SPREAD[0]), high=_round(mid * HOURS_SPREAD[1]))


def weeks(h: HourRange, hours_per_week: float | None) -> HourRange | None:
    if not hours_per_week:
        return None
    return HourRange(low=math.ceil(h.low / hours_per_week), high=math.ceil(h.high / hours_per_week))


def prerequisites(m: RequirementMatch, known: set[str]) -> list[str]:
    """Taxonomy prerequisites of the item's skill that the user's evidence doesn't already cover."""
    sid = requirement_skill_id(m.item.name) if m.item.item_type in ("tech", "tool", "market_skill") else None
    by_id = taxonomy()["by_id"]
    entry = by_id.get(sid) if sid else None
    if not entry:
        return []
    return [by_id[p]["display"] for p in entry.get("prerequisites", []) if p in by_id and p not in known]


def known_skill_ids(skill_ids: set[str]) -> set[str]:
    out = set(skill_ids)
    for s in skill_ids:
        out.update(coarser_ids(s))
    return out


def practice_ideas(gaps: list[RequirementMatch], vectors: dict[str, np.ndarray]) -> dict[str, list[str]]:
    """item_id -> the nearest other unmet tasks/DWAs (by cosine of their requirement vectors)."""
    pool = [m for m in gaps if m.item.item_type in PRACTICE_TYPES and m.item.item_id in vectors]
    if not pool:
        return {}
    pool_vecs = np.stack([vectors[m.item.item_id] for m in pool])
    out = {}
    for m in gaps:
        v = vectors.get(m.item.item_id)
        if v is None:
            continue
        sims = pool_vecs @ v
        order = [i for i in np.argsort(-sims) if pool[i].item.item_id != m.item.item_id and sims[i] >= PRACTICE_MIN_SIM]
        out[m.item.item_id] = [pool[i].item.name for i in order[:PRACTICE_IDEAS]]
    return out


def order(gaps: list[RequirementMatch], known: set[str]) -> list[RequirementMatch]:
    """By weight; an item whose taxonomy skill is a prerequisite of a heavier item moves ahead of it."""
    ranked = sorted(gaps, key=lambda m: -m.item.weight)
    sid = {m.item.item_id: requirement_skill_id(m.item.name) for m in ranked
           if m.item.item_type in ("tech", "tool", "market_skill")}
    by_id = taxonomy()["by_id"]
    out: list[RequirementMatch] = []
    placed: set[int] = set()
    for m in ranked:
        if id(m) in placed:
            continue
        pre_ids = set(by_id.get(sid.get(m.item.item_id) or "", {}).get("prerequisites", [])) - known
        if pre_ids:
            for p in ranked:
                if id(p) not in placed and sid.get(p.item.item_id) in pre_ids:
                    out.append(p)
                    placed.add(id(p))
        out.append(m)
        placed.add(id(m))
    return out


# --- A3b: ranking by expected score gain --------------------------------------------------------------------
# Generic office/productivity software: a separate `basics` list unless the occupation's market skills name it;
# at most BASICS_MAX items of at least BASICS_MIN_WEIGHT (hot or in-demand technology), the rest go to `later`.
BASICS_MAX = 4
BASICS_MIN_WEIGHT = 0.5
BASIC_SOFTWARE = re.compile(
    r"\b(microsoft (word|excel|outlook|access|powerpoint|office|windows|onenote|teams|exchange|sharepoint)|"
    r"office suite|google (docs|sheets|drive|slides|workspace)|gmail|adobe acrobat|web browser|email|e-mail|"
    r"word processing|spreadsheet software|presentation software|operating system|internet browser)\b", re.IGNORECASE)


def expected_gain(matches: list[RequirementMatch], credit) -> dict[str, float]:
    """requirement_id -> score the item would add if fully met: the type's effective share (renormalised, as in the score)
    x the item's share of its type's weight x the credit still missing."""
    from src.general.matcher import TYPE_SHARE, effective_share

    groups: dict[str, list[RequirementMatch]] = {}
    for m in matches:
        if m.item.item_type in TYPE_SHARE:
            groups.setdefault(m.item.item_type, []).append(m)
    raw = {t: effective_share(TYPE_SHARE[t], sum(m.item.weight for m in ms), sum(m.item.base_weight for m in ms), len(ms))
           for t, ms in groups.items()}
    total = sum(raw.values()) or 1.0
    out = {}
    for t, ms in groups.items():
        w = sum(m.item.weight for m in ms) or 1.0
        for m in ms:
            out[m.item.requirement_id] = raw[t] / total * m.item.weight / w * (1 - credit(m))
    return out


def is_basic_software(m: RequirementMatch, market_skill_ids: set[str], market_names: set[str]) -> bool:
    if m.item.item_type not in ("tech", "tool") or not BASIC_SOFTWARE.search(m.item.name):
        return False
    sid = requirement_skill_id(m.item.name)
    return not (sid and sid in market_skill_ids) and not any(n in m.item.name.lower() for n in market_names)


def plan(gaps: list[RequirementMatch], matches: list[RequirementMatch], credit, known: set[str],
         market_skills: list[str]) -> tuple[list[RequirementMatch], list[RequirementMatch], list[RequirementMatch]]:
    """(main, later, basics). Main: by expected gain (role-implied items after real gaps), taxonomy prerequisites
    first, at most ROADMAP_MAX_ITEMS items and ROADMAP_MAX_TECH tech/tool items. Basic office software goes to
    basics; everything else to later."""
    gain = expected_gain(matches, credit)
    ids = {requirement_skill_id(n) for n in market_skills} - {None}
    names = {n.lower() for n in market_skills}
    basics = [m for m in gaps if is_basic_software(m, ids, names)]
    rest = [m for m in gaps if not is_basic_software(m, ids, names)]
    ranked = sorted(rest, key=lambda m: (m.reason == "implied_by_role", -gain.get(m.item.requirement_id, 0.0)))
    main, later, tech = [], [], 0
    for m in ranked:
        is_tech = m.item.item_type in ("tech", "tool")
        if len(main) < ROADMAP_MAX_ITEMS and not (is_tech and tech >= ROADMAP_MAX_TECH):
            main.append(m)
            tech += is_tech
        else:
            later.append(m)
    main = order_keep_rank(main, known)
    basics.sort(key=lambda m: -gain.get(m.item.requirement_id, 0.0))
    shown = [m for m in basics if m.item.weight >= BASICS_MIN_WEIGHT][:BASICS_MAX]
    return main, later + [m for m in basics if m not in shown], shown


def order_keep_rank(items: list[RequirementMatch], known: set[str]) -> list[RequirementMatch]:
    """The given order, except a taxonomy prerequisite of a later item moves ahead of it."""
    sid = {m.item.item_id: requirement_skill_id(m.item.name) for m in items if m.item.item_type in ("tech", "tool", "market_skill")}
    by_id = taxonomy()["by_id"]
    out, placed = [], set()
    for m in items:
        if id(m) in placed:
            continue
        pre = set(by_id.get(sid.get(m.item.item_id) or "", {}).get("prerequisites", [])) - known
        for p in items:
            if pre and id(p) not in placed and sid.get(p.item.item_id) in pre:
                out.append(p)
                placed.add(id(p))
        out.append(m)
        placed.add(id(m))
    return out
