"""Job ranking: match + location preference + experience fit + salary attractiveness + recency.

Salary is one of five factors and never the only one (spec: Job Ranking). Formula in WORKING.md §7.
"""
from __future__ import annotations

from datetime import datetime

from src.models.schemas import Job

RANK_WEIGHTS = {"match": 0.55, "location": 0.15, "experience": 0.10, "salary": 0.10, "recency": 0.10}
RECENCY_FULL_DAYS = 3         # posted within 3 days: full recency
RECENCY_ZERO_DAYS = 45        # falls linearly to the floor by 45 days
RECENCY_FLOOR = 0.2
RECENCY_UNKNOWN = 0.5
STALE_RECENCY_FACTOR = 0.8    # snapshot jobs (served after the TTL) rank a little lower
SALARY_UNKNOWN = 0.5          # no usable posted salary: neutral, neither rewarded nor punished


def salary_attractiveness(job: Job, market_median: int | None) -> float:
    """0.5 at the market median, 1.0 at double it, 0.0 at zero; predicted salaries are ignored."""
    if job.salary_is_predicted or market_median is None:
        return SALARY_UNKNOWN
    values = [v for v in (job.salary_min, job.salary_max) if v is not None]
    if not values:
        return SALARY_UNKNOWN
    mid = sum(values) / len(values)
    return max(0.0, min(1.0, 0.5 + (mid - market_median) / (2 * market_median)))


def recency(job: Job, now: datetime) -> float:
    if job.posted_at is None:
        score = RECENCY_UNKNOWN
    else:
        days = (now - job.posted_at).total_seconds() / 86400
        if days <= RECENCY_FULL_DAYS:
            score = 1.0
        else:
            span = RECENCY_ZERO_DAYS - RECENCY_FULL_DAYS
            score = max(RECENCY_FLOOR, 1 - (1 - RECENCY_FLOOR) * (days - RECENCY_FULL_DAYS) / span)
    return score * (STALE_RECENCY_FACTOR if job.stale else 1.0)


def rank_components(job: Job, overall: float, location: float, experience: float, market_median: int | None,
                    now: datetime) -> dict[str, float]:
    return {"match": overall, "location": location, "experience": experience,
            "salary": salary_attractiveness(job, market_median), "recency": recency(job, now)}


def rank_score(components: dict[str, float]) -> float:
    return round(sum(RANK_WEIGHTS[k] * components[k] for k in RANK_WEIGHTS), 4)


def sort_key(score: float, overall: float, job: Job) -> tuple:
    """Stable, explainable order: rank score, then match, then newest, then job id."""
    posted = job.posted_at.timestamp() if job.posted_at else 0.0
    return (-score, -overall, -posted, job.job_id)
