"""Role market profile: which skills the fetched jobs for a role + city ask for, and inference for sparse jobs.

Adzuna truncates descriptions (~500 chars), so many jobs yield few skills. A job with fewer than
MIN_JOB_SKILLS extracted skills is topped up from this profile, marked skills_inferred, and its inferred
skills count for less in matching (matching.INFERRED_SKILL_WEIGHT).
"""
from __future__ import annotations

from collections import Counter

from src.models.schemas import Job, ProfileSkill, RoleMarketProfile

MIN_JOB_SKILLS = 3          # below this a job's own skills are too sparse to match on alone
INFER_FILL_TO = 5           # top a sparse job up to this many skills
MIN_PROFILE_SHARE = 0.15    # only infer skills asked for by at least 15% of analysed jobs
PROFILE_TOP_N = 15


def _own_skills(job: Job) -> list[str]:
    return [s for s in job.skills if s not in job.inferred_skills]


def build_profile(role: str, cities: list[str], jobs: list[Job]) -> RoleMarketProfile:
    analysed = [j for j in jobs if j.skills_source in ("m2", "jsearch_full") and _own_skills(j)]
    counts = Counter(s for j in analysed for s in set(_own_skills(j)))
    top = [ProfileSkill(skill=s, share=round(n / len(analysed), 3), jobs=n)
           for s, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:PROFILE_TOP_N]] if analysed else []
    return RoleMarketProfile(role=role, cities=cities, jobs_analysed=len(analysed), top_skills=top)


def infer_skills(jobs: list[Job], profile: RoleMarketProfile) -> list[Job]:
    """Top up sparse jobs from the profile. Jobs with enough skills are returned unchanged."""
    common = [p.skill for p in profile.top_skills if p.share >= MIN_PROFILE_SHARE]
    out = []
    for job in jobs:
        own = _own_skills(job)
        if len(own) >= MIN_JOB_SKILLS or not common:
            out.append(job)
            continue
        fill = [s for s in common if s not in own][: max(0, INFER_FILL_TO - len(own))]
        out.append(job.model_copy(update={"skills": own + fill, "skills_inferred": bool(fill), "inferred_skills": fill}))
    return out
