"""Job search pipeline: fetch -> filter -> market profile -> match -> rank -> salary -> negotiation -> unlocks."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx

from src.engines.job_fetcher import fetch_jobs
from src.engines.job_store import JobStore, role_key
from src.engines.locations import normalise_city
from src.engines.m2_client import M2Unavailable, resolve_typed_skills
from src.engines.market_profile import build_profile, category_flags, concrete_children, infer_skills, is_broad
from src.engines.matching import TITLE_EXPERIENCE, candidate_cities, classify, match_job, skills_confidence
from src.engines.ranking import rank_components, rank_score, sort_key
from src.engines.salary import candidate_value, estimate_market, m1_salary, negotiate
from src.models.schemas import (
    MANUAL_SKILL_LEVEL, CandidateProfile, CandidateSkill, ExperienceBand, Job, JobResult, JobSearchRequest,
    JobSearchResponse, SalaryRange, SkillUnlock, slug,
)
from src.providers import adzuna, jsearch
from src.providers.common import ProviderError

UNLOCK_SKILLS = 3                 # how many missing skills to evaluate for "unlocks N jobs"
UNLOCK_LEVEL = 3                  # as if the candidate had the skill at a working level
GOOD_OR_BETTER = {"Good", "Strong"}
VALUE_TOP_JOBS = 5                # candidate value uses the mean match of the top-ranked jobs
SALARY_CACHE_TTL_DAYS = 7         # JSearch salary estimates change slowly; cached per role x city x experience band


def readable(skill_id: str) -> str:
    """'machine_learning' -> 'machine learning'; short ids read as acronyms ('ai' -> 'AI', 'aws' -> 'AWS')."""
    return skill_id.upper() if len(skill_id) <= 3 else skill_id.replace("_", " ")


class SearchContext:
    """Everything one search computed, reused by the salary and negotiation endpoints."""

    def __init__(self, response: JobSearchResponse, jobs: dict[str, Job], matches: dict[str, float]):
        self.response, self.jobs, self.matches = response, jobs, matches


def age_hours(fetched_at: datetime, now: datetime) -> float:
    return round(max(0.0, (now - fetched_at).total_seconds() / 3600), 1)


def age_text(hours: float | None) -> str | None:
    """'fetched 10 h ago' ('fetched 25 min ago' under an hour)."""
    if hours is None:
        return None
    return f"fetched {round(hours * 60)} min ago" if hours < 1 else f"fetched {round(hours, 1):g} h ago"


def _primary_location(req: JobSearchRequest) -> str | None:
    return req.location or (req.profile.location if req.profile else None) or (req.preferred_locations or [None])[0]


def _role_band(req: JobSearchRequest) -> ExperienceBand | None:
    if req.typical_experience:
        return req.typical_experience
    for pattern, (lo, hi) in TITLE_EXPERIENCE:
        if re.search(pattern, req.target_role, re.IGNORECASE):
            return ExperienceBand(min=lo, max=hi)
    return None


def _candidate(req: JobSearchRequest, m2_client: httpx.Client | None, warnings: list[str]) -> CandidateProfile:
    """Module 2's profile as-is; typed skills resolved through module 2 ("Postgres" -> postgresql)."""
    candidate = req.candidate()
    if req.profile is not None or not req.skills:
        return candidate
    try:
        skills, unknown = resolve_typed_skills(req.skills, client=m2_client)
    except M2Unavailable:
        warnings.append("Module 2 is unreachable, so typed skills are matched as plain keywords "
                        "(e.g. 'Postgres' won't match PostgreSQL).")
        return candidate
    resolved = [CandidateSkill(name=s["id"], level=MANUAL_SKILL_LEVEL, maps_to=s.get("maps_to")) for s in skills]
    resolved += [CandidateSkill(name=slug(n), level=MANUAL_SKILL_LEVEL) for n in unknown]
    if unknown:
        warnings.append(f"Not recognised as skills, matched by keyword only: {', '.join(unknown)}.")
    return candidate.model_copy(update={"skills": list({s.name: s for s in resolved}.values())})


