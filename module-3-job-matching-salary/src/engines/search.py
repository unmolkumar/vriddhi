"""Job search pipeline: fetch -> filter -> market profile -> match -> rank -> salary -> negotiation -> unlocks."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from src.engines.job_fetcher import fetch_jobs
from src.engines.job_store import JobStore
from src.engines.locations import normalise_city
from src.engines.market_profile import build_profile, infer_skills
from src.engines.matching import TITLE_EXPERIENCE, candidate_cities, classify, match_job
from src.engines.ranking import rank_components, rank_score, sort_key
from src.engines.salary import candidate_value, estimate_market, negotiate
from src.models.schemas import (
    CandidateProfile, CandidateSkill, ExperienceBand, Job, JobResult, JobSearchRequest, JobSearchResponse, SalaryRange,
    SkillUnlock, slug,
)
from src.providers import adzuna

UNLOCK_SKILLS = 3                 # how many missing skills to evaluate for "unlocks N jobs"
UNLOCK_LEVEL = 3                  # as if the candidate had the skill at a working level
GOOD_OR_BETTER = {"Good", "Strong"}
VALUE_TOP_JOBS = 5                # candidate value uses the mean match of the top-ranked jobs


class SearchContext:
    """Everything one search computed, reused by the salary and negotiation endpoints."""

    def __init__(self, response: JobSearchResponse, jobs: dict[str, Job], matches: dict[str, float]):
        self.response, self.jobs, self.matches = response, jobs, matches


def _primary_location(req: JobSearchRequest) -> str | None:
    return req.location or (req.profile.location if req.profile else None) or (req.preferred_locations or [None])[0]


def _role_band(req: JobSearchRequest) -> ExperienceBand | None:
    if req.typical_experience:
        return req.typical_experience
    import re
    for pattern, (lo, hi) in TITLE_EXPERIENCE:
        if re.search(pattern, req.target_role, re.IGNORECASE):
            return ExperienceBand(min=lo, max=hi)
    return None


def _unlock_candidates(req: JobSearchRequest, candidate: CandidateProfile, profile_skills: list[str]) -> list[str]:
    have = {s.name for s in candidate.skills} | {s.maps_to for s in candidate.skills if s.maps_to}
    wanted = []
    if req.gap_analysis:
        wanted += list(req.gap_analysis.critical_missing) + [slug(s) for s in req.gap_analysis.learning_priorities]
    wanted += profile_skills
    return [s for s in dict.fromkeys(wanted) if s and s not in have][:UNLOCK_SKILLS]


def run_search(req: JobSearchRequest, *, store: JobStore | None = None, client: httpx.Client | None = None,
               m2_client: httpx.Client | None = None, now: datetime | None = None, histogram_fn=None) -> SearchContext:
    now = now or datetime.now(timezone.utc)
    candidate = req.candidate()
    location = _primary_location(req)
    fetched = fetch_jobs(req.target_role, location, store=store, client=client, m2_client=m2_client, now=now,
                         jsearch_enrichment=req.jsearch_enrichment)
    warnings = list(fetched.warnings)
    jobs = fetched.jobs
    if req.employment_type:
        kept = [j for j in jobs if j.employment_type in req.employment_type or j.employment_type == "unknown"]
        if len(kept) < len(jobs):
            warnings.append(f"Filtered out {len(jobs) - len(kept)} job(s) with another employment type.")
        jobs = kept

    profile = build_profile(req.target_role, fetched.cities, jobs)
    jobs = infer_skills(jobs, profile)
    if any(j.skills_inferred for j in jobs):
        warnings.append("Some listings had too few skills (descriptions are truncated); their missing skills were "
                        "filled from the role's market profile, marked inferred, and count for less.")

    city = normalise_city(location)
    if histogram_fn is None and adzuna.configured() and fetched.cities:
        histogram_fn = lambda: adzuna.histogram(req.target_role, fetched.cities[0], client=client)  # noqa: E731
    market = estimate_market(jobs, histogram_fn=histogram_fn, baseline=req.market_baseline)
    if market.estimated_median is None:
        warnings.append("No salary estimate: not enough posted salaries, histogram or baseline data.")

    weights = req.weights.normalised() if req.weights else None
    cities = candidate_cities(candidate)
    min_lpa = req.salary_preference.min_lpa if req.salary_preference else None
    kwargs = dict(weights=weights, typical_experience=req.typical_experience, work_modes=req.work_mode,
                  employment_types=req.employment_type, min_lpa=min_lpa, cities=cities)

    results, matches = [], {}
    for job in jobs:
        m = match_job(job, candidate, **kwargs)
        comps = rank_components(job, m.overall, m.breakdown.location, m.breakdown.experience, market.estimated_median, now)
        score = rank_score(comps)
        matches[job.job_id] = m.overall
        results.append((sort_key(score, m.overall, job), job, m, comps, score))
    results.sort(key=lambda r: r[0])

    job_results = [JobResult(
        job_id=job.job_id, title=job.title, company=job.company, location=job.location, work_mode=job.work_mode,
        employment_type=job.employment_type, posted_at=job.posted_at, source=job.source, publisher=job.publisher,
        source_url=job.source_url, stale=job.stale,
        salary=SalaryRange(min=job.salary_min, max=job.salary_max, is_predicted=job.salary_is_predicted),
        match_score=round(m.overall * 100), classification=classify(m.overall), match=m.breakdown,
        matched_skills=m.matched, missing_skills=m.missing, inferred_skills=job.inferred_skills,
        skills_source=job.skills_source, experience_required=m.band, experience_source=m.experience_source,
        rank=i + 1, rank_score=score, rank_components={k: round(v, 4) for k, v in comps.items()},
    ) for i, (_, job, m, comps, score) in enumerate(results)]

    top_matches = [r[2].overall for r in results[:VALUE_TOP_JOBS]]
    mean_match = sum(top_matches) / len(top_matches) if top_matches else 0.0
    value = candidate_value(market, match_score=mean_match, years=candidate.experience_years, band=_role_band(req), city=city)
    negotiation = None
    if results:
        top = results[0][1]
        top_value = candidate_value(market, match_score=results[0][2].overall, years=candidate.experience_years,
                                    band=_role_band(req), city=city)
        negotiation = negotiate(market=market, candidate=top_value, job_id=top.job_id, posted_min=top.salary_min,
                                posted_max=top.salary_max, posted_is_predicted=top.salary_is_predicted)

    unlocks = []
    for skill in _unlock_candidates(req, candidate, [p.skill for p in profile.top_skills]):
        boosted = candidate.model_copy(update={"skills": candidate.skills + [CandidateSkill(name=skill, level=UNLOCK_LEVEL)]})
        moved = [job.job_id for _, job, m, _, _ in results
                 if classify(m.overall) not in GOOD_OR_BETTER
                 and classify(match_job(job, boosted, **kwargs).overall) in GOOD_OR_BETTER]
        where = f" in {city}" if city else ""
        unlocks.append(SkillUnlock(
            skill=skill, jobs_unlocked=len(moved), city=city, example_job_ids=moved[:3],
            message=(f"Learning {skill.replace('_', ' ')} would move {len(moved)} more {req.target_role} "
                     f"job{'s' if len(moved) != 1 else ''}{where} to a Good or Strong match.")))
    unlocks.sort(key=lambda u: -u.jobs_unlocked)

    response = JobSearchResponse(
        target_role=req.target_role, location=city, cities=fetched.cities, total_found=len(jobs),
        total_available=fetched.total_available, jobs=job_results[:req.limit], role_market_profile=profile,
        market_salary=market, candidate_value=value, negotiation=negotiation, skill_unlocks=unlocks,
        provider_trace=fetched.attempts, sources=fetched.sources, stale=fetched.stale,
        warnings=list(dict.fromkeys(warnings)))
    return SearchContext(response, {j.job_id: j for j in jobs}, matches)
