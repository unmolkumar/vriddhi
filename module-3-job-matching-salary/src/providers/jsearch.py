"""JSearch on RapidAPI (fallback provider). Aggregates Google for Jobs: LinkedIn, Naukri, Indeed, company sites.

Docs: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch . The account must be subscribed to JSearch;
an unsubscribed key gets HTTP 403 "You are not subscribed to this API", reported as status not_subscribed.
Uses GET /search-v2 (the old /search now returns 404 "Endpoint '/search' does not exist"). Checked against a
recorded response (tests/mocks/jsearch_search_v2_sample.json): jobs are under data.jobs, with a cursor.
Fields used: job_id, job_title, employer_name, job_description (full text, often several thousand chars),
job_city, job_state, job_employment_types (codes) / job_employment_type (text), job_is_remote,
job_posted_at_datetime_utc, job_min_salary, job_max_salary, job_salary_period, job_salary_string (currency),
job_publisher, job_apply_link. Older fields (job_salary_currency, job_offer_expiration_datetime_utc,
job_required_experience) are still honoured when present. All optional.
"""
from __future__ import annotations

import os
from datetime import datetime

import httpx

from src.engines.locations import normalise_city, provider_query_name
from src.models.schemas import Job
from src.providers.common import (
    ProviderError, description_quality, employment_from_text, parse_datetime, parse_experience,
    plausible_salary, work_mode,
)

URL = "https://jsearch.p.rapidapi.com/search-v2"
HOST = "jsearch.p.rapidapi.com"
DATE_POSTED = "month"
TIMEOUT_S = 30.0  # /search-v2 runs a Google for Jobs search: measured 12.6 s, and over 20 s once; Adzuna keeps 5 s
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
            params={"query": f"{role} in {provider_query_name(city)}", "country": "in", "date_posted": DATE_POSTED})
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
    payload = data.get("data")
    records = payload.get("jobs") if isinstance(payload, dict) else payload  # v2: {"cursor", "jobs"}; v1: a list
    jobs = [j for j in (normalise(r, now) for r in records or []) if j]
    return jobs, None  # JSearch doesn't report a total count


def _currency(raw: dict) -> str:
    """INR unless the record says otherwise (job_salary_currency, or a symbol in job_salary_string)."""
    if raw.get("job_salary_currency"):
        return str(raw["job_salary_currency"]).upper()
    text = raw.get("job_salary_string") or ""
    if "$" in text or "USD" in text.upper():
        return "USD"
    if "€" in text or "£" in text:
        return "OTHER"
    return "INR"


def _employment(raw: dict) -> str:
    codes = raw.get("job_employment_types") or [raw.get("job_employment_type") or ""]
    return next((EMPLOYMENT[c.upper()] for c in codes if isinstance(c, str) and c.upper() in EMPLOYMENT), "unknown")


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
    salary_ok = _currency(raw) == "INR"
    lo = _annual(raw.get("job_min_salary"), raw.get("job_salary_period")) if salary_ok else None
    hi = _annual(raw.get("job_max_salary"), raw.get("job_salary_period")) if salary_ok else None
    if lo and hi and lo > hi:
        lo, hi = hi, lo
    city_raw = raw.get("job_city")
    return Job(
        job_id=f"jsearch:{raw['job_id']}", title=title, company=raw.get("employer_name"),
        description=description, description_quality=description_quality(description),
        location=normalise_city(city_raw), location_raw=", ".join(p for p in (city_raw, raw.get("job_state")) if p) or None,
        employment_type=employment_from_text(title) or _employment(raw),
        work_mode=work_mode(f"{title}\n{description}", raw.get("job_is_remote")),
        experience_min=exp_min, experience_max=exp_max, salary_min=lo, salary_max=hi,
        posted_at=parse_datetime(raw.get("job_posted_at_datetime_utc") or raw.get("job_posted_at_timestamp")),
        source=NAME, publisher=raw.get("job_publisher"), source_url=raw.get("job_apply_link"),
        last_observed_at=now,
    )


SALARY_URL = "https://jsearch.p.rapidapi.com/estimated-salary"
SALARY_TIMEOUT_S = 15.0  # measured 3.2 s
EXPERIENCE_BUCKETS = [(1, "LESS_THAN_ONE"), (4, "ONE_TO_THREE"), (7, "FOUR_TO_SIX"), (10, "SEVEN_TO_NINE"),
                      (15, "TEN_TO_FOURTEEN")]  # upper bound (exclusive) -> JSearch bucket; 15+ -> ABOVE_FIFTEEN


BUCKET_BANDS = {"LESS_THAN_ONE": (0.0, 1.0), "ONE_TO_THREE": (1.0, 3.0), "FOUR_TO_SIX": (4.0, 6.0),
                "SEVEN_TO_NINE": (7.0, 9.0), "TEN_TO_FOURTEEN": (10.0, 14.0), "ABOVE_FIFTEEN": (15.0, 25.0)}


def experience_bucket(years: float | None) -> str:
    if years is None:
        return "ALL"
    return next((name for upper, name in EXPERIENCE_BUCKETS if years < upper), "ABOVE_FIFTEEN")


def estimated_salary(role: str, city: str, bucket: str, *, client: httpx.Client | None = None) -> dict | None:
    """Glassdoor-backed salary estimate (GET /estimated-salary). Checked against a recorded response
    (tests/mocks/jsearch_salary_sample.json): data[].min_salary, median_salary, max_salary, salary_period,
    salary_currency, salary_count, publisher_name, confidence, salaries_updated_at.
    Returns a normalised dict in INR per year, or None when JSearch has no usable figure."""
    if not configured():
        raise ProviderError("not_configured", "RAPIDAPI_KEY not set")
    try:
        resp = (client or httpx).get(
            SALARY_URL, timeout=SALARY_TIMEOUT_S,
            headers={"X-RapidAPI-Key": os.environ["RAPIDAPI_KEY"].strip(), "X-RapidAPI-Host": HOST},
            params={"job_title": role, "location": f"{provider_query_name(city)}, India", "location_type": "CITY",
                    "years_of_experience": bucket})
    except httpx.TimeoutException:
        raise ProviderError("timeout", f"no response in {SALARY_TIMEOUT_S:g} s") from None
    except httpx.HTTPError as e:
        raise ProviderError("error", type(e).__name__) from None
    if resp.status_code == 403 and "not subscribed" in resp.text.lower():
        raise ProviderError("not_subscribed", "subscribe this RapidAPI key to JSearch")
    if resp.status_code != 200:
        raise ProviderError("error", f"HTTP {resp.status_code}")
    try:
        rows = resp.json().get("data") or []
    except ValueError:
        raise ProviderError("error", "response was not JSON") from None
    for row in rows:
        factor = PERIOD_TO_YEAR.get((row.get("salary_period") or "").upper())
        if (row.get("salary_currency") or "").upper() != "INR" or factor is None:
            continue
        lo, mid, hi = (plausible_salary((row.get(k) or 0) * factor) for k in ("min_salary", "median_salary", "max_salary"))
        if lo and mid and hi and lo <= mid <= hi:
            return {"min": lo, "median": mid, "max": hi, "sample_size": int(row.get("salary_count") or 0),
                    "publisher": row.get("publisher_name"), "confidence": (row.get("confidence") or "").upper(),
                    "updated_at": row.get("salaries_updated_at"), "bucket": bucket}
    return None