def _jsearch_salary(req: JobSearchRequest, city: str | None, years: float, store: JobStore,
                    client: httpx.Client | None, now: datetime, warnings: list[str]) -> dict | None:
    """Cached JSearch salary estimate for role x city x experience bucket (exact bucket, else 'ALL');
    a fresh call only when the request opts in (jsearch_salary), to protect the 200/month quota."""
    if not city:
        return None
    bucket = jsearch.experience_bucket(years)
    ttl = timedelta(days=SALARY_CACHE_TTL_DAYS)
    for b in (bucket, "ALL"):
        cached = store.load_salary(f"{role_key(req.target_role)}|{city}|{b}")
        if cached and now - cached[1] <= ttl:
            return cached[0]
    if not (req.jsearch_salary and jsearch.configured()):
        return None
    try:
        estimate = jsearch.estimated_salary(req.target_role, city, bucket, client=client)
    except ProviderError as e:
        warnings.append(f"JSearch salary estimate unavailable ({e.status}).")
        return None
    if estimate:
        store.save_salary(f"{role_key(req.target_role)}|{city}|{bucket}", estimate, now)
    return estimate


def _unlock_skills(req: JobSearchRequest, candidate: CandidateProfile, jobs: list[Job], profile) -> list[str]:
    """Concrete skills to evaluate: module 2's gap first, then the market profile. Broad categories
    (ai, data_science...) are replaced by their most-asked concrete child in this market, or dropped."""
    have = {s.name for s in candidate.skills} | {s.maps_to for s in candidate.skills if s.maps_to}
    wanted = []
    if req.gap_analysis:
        wanted += list(req.gap_analysis.critical_missing) + [slug(s) for s in req.gap_analysis.learning_priorities]
    wanted += [p.skill for p in profile.top_skills]
    out, flags = [], category_flags(jobs)
    for skill in dict.fromkeys(wanted):
        broad = is_broad(skill, flags)
        options = [c for c in concrete_children(skill, jobs, profile) if c not in have] if broad else [skill]
        pick = next((o for o in options if o and o not in have and o not in out and not is_broad(o, flags)), None)
        if pick:
            out.append(pick)
        if len(out) == UNLOCK_SKILLS:
            break
    return out


