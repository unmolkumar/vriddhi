"""Candidate <-> job match: six weighted components (spec starting weights, configurable per request).

overall = Σ weight_c * score_c, each score in [0, 1]. Formulas are in WORKING.md §6.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.engines.locations import REGIONS, expand_cities
from src.engines.market_profile import is_broad
from src.models.schemas import CandidateProfile, CandidateSkill, ExperienceBand, Job, MatchBreakdown, MatchWeights

DEFAULT_WEIGHTS = MatchWeights().normalised()      # skills .40, experience .20, education/location/seniority/preference .10

# Skills
LEVEL_CREDIT = {0: 0.0, 1: 0.6, 2: 0.8}             # candidate level -> credit; level 3+ (work-supported) = 1.0
NEEDS_VERIFICATION_FACTOR = 0.5                    # module 2 flagged the claim as unsupported by the resume
RELATED_SKILL_CREDIT = 0.5                         # candidate has the job skill's parent (SQL for a PostgreSQL job)
INFERRED_SKILL_WEIGHT = 0.5                        # job skills filled from the market profile count half ...
INFERRED_PENALTY = 0.3                             # ... and the skill score shrinks by 30% x share of inferred skills
BROAD_SKILL_WEIGHT = 0.5                           # a listing asking for a field ("data science") is a vague requirement
KEYWORD_TOP_N = 8                                  # keyword fallback: candidate's top skills searched in job text

# Experience
OPEN_ENDED_EXTRA_YEARS = 3.0                       # "4+ years" is read as 4-7
OVER_EXPERIENCE_SPAN = 10.0                        # score falls to the floor over this many years above the band
OVER_EXPERIENCE_FLOOR = 0.5
TITLE_EXPERIENCE = [                               # title_heuristic bands when the posting and request give none
    (r"\b(intern|internship|trainee)\b", (0.0, 1.0)),
    (r"\b(junior|jr|graduate|fresher|entry)\b", (0.0, 2.0)),
    (r"\b(principal|staff|head|director|architect)\b", (8.0, 15.0)),
    (r"\b(lead|manager)\b", (6.0, 12.0)),
    (r"\b(senior|sr)\b", (4.0, 8.0)),
]

# Other components
NEUTRAL = 0.7                                      # the job doesn't say (no location, mode, degree...)
EDUCATION_SHORTFALL = 0.6                          # has a degree, but below what the job mentions
NO_EDUCATION = 0.4
OTHER_LOCATION = 0.3
SAME_REGION = 0.8                                  # e.g. Noida job for a Gurugram candidate (both Delhi NCR)
SENIORITY_STEP_PENALTY = 0.3                       # per seniority level apart
SALARY_BELOW_PREFERENCE = 0.3

CLASSIFICATION = [(0.80, "Strong"), (0.65, "Good"), (0.45, "Partial")]   # else Weak

_DEGREES = [(3, r"\bph\.?\s?d\b|\bdoctorate\b"),
            (2, r"\bmaster'?s?\b|\bm\.?\s?tech\b|\bm\.?\s?e\b|\bm\.?\s?sc\b|\bmca\b|\bmba\b|\bpost.?graduate\b"),
            (1, r"\bbachelor'?s?\b|\bb\.?\s?tech\b|\bb\.?\s?e\b|\bb\.?\s?sc\b|\bbca\b|\bgraduate\b|\bdegree\b")]
_SENIORITY = [(0, r"\b(intern|internship|trainee)\b"), (1, r"\b(junior|jr|associate|graduate|fresher|entry)\b"),
              (5, r"\b(principal|staff|head|director|architect|vp)\b"), (4, r"\b(lead|manager)\b"),
              (3, r"\b(senior|sr)\b")]


@dataclass
class JobMatch:
    overall: float
    breakdown: MatchBreakdown
    matched: list[str]
    missing: list[str]
    band: ExperienceBand | None
    experience_source: str


def classify(overall: float) -> str:
    return next((label for cut, label in CLASSIFICATION if overall >= cut), "Weak")


# --- skills ------------------------------------------------------------------------------

def skill_credit(skill: CandidateSkill) -> float:
    credit = LEVEL_CREDIT.get(skill.level, 1.0)
    return credit * (NEEDS_VERIFICATION_FACTOR if skill.needs_verification else 1.0)


def _candidate_credits(candidate: CandidateProfile) -> tuple[dict[str, float], dict[str, float]]:
    """(credit per skill id, credit per coarser id via maps_to: a PostgreSQL user covers 'sql')."""
    direct: dict[str, float] = {}
    via_parent: dict[str, float] = {}
    for s in candidate.skills:
        c = skill_credit(s)
        direct[s.name] = max(direct.get(s.name, 0.0), c)
        if s.maps_to:
            via_parent[s.maps_to] = max(via_parent.get(s.maps_to, 0.0), c)
    return direct, via_parent


def skill_component(job: Job, candidate: CandidateProfile) -> tuple[float, list[str], list[str], str]:
    direct, via_parent = _candidate_credits(candidate)
    if not job.skills:  # module 2 unreachable or nothing extracted: candidate skills found in the job text
        text = f"{job.title}\n{job.description}".lower()
        top = [s.name for s in sorted(candidate.skills, key=lambda s: -s.level)][:KEYWORD_TOP_N]
        hits = [n for n in top if re.search(rf"\b{re.escape(n.replace('_', ' '))}\b", text)]
        return (len(hits) / len(top) if top else 0.0), hits, [], "keywords"
    total = credit = 0.0
    matched, missing = [], []
    for s in job.skills:
        weight = (INFERRED_SKILL_WEIGHT if s in job.inferred_skills else 1.0) * (BROAD_SKILL_WEIGHT if is_broad(s) else 1.0)
        c = direct.get(s) or via_parent.get(s) or 0.0
        parent = job.skill_parents.get(s)
        if not c and parent and parent in direct:
            c = RELATED_SKILL_CREDIT * direct[parent]
        total += weight
        credit += weight * c
        (matched if c > 0 else missing).append(s)
    # Concrete skills first (the job's own before inferred); broad categories (ai, data_science) always last.
    missing.sort(key=lambda s: (is_broad(s), s in job.inferred_skills))
    inferred_fraction = len([s for s in job.skills if s in job.inferred_skills]) / len(job.skills)
    # A job whose skills are mostly inferred can't outrank comparable jobs with real skills.
    return credit / total * (1 - INFERRED_PENALTY * inferred_fraction), matched, missing, "skills"


def skills_confidence(job: Job) -> str:
    if not job.inferred_skills:
        return "extracted"
    return "inferred" if len(job.inferred_skills) == len(job.skills) else "partly_inferred"


# --- experience and seniority -----------------------------------------------------------------

def experience_band(job: Job, requested: ExperienceBand | None) -> tuple[ExperienceBand | None, str]:
    if job.experience_min is not None:
        hi = job.experience_max if job.experience_max is not None else job.experience_min + OPEN_ENDED_EXTRA_YEARS
        return ExperienceBand(min=job.experience_min, max=max(hi, job.experience_min)), "posting"
    if requested is not None:
        return requested, "request"
    for pattern, (lo, hi) in TITLE_EXPERIENCE:
        if re.search(pattern, job.title, re.IGNORECASE):
            return ExperienceBand(min=lo, max=hi), "title_heuristic"
    return None, "unknown"


def experience_component(years: float, band: ExperienceBand | None) -> float:
    if band is None:
        return NEUTRAL
    if years < band.min:
        return years / band.min if band.min > 0 else 1.0     # spec example: 2 years for "3+" -> 0.67
    if years <= band.max:
        return 1.0
    return max(OVER_EXPERIENCE_FLOOR, 1 - (years - band.max) / OVER_EXPERIENCE_SPAN)


def _level(text: str, table) -> int | None:
    return next((lvl for lvl, pattern in table if re.search(pattern, text, re.IGNORECASE)), None)


def candidate_seniority(years: float) -> int:
    return 0 if years < 0.5 else 1 if years < 2 else 2 if years < 5 else 3 if years < 8 else 4 if years < 12 else 5


def seniority_component(job: Job, years: float, band: ExperienceBand | None) -> float:
    job_level = _level(job.title, _SENIORITY)
    if job_level is None and band is not None:
        job_level = candidate_seniority(band.min)
    if job_level is None:
        return NEUTRAL
    return max(0.0, 1 - SENIORITY_STEP_PENALTY * abs(job_level - candidate_seniority(years)))


# --- education, location, preferences ----------------------------------------------------------

def education_component(education: list[str], job: Job) -> float:
    required = _level(job.description, _DEGREES) or _level(job.title, _DEGREES)
    if not required:
        return 1.0
    have = max((_level(e, _DEGREES) or 0 for e in education), default=0)
    if have >= required:
        return 1.0
    return EDUCATION_SHORTFALL if have else NO_EDUCATION


def candidate_cities(candidate: CandidateProfile) -> set[str]:
    cities = set()
    for loc in [candidate.location, *candidate.preferred_locations]:
        cities.update(expand_cities(loc))
    return cities


def location_component(job: Job, cities: set[str]) -> float:
    if job.work_mode == "remote" or job.location == "Remote":
        return 1.0
    if job.location is None or not cities:
        return NEUTRAL
    if job.location in cities:
        return 1.0
    same_region = any(job.location in members and cities & set(members) for members in REGIONS.values())
    return SAME_REGION if same_region else OTHER_LOCATION


def preference_component(job: Job, work_modes: list[str], employment_types: list[str], min_lpa: float | None) -> float:
    parts = []
    if work_modes:
        parts.append(1.0 if job.work_mode in work_modes else NEUTRAL if job.work_mode == "unknown" else 0.0)
    if employment_types:
        parts.append(1.0 if job.employment_type in employment_types else NEUTRAL if job.employment_type == "unknown" else 0.0)
    if min_lpa:
        top = job.salary_max or job.salary_min
        if top is None or job.salary_is_predicted:
            parts.append(NEUTRAL)
        else:
            parts.append(1.0 if top >= min_lpa * 100_000 else SALARY_BELOW_PREFERENCE)
    return sum(parts) / len(parts) if parts else 1.0


# --- overall ---------------------------------------------------------------------------------

def match_job(job: Job, candidate: CandidateProfile, *, weights: dict[str, float] | None = None,
              typical_experience: ExperienceBand | None = None, work_modes: list[str] = (),
              employment_types: list[str] = (), min_lpa: float | None = None,
              cities: set[str] | None = None) -> JobMatch:
    weights = weights or DEFAULT_WEIGHTS
    skills, matched, missing, method = skill_component(job, candidate)
    band, source = experience_band(job, typical_experience)
    years = candidate.experience_years
    scores = {
        "skills": skills,
        "experience": experience_component(years, band),
        "education": education_component(candidate.education, job),
        "location": location_component(job, cities if cities is not None else candidate_cities(candidate)),
        "seniority": seniority_component(job, years, band),
        "preference": preference_component(job, list(work_modes), list(employment_types), min_lpa),
    }
    overall = sum(weights[k] * v for k, v in scores.items())
    breakdown = MatchBreakdown(**{k: round(v, 4) for k, v in scores.items()},
                               weights={k: round(v, 4) for k, v in weights.items()}, skill_method=method)
    return JobMatch(round(overall, 4), breakdown, matched, missing, band, source)

