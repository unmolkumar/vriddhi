"""v2 listing retrieval: several queries x cities, cache-first, Adzuna first; JSearch only when Adzuna gave a city
nothing at all.

Same providers, SQLite cache and dedupe as v1 (job_fetcher), but cached under 'v2:<query>' and without v1's per-job
skill extraction (v2 matches whole job texts through module 2's match_texts instead).

JSearch has ~200 calls a month: per city, it is called once (with the first query) only when every query got no
listings from Adzuna (failed or empty) and no fresh cache, and only if use_jsearch.
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


def _call(provider, query: str, city: str, label: str, client, now, attempts: list[ProviderAttempt]):
    """(jobs, total) or None; records the attempt."""
    if not provider.configured():
        attempts.append(ProviderAttempt(provider=provider.NAME, city=label, status="not_configured"))
        return None
    try:
        jobs, total = provider.search(query, city, client=client, now=now)
    except ProviderError as e:
        attempts.append(ProviderAttempt(provider=provider.NAME, city=label, status=e.status, detail=e.detail))
        return None
    attempts.append(ProviderAttempt(provider=provider.NAME, city=label, status="ok" if jobs else "empty", jobs=len(jobs)))
    return (jobs, total) if jobs else None


def fetch(queries: list[str], cities: list[str], *, store: JobStore | None = None, client: httpx.Client | None = None,
          now: datetime | None = None, force_refresh: bool = False, use_jsearch: bool = True):
    """(jobs, attempts, warnings, sources, stale, oldest fetch time)."""
    now = now or datetime.now(timezone.utc)
    store = store or JobStore()
    ttl = timedelta(hours=ttl_hours())
    attempts: list[ProviderAttempt] = []
    warnings: list[str] = []
    collected: list[Job] = []
    sources: set[str] = set()
    stale, oldest = False, None

    def take(jobs: list[Job], source: str, fetched_at: datetime):
        nonlocal oldest
        collected.extend(jobs)
        sources.add(source)
        oldest = min(oldest or fetched_at, fetched_at)

    for city in cities:
        city_jobs, snapshots = 0, []
        for query in queries:
            key, label = KEY_PREFIX + query, f"{city} | {query}"
            cached = store.load(key, city)
            if cached and not force_refresh and now - cached.fetched_at <= ttl:
                attempts.append(ProviderAttempt(provider=cached.source, city=label, status="cache_hit", jobs=len(cached.jobs)))
                take(cached.jobs, cached.source, cached.fetched_at)
                city_jobs += len(cached.jobs)
                continue
            got = _call(adzuna, query, city, label, client, now, attempts)
            if got:
                store.save(key, city, adzuna.NAME, got[0], got[1], now)
                take(got[0], adzuna.NAME, now)
                city_jobs += len(got[0])
            elif cached:
                snapshots.append((query, cached))
        if city_jobs == 0 and use_jsearch and queries:
            query = queries[0]
            got = _call(jsearch, query, city, f"{city} | {query}", client, now, attempts)
            if got:
                store.save(KEY_PREFIX + query, city, jsearch.NAME, got[0], got[1], now)
                take(got[0], jsearch.NAME, now)
                city_jobs += len(got[0])
        if city_jobs == 0:
            for query, cached in snapshots:
                age = round((now - cached.fetched_at).total_seconds() / 3600, 1)
                take([j.model_copy(update={"stale": True, "age_hours": age}) for j in cached.jobs], "snapshot",
                     cached.fetched_at)
                attempts.append(ProviderAttempt(provider="snapshot", city=f"{city} | {query}", status="used",
                                                jobs=len(cached.jobs), detail=f"{age} h old"))
                warnings.append(f"Live providers unavailable for '{query}' in {city}; showing jobs fetched {age} h ago.")
                stale = True
            if not snapshots:
                attempts.append(ProviderAttempt(provider="snapshot", city=city, status="missing"))
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
