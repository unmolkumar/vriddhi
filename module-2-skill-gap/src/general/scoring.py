"""Match score, experience factor and verdict for the general engine (WORKING.md section 12).

  credit(item)  = min(1, EVIDENCE_STRENGTH[evidence type] / required level)  if met
                = PARTIAL_CREDIT                                            if partial
                = 0                                                         if missing
  skill_score   = sum over core types of effective_share(type) x weighted mean credit (matcher.coverage)
  match_score   = skill_score x experience_factor
  experience_factor = 1 inside or above the band; below it, down to EXPERIENCE_MIN_FACTOR at EXPERIENCE_GAP_YEARS
"""
from __future__ import annotations

import re
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

# Role history (A3): a past title that resolves to the occupation gives its core tasks/DWAs without real evidence
# an implied partial credit, by years, always below PARTIAL_CREDIT; never 'met'.
IMPLIED_TYPES = ("task", "dwa")
IMPLIED_CREDIT_PER_YEAR = 0.08
IMPLIED_CREDIT_MAX = 0.4
IMPLIED_CREDIT_UNKNOWN_YEARS = 0.1       # a title with no dates (resume header)
ROLE_MIN_CONFIDENCE = 0.7                # module 1 search confidence for a past title to count

# Experience. Band from module 1's Indian postings (profile.indian_experience) when it is reliable (enough recent
# postings), else the O*NET job zone.
JOB_ZONE_YEARS = {1: (0.0, 1.0), 2: (0.0, 2.0), 3: (1.0, 4.0), 4: (2.0, 6.0), 5: (4.0, 10.0)}
INDIA_BAND_MIN_SAMPLE = 30       # postings behind module 1's Indian band
INDIA_BAND_MIN_YEAR = 2023       # ... and its newest posting year (years_covered "2023-2025")
EXPERIENCE_MIN_FACTOR = 0.8      # far below the band, the score keeps 80%
EXPERIENCE_GAP_YEARS = 3.0       # ... reached this many years below the band's minimum

# fit_percent (A4): piecewise-linear through 0 -> 0, GOOD_FIT_THRESHOLD -> 50, FIT_MEDIAN_GOOD -> 80,
# FIT_P90_GOOD -> 95, 1.0 -> FIT_CAP; 100 only when every core requirement is met. Anchors: the full tuning profiles
# (incl. short and oblique) at or above the threshold, frozen constants (scripts/calibrate.py --verdict).
FIT_MEDIAN_GOOD = 0.48
FIT_P90_GOOD = 0.59
FIT_CAP = 99
FIT_LABELS = ((80, "Strong fit"), (50, "Good fit"), (25, "Developing"), (0, "Early stage"))

# Verdict (calibrated on the tuning set, WORKING.md section 12.2).
GOOD_FIT_THRESHOLD = 0.29        # A4: tuned with verdict.py's thresholds on the tuning sets (WORKING.md 15); 0.22 in A3b
OVERQUALIFIED_EXTRA_YEARS = 2.0  # years above the band's maximum


class Band(NamedTuple):
    low: float
    high: float
    source: str                  # india_postings | job_zone | none


def credit(m: RequirementMatch) -> float:
    if m.implied_credit is not None:          # implied by role history, or a "some" answer
        return m.implied_credit
    if m.status == "missing":
        return 0.0
    if m.status == "partial":
        return PARTIAL_CREDIT
    strength = EVIDENCE_STRENGTH.get(m.evidence_type or "self", EVIDENCE_STRENGTH["self"])
    return min(1.0, strength / max(m.item.level, 1e-6))


def implied_credit(years: float | None) -> float:
    if years is None:
        return IMPLIED_CREDIT_UNKNOWN_YEARS
    return min(IMPLIED_CREDIT_MAX, IMPLIED_CREDIT_PER_YEAR * years)


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


def fit_percent(score: float, all_met: bool = False) -> int:
    """User-facing 0-100 from the raw match score (see FIT_MEDIAN_GOOD); all_met: every core requirement met."""
    if all_met:
        return 100
    xs = (0.0, GOOD_FIT_THRESHOLD, FIT_MEDIAN_GOOD, FIT_P90_GOOD, 1.0)
    ys = (0, 50, 80, 95, FIT_CAP)
    score = min(max(score, 0.0), 1.0)
    i = next(k for k in range(1, len(xs)) if score <= xs[k])
    return round(ys[i - 1] + (ys[i] - ys[i - 1]) * (score - xs[i - 1]) / (xs[i] - xs[i - 1]))


def fit_label(percent: int) -> str:
    return next(label for floor, label in FIT_LABELS if percent >= floor)


def india_band_reliable(exp: dict) -> bool:
    """Enough recent postings behind module 1's Indian band. Missing sample_size / years_covered -> unreliable."""
    years = [int(y) for y in re.findall(r"\d{4}", str(exp.get("years_covered") or ""))]
    return (not exp.get("fallback_to_job_zone") and exp.get("typical_max") is not None
            and (exp.get("sample_size") or 0) >= INDIA_BAND_MIN_SAMPLE and bool(years) and max(years) >= INDIA_BAND_MIN_YEAR)


def experience_band(profile: dict | None) -> Band | None:
    """Module 1 profile -> band: indian_experience when reliable (india_band_reliable), else the job zone."""
    if not profile:
        return None
    exp = profile.get("indian_experience") or {}
    if india_band_reliable(exp):
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


def verdict(score: float, years: float | None, band: Band | None, better_fit: tuple[str, str, float] | None,
            threshold: float | None = None) -> tuple[str, str]:
    """(label, reason). better_fit: (soc, title, score) of a related occupation with a higher job zone where the
    user also clears GOOD_FIT_THRESHOLD. threshold overrides GOOD_FIT_THRESHOLD (short descriptions)."""
    threshold = GOOD_FIT_THRESHOLD if threshold is None else threshold
    if score < threshold:
        return "under_skilled", (f"Your evidence covers {score:.0%} of this role's weighted core requirements, "
                                 f"below the {threshold:.0%} needed for a good fit.")
    senior = years is not None and band is not None and years > band.high + OVERQUALIFIED_EXTRA_YEARS
    if senior and better_fit:
        return "over_qualified", (f"You match this role ({score:.0%}) and have {years:g} years against a typical "
                                  f"{band.low:g}-{band.high:g}; {better_fit[1]} is a more senior fit ({better_fit[2]:.0%}).")
    return "good_fit", f"Your evidence covers {score:.0%} of this role's weighted core requirements."
