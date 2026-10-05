"""Adzuna India (primary provider). Docs: https://developer.adzuna.com/docs/search

Field names checked against a recorded response (tests/mocks/adzuna_search_sample.json):
results[].id, title, description (truncated ~500 chars), company.display_name, location.display_name,
location.area[], contract_time, contract_type, created, redirect_url, salary_min, salary_max (absent when
unknown), salary_is_predicted ("1" = Adzuna's own estimate).
"""
from __future__ import annotations

import os
from datetime import datetime

import httpx

from src.engines.locations import city_from_parts, provider_query_name
from src.models.schemas import Job
from src.providers.common import (
    TIMEOUT_S, ProviderError, description_quality, employment_from_text, parse_datetime, parse_experience,
    plausible_salary, work_mode,
)

BASE_URL = "https://api.adzuna.com/v1/api/jobs/in"
RESULTS_PER_PAGE = 50
MAX_DAYS_OLD = 30
NAME = "adzuna"


def configured() -> bool:
    return bool(os.getenv("ADZUNA_APP_ID", "").strip() and os.getenv("ADZUNA_APP_KEY", "").strip())


def _auth() -> dict:
    if not configured():
        raise ProviderError("not_configured", "ADZUNA_APP_ID / ADZUNA_APP_KEY not set")
    return {"app_id": os.environ["ADZUNA_APP_ID"].strip(), "app_key": os.environ["ADZUNA_APP_KEY"].strip()}


def _get(client: httpx.Client | None, path: str, params: dict) -> dict:
    try:
        resp = (client or httpx).get(f"{BASE_URL}/{path}", params={**_auth(), **params,
                                                                    "content-type": "application/json"},
                                     timeout=TIMEOUT_S)
    except httpx.TimeoutException:
        raise ProviderError("timeout", f"no response in {TIMEOUT_S:g} s") from None
    except httpx.HTTPError as e:
        raise ProviderError("error", type(e).__name__) from None
    if resp.status_code != 200:
        raise ProviderError("error", f"HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError:
        raise ProviderError("error", "response was not JSON") from None


def search(role: str, city: str, *, client: httpx.Client | None = None, now: datetime) -> tuple[list[Job], int | None]:
    data = _get(client, "search/1", {"what": role, "where": provider_query_name(city),
                                     "results_per_page": RESULTS_PER_PAGE, "max_days_old": MAX_DAYS_OLD})
    jobs = [j for j in (normalise(r, now) for r in data.get("results") or []) if j]
    return jobs, data.get("count")


def histogram(role: str, city: str, *, client: httpx.Client | None = None) -> dict[int, int]:
    """Salary distribution for role + city: {lower bound INR: vacancies}. Used for salary (step 2)."""
    data = _get(client, "histogram", {"what": role, "where": provider_query_name(city)})
    return {int(float(k)): int(v) for k, v in (data.get("histogram") or {}).items()}


def normalise(raw: dict, now: datetime) -> Job | None:
    title = (raw.get("title") or "").strip()
    if not raw.get("id") or not title:
        return None
    description = raw.get("description") or ""
    location = raw.get("location") or {}
    exp_min, exp_max = parse_experience(f"{title}\n{description}")
    contract_time, contract_type = raw.get("contract_time"), raw.get("contract_type")
    employment = (employment_from_text(title)
                  or {"full_time": "full_time", "part_time": "part_time"}.get(contract_time)
                  or {"contract": "contract", "permanent": "full_time"}.get(contract_type) or "unknown")
    lo, hi = plausible_salary(raw.get("salary_min")), plausible_salary(raw.get("salary_max"))
    if lo and hi and lo > hi:
        lo, hi = hi, lo
    return Job(
        job_id=f"adzuna:{raw['id']}", title=title, company=(raw.get("company") or {}).get("display_name"),
        description=description, description_quality=description_quality(description),
        location=city_from_parts(location.get("area") or []) if location.get("area") else None,
        location_raw=location.get("display_name"), employment_type=employment,
        work_mode=work_mode(f"{title}\n{description}"), experience_min=exp_min, experience_max=exp_max,
        salary_min=lo, salary_max=hi, salary_is_predicted=str(raw.get("salary_is_predicted", "0")) == "1",
        posted_at=parse_datetime(raw.get("created")), source=NAME, source_url=raw.get("redirect_url"),
        last_observed_at=now,
    )
