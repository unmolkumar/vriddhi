"""Cache-first live job retrieval: Adzuna -> JSearch -> stale snapshot, per city, then merge and dedupe."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx

from src.engines.job_store import JobStore
from src.engines.locations import expand_cities
from src.engines.m2_client import enrich_skills
from src.models.schemas import FetchResult, Job, ProviderAttempt
from src.providers import adzuna, jsearch
from src.providers.common import ProviderError, dedupe_key

PROVIDERS = [adzuna, jsearch]       # in order of preference
DEFAULT_TTL_HOURS = 6.0
MAX_JOB_AGE_DAYS = 45               # older postings are treated as expired


def ttl_hours() -> float:
    try:
        return float(os.getenv("LIVE_CACHE_TTL_HOURS", "") or DEFAULT_TTL_HOURS)
    except ValueError:
        return DEFAULT_TTL_HOURS


def _quality(job: Job) -> tuple:
    """Which duplicate to keep: a real posted salary, then a longer description, then provider order."""
    real_salary = job.salary_min is not None and not job.salary_is_predicted
    return (real_salary, job.salary_min is not None, len(job.description), -[p.NAME for p in PROVIDERS].index(job.source))


def dedupe(jobs: list[Job]) -> list[Job]:
    best: dict[str, Job] = {}
    for job in jobs:
        key = dedupe_key(job.title, job.company, job.location)
        if key not in best or _quality(job) > _quality(best[key]):
            best[key] = job
    return list(best.values())


def fetch_jobs(role: str, location: str, *, store: JobStore | None = None, client: httpx.Client | None = None,
               m2_client: httpx.Client | None = None, now: datetime | None = None,
               force_refresh: bool = False) -> FetchResult:
    """Current jobs for role in location ('Delhi-NCR' expands to Delhi, Noida, Gurugram).

    Per city: fresh cache -> providers in order -> stale snapshot. Snapshot jobs are labelled stale with
    their age; nothing older than the TTL is served as fresh.
    """
    now = now or datetime.now(timezone.utc)
    store = store or JobStore()
    ttl = timedelta(hours=ttl_hours())
    cities = expand_cities(location)
    attempts: list[ProviderAttempt] = []
    warnings: list[str] = []
    collected: list[Job] = []
    sources, totals = set(), []
    all_cached, any_stale = True, False

    for city in cities:
        cached = store.load(role, city)
        if cached and not force_refresh and now - cached.fetched_at <= ttl:
            attempts.append(ProviderAttempt(provider=cached.source, city=city, status="cache_hit", jobs=len(cached.jobs)))
            collected += _with_skills(role, city, cached.jobs, store, m2_client, warnings)
            sources.add(cached.source)
            totals.append(cached.total_count)
            continue
        all_cached = False
        fetched = None
        for provider in PROVIDERS:
            if not provider.configured():
                attempts.append(ProviderAttempt(provider=provider.NAME, city=city, status="not_configured"))
                continue
            try:
                jobs, total = provider.search(role, city, client=client, now=now)
            except ProviderError as e:
                attempts.append(ProviderAttempt(provider=provider.NAME, city=city, status=e.status, detail=e.detail))
                continue
            if not jobs:
                attempts.append(ProviderAttempt(provider=provider.NAME, city=city, status="empty"))
                continue
            attempts.append(ProviderAttempt(provider=provider.NAME, city=city, status="ok", jobs=len(jobs)))
            fetched = (provider.NAME, jobs, total)
            break
        if fetched:
            name, jobs, total = fetched
            jobs, _ = enrich_skills(jobs, client=m2_client)
            store.save(role, city, name, jobs, total, now)
            collected += jobs
            sources.add(name)
            totals.append(total)
            continue
        if cached:  # every provider failed: fall back to the last snapshot, clearly labelled
            age = round((now - cached.fetched_at).total_seconds() / 3600, 1)
            stale_jobs = [j.model_copy(update={"stale": True, "age_hours": age}) for j in cached.jobs]
            attempts.append(ProviderAttempt(provider="snapshot", city=city, status="used", jobs=len(stale_jobs),
                                            detail=f"{age} h old"))
            warnings.append(f"Live providers unavailable for {city}; showing jobs last fetched {age} hours ago.")
            collected += _with_skills(role, city, stale_jobs, store, m2_client, warnings)
            sources.add("snapshot")
            any_stale = True
        else:
            attempts.append(ProviderAttempt(provider="snapshot", city=city, status="missing"))
            warnings.append(f"No live or cached jobs available for {city}.")

    cutoff = now - timedelta(days=MAX_JOB_AGE_DAYS)
    fresh = [j for j in collected if j.posted_at is None or j.posted_at >= cutoff]
    if len(fresh) < len(collected):
        warnings.append(f"Dropped {len(collected) - len(fresh)} job(s) posted over {MAX_JOB_AGE_DAYS} days ago.")
    jobs = dedupe(fresh)
    if len(jobs) < len(fresh):
        warnings.append(f"Merged {len(fresh) - len(jobs)} duplicate listing(s).")
    if any(j.skills_source == "unavailable" for j in jobs):
        warnings.append("Module 2 (skill extraction) is unreachable; job skills are missing and skill matching "
                        "falls back to title and keyword overlap.")
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    jobs.sort(key=lambda j: j.posted_at or epoch, reverse=True)
    known = [t for t in totals if t is not None]
    return FetchResult(role=role, cities=cities, jobs=jobs, total_available=sum(known) if known else None,
                       sources=sorted(sources), from_cache=bool(cities) and all_cached, stale=any_stale,
                       attempts=attempts, warnings=list(dict.fromkeys(warnings)))


def _with_skills(role: str, city: str, jobs: list[Job], store: JobStore, m2_client, warnings: list[str]) -> list[Job]:
    """Retry module 2 for cached jobs whose skills couldn't be extracted earlier."""
    if all(j.skills_source == "m2" for j in jobs):
        return jobs
    enriched, available = enrich_skills(jobs, client=m2_client)
    if available:
        store.update_jobs(role, city, [j.model_copy(update={"stale": False, "age_hours": None}) for j in enriched])
    return enriched
