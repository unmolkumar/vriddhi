"""Module 1 target (required skills) vs a UserProfile -> gap matrix, match score, verdict, roadmap.

Formulas are in WORKING.md §5; every weight and threshold is a named constant here.
"""
from __future__ import annotations

import re

from src.engines import similarity
from src.engines.profile_builder import profile_from_manual
from src.engines.roadmap_generator import build_roadmap, known_ids
from src.engines.skill_extractor import coarser_ids, m1_slug, resolve_skill
from src.models.schemas import (
    ExperienceRange, ExtractedSkill, GapAnalysisRequest, GapAnalysisResult, ScoreBreakdown, SkillBuckets, SkillGap,
    UserProfile,
)

# Importance (priority: skill_importance -> knowledge_graph -> rank decay)
RANK_DECAY = 0.15                     # w_i = 1 / (1 + RANK_DECAY * i), i = position in required_skills
TOP_RANKED = 3                        # without explicit importance, the first 3 skills need level 3 ...
TOP_REQUIRED_LEVEL = 3
DEFAULT_REQUIRED_LEVEL = 2            # ... and the rest level 2
IMPORTANCE_TO_LEVEL = [(0.85, 4), (0.70, 3)]  # explicit importance -> required level, else DEFAULT
PRIORITY_HIGH = 0.80
PRIORITY_MEDIUM = 0.60

# Score
ADJACENT_CREDIT = 0.40                # partial credit for an adjacent skill
COVERAGE_WEIGHT = 0.85
EXPERIENCE_WEIGHT = 0.15
EXPERIENCE_SMOOTHING = 1.0            # experience_factor = min(1, (years + s) / (role_min + s))

# Verdict
UNDER_SKILLED_BELOW = 0.60
OVER_QUALIFIED_SCORE = 0.85
STRONG_LEVEL = 3                      # level 3+ = work-supported or better
OVER_EXTRA_STRONG = 3                 # strong skills the role doesn't use

# Typical experience when the request doesn't give one, from the title's seniority words.
SENIORITY_RANGES = [
    (r"\b(intern|trainee|junior|jr|graduate|fresher|entry)\b", (0.0, 2.0)),
    (r"\b(principal|staff|head|director|architect)\b", (8.0, 15.0)),
    (r"\b(lead|manager)\b", (6.0, 12.0)),
    (r"\b(senior|sr)\b", (4.0, 8.0)),
]
DEFAULT_EXPERIENCE_RANGE = (0.0, 5.0)
SENIORITY_LADDER = ["", "Senior", "Lead", "Principal"]
_SENIORITY_WORD = re.compile(r"^\s*(senior|sr\.?|lead|principal)\s+", re.IGNORECASE)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _resolve(raw: str) -> tuple[str, str, dict | None]:
    entry = resolve_skill(raw)
    if entry:
        return entry["id"], entry["display"], entry
    sid = re.sub(r"_+", "_", m1_slug(raw)) or raw
    return sid, sid.replace("_", " ").title(), None


def _kg_importance(kg: dict, warnings: list[str]) -> dict[str, float]:
    """Importance per taxonomy id from module 1 knowledge-graph skill/technology nodes."""
    out: dict[str, float] = {}
    nodes = kg.get("nodes") if isinstance(kg, dict) else None
    if not isinstance(nodes, list):
        warnings.append("knowledge_graph has no 'nodes' list; ignored.")
        return out
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") not in ("skill", "technology"):
            continue
        for name in (node.get("label", ""), re.sub(r"^(skill|tech)_", "", str(node.get("id", "")))):
            if name:
                sid = _resolve(name)[0]
                out[sid] = max(out.get(sid, 0.0), _clamp(node.get("weight", 0.0)))
    return out


def _importance(ids: list[str], req: GapAnalysisRequest, warnings: list[str]) -> tuple[list[float], list[bool], str]:
    explicit: dict[str, float] = {}
    source = "rank_decay"
    if req.skill_importance:
        explicit = {_resolve(k)[0]: _clamp(v) for k, v in req.skill_importance.items()}
        source = "skill_importance"
    elif req.knowledge_graph:
        explicit = _kg_importance(req.knowledge_graph, warnings)
        source = "knowledge_graph"
    has = [sid in explicit for sid in ids]
    if not any(has):
        source = "rank_decay"
    weights = [explicit[sid] if h else 1 / (1 + RANK_DECAY * i) for i, (sid, h) in enumerate(zip(ids, has))]
    return weights, has, source


def _required_level(rank: int, importance: float, explicit: bool) -> int:
    if explicit:
        return next((lvl for cut, lvl in IMPORTANCE_TO_LEVEL if importance >= cut), DEFAULT_REQUIRED_LEVEL)
    return TOP_REQUIRED_LEVEL if rank < TOP_RANKED else DEFAULT_REQUIRED_LEVEL


