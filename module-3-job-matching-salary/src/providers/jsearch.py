"""JSearch on RapidAPI (fallback provider). Aggregates Google for Jobs: LinkedIn, Naukri, Indeed, company sites.

Docs: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch . The account must be subscribed to JSearch;
an unsubscribed key gets HTTP 403 "You are not subscribed to this API", reported as status not_subscribed.
Fields used: data[].job_id, job_title, employer_name, job_description, job_city, job_state, job_country,
job_employment_type, job_is_remote, job_posted_at_datetime_utc, job_offer_expiration_datetime_utc,
job_min_salary, job_max_salary, job_salary_period, job_salary_currency, job_publisher, job_apply_link,
job_required_experience.required_experience_in_months. All optional.
"""
from __future__ import annotations

import os
from datetime import datetime

import httpx

from src.engines.locations import normalise_city, provider_query_name
from src.models.schemas import Job
from src.providers.common import (
    TIMEOUT_S, ProviderError, description_quality, employment_from_text, parse_datetime, parse_experience,
    plausible_salary, work_mode,
)

URL = "https://jsearch.p.rapidapi.com/search"
HOST = "jsearch.p.rapidapi.com"
DATE_POSTED = "month"
NAME = "jsearch"
EMPLOYMENT = {"FULLTIME": "full_time", "PARTTIME": "part_time", "CONTRACTOR": "contract", "INTERN": "internship",
              "TEMPORARY": "temporary"}
PERIOD_TO_YEAR = {"YEAR": 1, "MONTH": 12}  # hourly/weekly INR figures are too unreliable to annualise


def configured() -> bool:
    return bool(os.getenv("RAPIDAPI_KEY", "").strip())


def search(role: str, city: str, *, client: httpx.Client | None = None, now: datetime) -> tuple[list[Job], int | None]:
    if not configured():
        raise ProviderError("not_configured", "RAPIDAPI_KEY not set")
    try:
        resp = (client or httpx).get(
            URL, timeout=TIMEOUT_S,
            headers={"X-RapidAPI-Key": os.environ["RAPIDAPI_KEY"].strip(), "X-RapidAPI-Host": HOST},
            params={"query": f"{role} in {provider_query_name(city)}", "page": 1, "num_pages": 1,
                    "country": "in", "date_posted": DATE_POSTED})
    except httpx.TimeoutException:
        raise ProviderError("timeout", f"no response in {TIMEOUT_S:g} s") from None
    except httpx.HTTPError as e:
        raise ProviderError("error", type(e).__name__) from None
    if resp.status_code == 403 and "not subscribed" in resp.text.lower():
        raise ProviderError("not_subscribed", "subscribe this RapidAPI key to JSearch")
    if resp.status_code != 200:
        raise ProviderError("error", f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        raise ProviderError("error", "response was not JSON") from None
    jobs = [j for j in (normalise(r, now) for r in data.get("data") or []) if j]
    return jobs, None  # JSearch doesn't report a total count


def _annual(value, period: str | None) -> int | None:
    factor = PERIOD_TO_YEAR.get((period or "YEAR").upper())
    if factor is None or value is None:
        return None
    try:
        return plausible_salary(float(value) * factor)
    except (TypeError, ValueError):
        return None


def normalise(raw: dict, now: datetime) -> Job | None:
    title = (raw.get("job_title") or "").strip()
    if not raw.get("job_id") or not title:
        return None
    expires = parse_datetime(raw.get("job_offer_expiration_datetime_utc"))
    if expires and expires < now:
        return None  # expired listing
    description = raw.get("job_description") or ""
    exp_min, exp_max = parse_experience(f"{title}\n{description}")
    months = (raw.get("job_required_experience") or {}).get("required_experience_in_months")
    if exp_min is None and isinstance(months, (int, float)) and months > 0:
        exp_min = round(months / 12, 1)
    salary_ok = (raw.get("job_salary_currency") or "INR").upper() == "INR"
    lo = _annual(raw.get("job_min_salary"), raw.get("job_salary_period")) if salary_ok else None
    hi = _annual(raw.get("job_max_salary"), raw.get("job_salary_period")) if salary_ok else None
    if lo and hi and lo > hi:
        lo, hi = hi, lo
    city_raw = raw.get("job_city")
    return Job(
        job_id=f"jsearch:{raw['job_id']}", title=title, company=raw.get("employer_name"),
        description=description, description_quality=description_quality(description),
        location=normalise_city(city_raw), location_raw=", ".join(p for p in (city_raw, raw.get("job_state")) if p) or None,
        employment_type=employment_from_text(title) or EMPLOYMENT.get((raw.get("job_employment_type") or "").upper(), "unknown"),
        work_mode=work_mode(f"{title}\n{description}", raw.get("job_is_remote")),
        experience_min=exp_min, experience_max=exp_max, salary_min=lo, salary_max=hi,
        posted_at=parse_datetime(raw.get("job_posted_at_datetime_utc") or raw.get("job_posted_at_timestamp")),
        source=NAME, publisher=raw.get("job_publisher"), source_url=raw.get("job_apply_link"),
        last_observed_at=now,
    )