def run_search(req: JobSearchRequest, *, store: JobStore | None = None, client: httpx.Client | None = None,
               m2_client: httpx.Client | None = None, now: datetime | None = None, histogram_fn=None) -> SearchContext:
    now = now or datetime.now(timezone.utc)
    store = store or JobStore()
    warnings: list[str] = []
    candidate = _candidate(req, m2_client, warnings)
    location = _primary_location(req)
    fetched = fetch_jobs(req.target_role, location, store=store, client=client, m2_client=m2_client, now=now,
                         jsearch_enrichment=req.jsearch_enrichment)
    warnings += fetched.warnings
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
    js_salary = _jsearch_salary(req, fetched.cities[0] if fetched.cities else city, candidate.experience_years,
                                store, client, now, warnings)
    m1 = (m1_salary(req.market_salary_percentiles, candidate.experience_years, city,
                    remote_only=set(req.work_mode) == {"remote"})
          if req.market_salary_percentiles else None)
    market = estimate_market(jobs, histogram_fn=histogram_fn, baseline=req.market_baseline,
                             percentiles=m1, jsearch_estimate=js_salary)
    if market.estimated_median is None:
        warnings.append("No salary estimate: not enough posted salaries, percentiles, estimates or histogram data.")

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

    where = f" in {city}" if city else ""
    notes = {"inferred": f"Skills inferred from similar {req.target_role} jobs{where}; the listing's text was too short.",
             "partly_inferred": f"Some skills inferred from similar {req.target_role} jobs{where}."}
    job_results = []
    for i, (_, job, m, comps, score) in enumerate(results):
        confidence = skills_confidence(job)
        job_results.append(JobResult(
            job_id=job.job_id, title=job.title, company=job.company, location=job.location, work_mode=job.work_mode,
            employment_type=job.employment_type, posted_at=job.posted_at, source=job.source, publisher=job.publisher,
            source_url=job.source_url, stale=job.stale, fetched_at=job.last_observed_at,
            age_hours=age_hours(job.last_observed_at, now),
            salary=SalaryRange(min=job.salary_min, max=job.salary_max, is_predicted=job.salary_is_predicted),
            match_score=round(m.overall * 100), classification=classify(m.overall), match=m.breakdown,
            matched_skills=m.matched, missing_skills=m.missing, inferred_skills=job.inferred_skills,
            skills_source=job.skills_source, skills_confidence=confidence, skills_note=notes.get(confidence),
            experience_required=m.band, experience_source=m.experience_source,
            rank=i + 1, rank_score=score, rank_components={k: round(v, 4) for k, v in comps.items()},
        ))

    # Position the candidate within the band the salary data describes, so experience isn't counted twice:
    # module 1's percentiles describe one experience tier, and a JSearch estimate one experience bucket.
    band = _role_band(req)
    if "module_1_percentiles" in market.sources_used and m1 and m1.band:
        band = m1.band
    elif market.sources_used == ["jsearch_salary_estimate"] and js_salary and js_salary.get("bucket") in jsearch.BUCKET_BANDS:
        lo, hi = jsearch.BUCKET_BANDS[js_salary["bucket"]]
        band = ExperienceBand(min=lo, max=hi)
    top_matches = [r[2].overall for r in results[:VALUE_TOP_JOBS]]
    mean_match = sum(top_matches) / len(top_matches) if top_matches else 0.0
    value = candidate_value(market, match_score=mean_match, years=candidate.experience_years, band=band, city=city)
    negotiation = None
    if results:
        top = results[0][1]
        top_value = candidate_value(market, match_score=results[0][2].overall, years=candidate.experience_years,
                                    band=band, city=city)
        negotiation = negotiate(market=market, candidate=top_value, job_id=top.job_id, posted_min=top.salary_min,
                                posted_max=top.salary_max, posted_is_predicted=top.salary_is_predicted)

    display = {}
    for job in jobs:
        display.update(job.skill_display)
    unlocks = []
    for skill in _unlock_skills(req, candidate, jobs, profile):
        boosted = candidate.model_copy(update={"skills": candidate.skills + [CandidateSkill(name=skill, level=UNLOCK_LEVEL)]})
        moved = [job.job_id for _, job, m, _, _ in results
                 if classify(m.overall) not in GOOD_OR_BETTER
                 and classify(match_job(job, boosted, **kwargs).overall) in GOOD_OR_BETTER]
        if not moved:
            continue                     # "would move 0 jobs" isn't advice
        name = display.get(skill) or readable(skill)
        unlocks.append(SkillUnlock(
            skill=skill, jobs_unlocked=len(moved), city=city, example_job_ids=moved[:3],
            message=(f"Learning {name} would move {len(moved)} more {req.target_role} "
                     f"job{'s' if len(moved) != 1 else ''}{where} to a Good or Strong match.")))
    unlocks.sort(key=lambda u: -u.jobs_unlocked)

    oldest = min((j.last_observed_at for j in jobs), default=None)
    age = age_hours(oldest, now) if oldest else None
    response = JobSearchResponse(
        target_role=req.target_role, location=city, cities=fetched.cities, total_found=len(jobs),
        total_available=fetched.total_available, jobs=job_results[:req.limit], role_market_profile=profile,
        market_salary=market, candidate_value=value, negotiation=negotiation, skill_unlocks=unlocks,
        provider_trace=fetched.attempts, sources=fetched.sources, stale=fetched.stale,
        fetched_at=oldest, age_hours=age, data_age=age_text(age), warnings=list(dict.fromkeys(warnings)))
    return SearchContext(response, {j.job_id: j for j in jobs}, matches)