def _priority(importance: float) -> str:
    return "High" if importance >= PRIORITY_HIGH else "Medium" if importance >= PRIORITY_MEDIUM else "Low"


def typical_experience(target_role: str) -> tuple[float, float]:
    for pattern, rng in SENIORITY_RANGES:
        if re.search(pattern, target_role, re.IGNORECASE):
            return rng
    return DEFAULT_EXPERIENCE_RANGE


def next_level_role(target_role: str) -> str | None:
    """'Data Analyst' -> 'Senior Data Analyst' -> 'Lead Data Analyst' -> 'Principal Data Analyst' -> None."""
    m = _SENIORITY_WORD.match(target_role)
    word = (m.group(1).rstrip(".").lower() if m else "")
    word = "senior" if word == "sr" else word
    base = target_role[m.end():] if m else target_role.strip()
    idx = [w.lower() for w in SENIORITY_LADDER].index(word)
    return f"{SENIORITY_LADDER[idx + 1]} {base}" if idx + 1 < len(SENIORITY_LADDER) else None


def _best(skills: list[ExtractedSkill]) -> ExtractedSkill | None:
    return max(skills, key=lambda s: (s.level, s.confidence), default=None)


def _match(sid: str, entry: dict | None, profile: UserProfile) -> tuple[str, str | None, ExtractedSkill | None]:
    """(status, reason, via) without the semantic step."""
    user = {s.name: s for s in profile.skills}
    if sid in user:
        return "matched", "exact", user[sid]
    finer = _best([s for s in profile.skills if sid in coarser_ids(s.name)])
    if finer:                                   # PostgreSQL covers SQL, AWS covers cloud
        return "matched", "maps_to", finer
    parent = entry.get("maps_to") if entry else None
    if parent:                                  # sibling (MySQL for PostgreSQL) or broader (SQL for PostgreSQL)
        related = _best([s for s in profile.skills if parent == s.name or parent in coarser_ids(s.name)])
        if related:
            return "adjacent", "maps_to", related
    prereqs = set(entry.get("prerequisites", [])) if entry else set()
    providers = [s for s in profile.skills if s.name in prereqs or prereqs & set(coarser_ids(s.name))]
    builds_on = [s for s in profile.skills if sid in (resolve_skill(s.name) or {}).get("prerequisites", [])]
    related = _best(providers + builds_on)
    if related:                                 # knows what it builds on, or something built on it
        return "adjacent", "prerequisite", related
    return "missing", None, None


def _advice(gap: SkillGap, via: ExtractedSkill | None) -> str | None:
    if via is None:
        return None
    if via.needs_verification:
        return (f"You rate yourself level {via.claimed_level} in {via.display}, but your resume doesn't show it yet. "
                f"A project or certificate with {gap.display} would back that up.")
    if not ({"work_supported", "project_supported"} & set(via.evidence)):
        return f"You list {via.display}, but nothing in your work or projects shows it. Build a project with {gap.display}."
    if gap.status == "adjacent":
        return f"You already know {via.display}; {gap.display} builds on it, so this is a quick win."
    return None


