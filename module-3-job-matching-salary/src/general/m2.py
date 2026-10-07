"""Module 2 over REST: POST /api/v2/skills/match_texts (one user's evidence vs a page of jobs). No module 2 imports."""
from __future__ import annotations

import httpx

from src.engines.m2_client import M2Unavailable, base_url

TIMEOUT = httpx.Timeout(60.0, connect=1.0)     # a cold page prepares occupations (module 2 caches them after)
MAX_JOBS = 50                                    # module 2's limit per call


def match_texts(evidence: dict, jobs: list[dict], *, client: httpx.Client | None = None) -> list[dict]:
    """evidence: {free_text?, skills?, profile?, experience_years?}; jobs: [{job_id, job_text, job_title?, soc_code?}].
    Returns module 2's results in job order (pages of MAX_JOBS). Raises M2Unavailable."""
    out = []
    for i in range(0, len(jobs), MAX_JOBS):
        try:
            resp = (client or httpx).post(f"{base_url()}/api/v2/skills/match_texts", timeout=TIMEOUT,
                                          json={**evidence, "jobs": jobs[i:i + MAX_JOBS]})
        except httpx.HTTPError as e:
            raise M2Unavailable(type(e).__name__) from None
        if resp.status_code != 200:
            raise M2Unavailable(f"HTTP {resp.status_code}: {resp.text[:200]}")
        out += resp.json()["results"]
    return out
