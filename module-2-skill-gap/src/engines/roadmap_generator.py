"""Gap matrix -> prerequisite-ordered learning roadmap with estimated hour ranges."""
from __future__ import annotations

import heapq
import math

from src.engines.skill_extractor import coarser_ids, taxonomy
from src.models.schemas import HourRange, Milestone, Roadmap, SkillGap, UserProfile

# Estimated learning hours by taxonomy difficulty tier (low, high). Estimates, not promises.
TIER_HOURS = {1: (10, 25), 2: (30, 60), 3: (60, 120)}
DEFAULT_TIER = 2                 # skills outside the taxonomy
KIND_HOURS_FACTOR = {            # adjacent / weak skills start from related or partial knowledge
    "missing": 1.0, "prerequisite": 1.0, "adjacent": 0.5, "weak": 0.5,
}
MAX_MILESTONES = 15
ROADMAP_NOTE = ("Hours and weeks are estimated ranges based on each skill's difficulty tier, not guarantees. "
                "Actual time depends on your background and how you practise.")


def known_ids(profile: UserProfile) -> set[str]:
    """Skills the user has, plus everything they imply via maps_to (PostgreSQL implies SQL)."""
    known = set()
    for s in profile.skills:
        known.add(s.name)
        known.update(coarser_ids(s.name))
    return known


def build_roadmap(gaps: list[SkillGap], profile: UserProfile, hours_per_week: float | None = None) -> Roadmap:
    by_id = taxonomy()["by_id"]
    known = known_ids(profile)
    items: dict[str, dict] = {}
    for g in gaps:
        if g.status == "missing":
            kind, reason = "missing", f"Asked for by the role ({g.priority.lower()} priority)."
        elif g.status == "adjacent":
            kind, reason = "adjacent", f"{g.display} {g.relation}, which you know."
        elif g.gap > 0:
            kind, reason = "weak", f"Raise from level {g.current_level} to {g.required_level}."
        else:
            continue
        items[g.skill] = {"kind": kind, "display": g.display, "importance": g.importance, "reason": reason,
                          "required_for": []}

    # Pull in prerequisites the user lacks, recursively (taxonomy prerequisites are acyclic).
    stack = [sid for sid, it in items.items() if it["kind"] in ("missing", "adjacent")]
    while stack:
        sid = stack.pop()
        for pre in by_id.get(sid, {}).get("prerequisites", []):
            if pre in known:
                continue
            if pre not in items:
                items[pre] = {"kind": "prerequisite", "display": by_id[pre]["display"], "importance": 0.0,
                              "reason": "", "required_for": []}
                stack.append(pre)
            if items[pre]["kind"] == "prerequisite":
                items[pre]["importance"] = max(items[pre]["importance"], items[sid]["importance"])
                if sid not in items[pre]["required_for"]:
                    items[pre]["required_for"].append(sid)
    for it in items.values():
        if it["kind"] == "prerequisite":
            it["reason"] = "Prerequisite for " + ", ".join(items[s]["display"] for s in it["required_for"]) + "."

    # Topological order (Kahn); among ready skills, the most important first.
    deps = {sid: [p for p in by_id.get(sid, {}).get("prerequisites", []) if p in items] for sid in items}
    waiting = {sid: len(d) for sid, d in deps.items()}
    dependants: dict[str, list[str]] = {sid: [] for sid in items}
    for sid, d in deps.items():
        for p in d:
            dependants[p].append(sid)
    position = {sid: i for i, sid in enumerate(items)}
    ready = [(-items[s]["importance"], position[s], s) for s, n in waiting.items() if n == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        _, _, sid = heapq.heappop(ready)
        order.append(sid)
        for d in dependants[sid]:
            waiting[d] -= 1
            if waiting[d] == 0:
                heapq.heappush(ready, (-items[d]["importance"], position[d], d))
    assert len(order) == len(items), "prerequisite cycle"  # guarded by the taxonomy tests

    milestones, low_total, high_total = [], 0, 0
    for n, sid in enumerate(order[:MAX_MILESTONES], start=1):
        it = items[sid]
        tier = by_id.get(sid, {}).get("difficulty_tier", DEFAULT_TIER)
        lo, hi = (round(h * KIND_HOURS_FACTOR[it["kind"]]) for h in TIER_HOURS[tier])
        low_total, high_total = low_total + lo, high_total + hi
        weeks = (HourRange(low=math.ceil(low_total / hours_per_week), high=math.ceil(high_total / hours_per_week))
                 if hours_per_week else None)
        milestones.append(Milestone(
            order=n, skill=sid, display=it["display"], kind=it["kind"], reason=it["reason"],
            importance=round(it["importance"], 3), difficulty_tier=tier, prerequisites=deps[sid],
            required_for=it["required_for"], estimated_hours=HourRange(low=lo, high=hi), estimated_weeks=weeks))
    note = ROADMAP_NOTE
    if len(order) > MAX_MILESTONES:
        note += f" {len(order) - MAX_MILESTONES} further skills omitted; finish these first."
    total = HourRange(low=low_total, high=high_total)
    return Roadmap(
        milestones=milestones, total_estimated_hours=total, hours_per_week=hours_per_week,
        estimated_total_weeks=(HourRange(low=math.ceil(low_total / hours_per_week), high=math.ceil(high_total / hours_per_week))
                               if hours_per_week else None),
        note=note)