def analyze_gap(req: GapAnalysisRequest) -> GapAnalysisResult:
    warnings: list[str] = []
    profile = req.profile or profile_from_manual(req.manual_profile)

    required: list[tuple[str, str, dict | None]] = []
    for raw in req.required_skills:
        resolved = _resolve(raw)
        if any(resolved[0] == r[0] for r in required):
            warnings.append(f"'{raw}' duplicates an earlier required skill; counted once.")
            continue
        required.append(resolved)
        if resolved[2] is None:
            warnings.append(f"'{raw}' is not in the skill taxonomy; matched by name and similarity only.")
    ids = [sid for sid, _, _ in required]
    weights, explicit, importance_source = _importance(ids, req, warnings)

    rows = []
    for rank, ((sid, display, entry), w, has_w) in enumerate(zip(required, weights, explicit)):
        status, reason, via = _match(sid, entry, profile)
        rows.append({"sid": sid, "display": display, "entry": entry, "w": w, "status": status, "reason": reason,
                     "via": via, "sim": None, "req_level": _required_level(rank, w, has_w)})

    # Semantic step for what is still missing: MiniLM (or TF-IDF) cosine against the user's skill names.
    missing = [r for r in rows if r["status"] == "missing"]
    if missing and profile.skills:
        matrix = similarity.similarity_matrix([r["display"] for r in missing], [s.display for s in profile.skills])
        for r, sims in zip(missing, matrix):
            best = max(range(len(sims)), key=sims.__getitem__)
            if sims[best] >= similarity.threshold():
                r.update(status="adjacent", reason="semantic", via=profile.skills[best], sim=round(sims[best], 3))

    gaps, credit, total = [], 0.0, 0.0
    for r in rows:
        via = r["via"]
        current = via.level if via else 0
        gap = SkillGap(
            skill=r["sid"], display=r["display"], in_taxonomy=r["entry"] is not None, status=r["status"],
            reason=r["reason"], via=via.name if via else None, via_display=via.display if via else None,
            similarity=r["sim"], importance=round(r["w"], 4), priority=_priority(r["w"]),
            required_level=r["req_level"], current_level=current, gap=r["req_level"] - current,
            evidence=via.evidence if via else [])
        gap.advice = _advice(gap, via)
        gaps.append(gap)
        level_ratio = min(1.0, current / r["req_level"]) if r["req_level"] else 1.0
        credit += r["w"] * (level_ratio if r["status"] == "matched" else ADJACENT_CREDIT if r["status"] == "adjacent" else 0.0)
        total += r["w"]

    coverage = credit / total if total else 0.0
    lo, hi = ((req.typical_experience_years.min, req.typical_experience_years.max) if req.typical_experience_years
              else typical_experience(req.target_role))
    years = profile.experience_years
    exp_factor = min(1.0, (years + EXPERIENCE_SMOOTHING) / (lo + EXPERIENCE_SMOOTHING))
    score = round(COVERAGE_WEIGHT * coverage + EXPERIENCE_WEIGHT * exp_factor, 4)

    roadmap = build_roadmap(gaps, profile, req.hours_per_week)
    used = set(ids) | {g.via for g in gaps if g.via}
    unused_strong = [s.display for s in profile.skills if s.level >= STRONG_LEVEL and s.name not in used]
    pct = round(score * 100)
    critical = sorted((g for g in gaps if g.status == "missing"), key=lambda g: -g.importance)
    suggested = None
    if score >= OVER_QUALIFIED_SCORE and years > hi and len(unused_strong) >= OVER_EXTRA_STRONG:
        verdict = "over_qualified"
        suggested = next_level_role(req.target_role)
        message = (f"You're over-qualified for {req.target_role}: an estimated {pct}% match, {years:g} years against a typical "
                   f"{lo:g}-{hi:g}, and strong skills the role doesn't use ({', '.join(unused_strong[:4])}). "
                   + (f"Consider {suggested} roles." if suggested else "Consider a more senior role."))
    elif score < UNDER_SKILLED_BELOW:
        verdict = "under_skilled"
        todo = [m.display for m in roadmap.milestones if m.kind != "weak"][:3] or [g.display for g in critical[:3]]
        message = (f"You cover an estimated {pct}% of what {req.target_role} asks for. "
                   + (f"You're yet to learn {', '.join(todo)}." if todo else "Strengthen the skills below."))
    else:
        verdict = "good_fit"
        message = f"You're a good fit for {req.target_role} (estimated {pct}% match). Apply now"
        message += (f", and close {', '.join(g.display for g in critical[:2])} to strengthen your profile."
                    if critical else ".")

    matched = [g for g in gaps if g.status == "matched"]
    by_importance = lambda gs: [g.skill for g in sorted(gs, key=lambda g: -g.importance)]
    return GapAnalysisResult(
        target_role=req.target_role, match_score=score, match_percent=pct, verdict=verdict, verdict_message=message,
        suggested_role=suggested,
        score_breakdown=ScoreBreakdown(
            coverage=round(coverage, 4), experience_factor=round(exp_factor, 4), coverage_weight=COVERAGE_WEIGHT,
            experience_weight=EXPERIENCE_WEIGHT, adjacent_credit=ADJACENT_CREDIT,
            formula=("match_score = 0.85 * coverage + 0.15 * experience_factor; coverage = sum(w * credit) / sum(w), "
                     "credit = min(1, current/required) if matched, 0.40 if adjacent, 0 if missing; "
                     "experience_factor = min(1, (years + 1) / (role_min + 1))")),
        importance_source=importance_source, experience_years=years,
        typical_experience_years=ExperienceRange(min=lo, max=hi),
        gap_matrix=gaps,
        skills=SkillBuckets(
            matched=by_importance(g for g in matched if g.gap <= 0),
            weak=by_importance(g for g in matched if g.gap > 0),
            adjacent=by_importance(g for g in gaps if g.status == "adjacent"),
            critical_missing=[g.skill for g in critical],
            above_requirement=by_importance(g for g in matched if g.gap < 0)),
        strengths=[g.display for g in sorted(matched, key=lambda g: -g.importance) if g.gap <= 0],
        learning_priorities=[m.display for m in roadmap.milestones[:5]],
        advice=[g.advice for g in gaps if g.advice],
        roadmap=roadmap, similarity_backend=similarity.backend(),
        warnings=warnings,
    )
