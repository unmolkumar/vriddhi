"""v2 listing retrieval: several queries x cities, cache-first, Adzuna then JSearch only when Adzuna fails.

Same providers, SQLite cache and dedupe as v1 (job_fetcher), but cached under 'v2:<query>' and without v1's per-job
skill extraction (v2 matches whole job texts through module 2's match_texts instead).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from src.engines.job_fetcher import MAX_JOB_AGE_DAYS, dedupe, ttl_hours
from src.engines.job_store import JobStore
from src.models.schemas import Job, ProviderAttempt
from src.providers import adzuna, jsearch
from src.providers.common import ProviderError

KEY_PREFIX = "v2:"


def fetch(queries: list[str], cities: list[str], *, store: JobStore | None = None, client: httpx.Client | None = None,
          now: datetime | None = None, force_refresh: bool = False, use_jsearch: bool = True):
    """(jobs, attempts, warnings, sources, stale, oldest fetch time). Per (query, city): fresh cache -> Adzuna ->
    JSearch (only if Adzuna failed or was empty, and use_jsearch) -> stale snapshot."""
    now = now or datetime.now(timezone.utc)
    store = store or JobStore()
    ttl = timedelta(hours=ttl_hours())
    attempts: list[ProviderAttempt] = []
    warnings: list[str] = []
    collected: list[Job] = []
    sources: set[str] = set()
    stale, oldest = False, None
    for city in cities:
        for query in queries:
            key = KEY_PREFIX + query
            cached = store.load(key, city)
            label = f"{city} | {query}"
            if cached and not force_refresh and now - cached.fetched_at <= ttl:
                attempts.append(ProviderAttempt(provider=cached.source, city=label, status="cache_hit", jobs=len(cached.jobs)))
                collected += cached.jobs
                sources.add(cached.source)
                oldest = min(oldest or cached.fetched_at, cached.fetched_at)
                continue
            got = None
            for provider in [adzuna] + ([jsearch] if use_jsearch else []):
                if not provider.configured():
                    attempts.append(ProviderAttempt(provider=provider.NAME, city=label, status="not_configured"))
                    continue
                try:
                    jobs, total = provider.search(query, city, client=client, now=now)
                except ProviderError as e:
                    attempts.append(ProviderAttempt(provider=provider.NAME, city=label, status=e.status, detail=e.detail))
                    continue
                attempts.append(ProviderAttempt(provider=provider.NAME, city=label, status="ok" if jobs else "empty",
                                                jobs=len(jobs)))
                if jobs:
                    got = (provider.NAME, jobs, total)
                    break
            if got:
                name, jobs, total = got
                store.save(key, city, name, jobs, total, now)
                collected += jobs
                sources.add(name)
                oldest = min(oldest or now, now)
            elif cached:
                age = round((now - cached.fetched_at).total_seconds() / 3600, 1)
                collected += [j.model_copy(update={"stale": True, "age_hours": age}) for j in cached.jobs]
                attempts.append(ProviderAttempt(provider="snapshot", city=label, status="used", jobs=len(cached.jobs),
                                                detail=f"{age} h old"))
                warnings.append(f"Live providers unavailable for '{query}' in {city}; showing jobs fetched {age} h ago.")
                sources.add("snapshot")
                stale = True
                oldest = min(oldest or cached.fetched_at, cached.fetched_at)
            else:
                attempts.append(ProviderAttempt(provider="snapshot", city=label, status="missing"))
    cutoff = now - timedelta(days=MAX_JOB_AGE_DAYS)
    fresh = [j for j in collected if j.posted_at is None or j.posted_at >= cutoff]
    if len(fresh) < len(collected):
        warnings.append(f"Dropped {len(collected) - len(fresh)} listing(s) posted over {MAX_JOB_AGE_DAYS} days ago.")
    jobs = dedupe(fresh)
    if not jobs:
        warnings.append("No live or cached listings for these queries and cities.")
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    jobs.sort(key=lambda j: (j.posted_at or epoch, j.job_id), reverse=True)
    return jobs, attempts, list(dict.fromkeys(warnings)), sorted(sources), stale, oldest
