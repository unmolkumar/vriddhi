"""One real call per provider. Skipped when the provider's key isn't set (or JSearch isn't subscribed)."""
import os
from datetime import datetime, timezone

import pytest
from dotenv import load_dotenv

from src.providers import adzuna, jsearch
from src.providers.common import ProviderError

load_dotenv()
pytestmark = pytest.mark.live
NOW = datetime.now(timezone.utc)


def _check(jobs):
    assert jobs, "provider returned no jobs"
    for job in jobs:
        assert job.title and job.source_url and job.currency == "INR" and job.last_observed_at == NOW
        if job.salary_min is not None:
            assert 50_000 <= job.salary_min <= 20_000_000


@pytest.mark.skipif(not (os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY")), reason="Adzuna keys not set")
def test_adzuna_live():
    jobs, total = adzuna.search("data analyst", "Pune", now=NOW)
    _check(jobs)
    assert total and total >= len(jobs) and {j.source for j in jobs} == {"adzuna"}


@pytest.mark.skipif(not os.getenv("RAPIDAPI_KEY"), reason="RAPIDAPI_KEY not set")
def test_jsearch_live():
    try:
        jobs, _ = jsearch.search("data analyst", "Pune", now=NOW)
    except ProviderError as e:
        if e.status == "not_subscribed":
            pytest.skip("RAPIDAPI_KEY is not subscribed to JSearch")
        raise
    _check(jobs)
