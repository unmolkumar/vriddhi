"""Match score, experience factor and verdict for the general engine (WORKING.md section 12).

  credit(item)  = min(1, EVIDENCE_STRENGTH[evidence type] / required level)  if met
                = PARTIAL_CREDIT                                            if partial
                = 0                                                         if missing
  skill_score   = sum over core types of effective_share(type) x weighted mean credit (matcher.coverage)
  match_score   = skill_score x experience_factor
  experience_factor = 1 inside or above the band; below it, down to EXPERIENCE_MIN_FACTOR at EXPERIENCE_GAP_YEARS
"""
from __future__ import annotations

from typing import NamedTuple

from src.engines.profile_builder import EVIDENCE_STRENGTH as V1_EVIDENCE_STRENGTH
from src.general.matcher import TYPE_SHARE, RequirementMatch, coverage, effective_share

# Evidence strength = v1's confidence for the same evidence (work 0.90, project 0.75, mentioned 0.50, self 0.40).
EVIDENCE_STRENGTH = {
    "work": V1_EVIDENCE_STRENGTH["work_supported"][1],
    "project": V1_EVIDENCE_STRENGTH["project_supported"][1],
    "mentioned": V1_EVIDENCE_STRENGTH["resume_mentioned"][1],
    "self": V1_EVIDENCE_STRENGTH["self_reported"][1],
}
PARTIAL_CREDIT = 0.5
WEAK_EVIDENCE = ("mentioned", "self")     # met only by these -> advice to show it in work or projects

# Experience. Band from module 1's Indian postings (profile.indian_experience), else the O*NET job zone.
JOB_ZONE_YEARS = {1: (0.0, 1.0), 2: (0.0, 2.0), 3: (1.0, 4.0), 4: (2.0, 6.0), 5: (4.0, 10.0)}
EXPERIENCE_MIN_FACTOR = 0.8      # far below the band, the score keeps 80%
EXPERIENCE_GAP_YEARS = 3.0       # ... reached this many years below the band's minimum

# Verdict (calibrated on the tuning set, WORKING.md section 12.2).
GOOD_FIT_THRESHOLD = 0.29        # midpoint of tuning-set partial max 0.288 and full min 0.295 (a thin gap)
OVERQUALIFIED_EXTRA_YEARS = 3.0  # years above the band's maximum


class Band(NamedTuple):
    low: float
    high: float
    source: str                  # india_postings | job_zone | none


def credit(m: RequirementMatch) -> float:
    if m.status == "missing":
        return 0.0
    if m.status == "partial":
        return PARTIAL_CREDIT
    strength = EVIDENCE_STRENGTH.get(m.evidence_type or "self", EVIDENCE_STRENGTH["self"])
    return min(1.0, strength / max(m.item.level, 1e-6))


def skill_score(matches: list[RequirementMatch]) -> float:
    return coverage(matches, credit=credit)


def by_type(matches: list[RequirementMatch]) -> dict[str, dict]:
    """Per core type: effective share (renormalised), weighted mean credit, item count."""
    groups: dict[str, list[RequirementMatch]] = {}
    for m in matches:
        if m.item.item_type in TYPE_SHARE:
            groups.setdefault(m.item.item_type, []).append(m)
    raw = {t: effective_share(TYPE_SHARE[t], sum(m.item.weight for m in ms), sum(m.item.base_weight for m in ms), len(ms))
           for t, ms in groups.items()}
    total = sum(raw.values()) or 1.0
    out = {}
    for t, ms in groups.items():
        w = sum(m.item.weight for m in ms)
        out[t] = {"share": round(raw[t] / total, 4), "items": len(ms),
                  "coverage": round(sum(m.item.weight * credit(m) for m in ms) / w, 4) if w else 0.0}
    return out


def experience_band(profile: dict | None) -> Band | None:
    """Module 1 profile -> band: indian_experience when it has postings behind it, else the job zone."""
    if not profile:
        return None
    exp = profile.get("indian_experience") or {}
    if exp.get("sample_size") and not exp.get("fallback_to_job_zone") and exp.get("typical_max") is not None:
        return Band(float(exp.get("typical_min") or 0.0), float(exp["typical_max"]), "india_postings")
    zone = (profile.get("job_zone") or {}).get("job_zone")
    if zone in JOB_ZONE_YEARS:
        return Band(*JOB_ZONE_YEARS[zone], "job_zone")
    return None


def experience_factor(years: float | None, band: Band | None) -> float:
    if years is None or band is None or years >= band.low:
        return 1.0
    short = min(1.0, (band.low - years) / EXPERIENCE_GAP_YEARS)
    return 1.0 - (1.0 - EXPERIENCE_MIN_FACTOR) * short


def verdict(score: float, years: float | None, band: Band | None, better_fit: tuple[str, str, float] | None
            ) -> tuple[str, str]:
    """(label, reason). better_fit: (soc, title, score) of a related occupation with a higher job zone where the
    user also clears GOOD_FIT_THRESHOLD."""
    if score < GOOD_FIT_THRESHOLD:
        return "under_skilled", (f"Your evidence covers {score:.0%} of this role's weighted core requirements, "
                                 f"below the {GOOD_FIT_THRESHOLD:.0%} needed for a good fit.")
    senior = years is not None and band is not None and years > band.high + OVERQUALIFIED_EXTRA_YEARS
    if senior and better_fit:
        return "over_qualified", (f"You match this role ({score:.0%}) and have {years:g} years against a typical "
                                  f"{band.low:g}-{band.high:g}; {better_fit[1]} is a more senior fit ({better_fit[2]:.0%}).")
    return "good_fit", f"Your evidence covers {score:.0%} of this role's weighted core requirements."
