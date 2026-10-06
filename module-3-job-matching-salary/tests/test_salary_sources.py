"""Module 1's on-site/remote percentile split, the remote preference, and data age on jobs and responses."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import NOW, json_response, m2_extract_handler, make_job
from test_followups import DS
from test_polish import ADZ, HIST, wired  # noqa: F401  (fixture)
from src.api.main import app
from src.engines import job_fetcher
from src.engines import search as search_module
from src.engines.salary import M1_SENIOR_CAP_YEARS, MIN_PERCENTILE_SAMPLE, estimate_market, m1_salary
from src.engines.search import age_text
from src.models.schemas import JobSearchRequest, MarketPercentiles

L = 100_000


def _band(p25, p50, p75, n):
    return {"p25": p25, "p50": p50, "p75": p75, "currency": "INR_LPA", "sample_size": n}


# Module 1's Data Scientist payload after the on-site/remote split (its WORKING.md §7.7).
NEW = MarketPercentiles.model_validate({
    "overall_inr_lpa": _band(10.5, 17.5, 27.5, 199),
    "remote_inr_lpa": _band(10.6, 17.5, 35.5, 89),
    "overall_usd": {"p25": 57083.0, "p50": 90976.0, "p75": 140117.0, "currency": "USD", "sample_size": 2808},
    "by_experience_inr_lpa": {"entry": _band(6.6, 8.8, 12.3, 69), "mid": _band(15.0, 21.1, 30.0, 107),
                              "senior": _band(21.8, 32.5, 58.9, 63)},
    "by_city_inr_lpa": {"Bengaluru": _band(12.0, 15.0, 22.8, 45)},
})


def test_new_shape_uses_on_site_buckets_by_default():
    m1 = m1_salary(NEW, 4, "Bengaluru")
    assert (m1.p25, m1.p50, m1.p75, m1.sample_size, m1.method) == (15 * L, 21.1 * L, 30 * L, 107, "experience_bucket")
    assert "remote" not in m1.detail
    assert m1_salary(NEW, 9, "Pune").band.max == M1_SENIOR_CAP_YEARS == 12


def test_remote_preference_uses_the_remote_band():
    m1 = m1_salary(NEW, 4, "Bengaluru", remote_only=True)
    assert (m1.p25, m1.p50, m1.p75, m1.sample_size, m1.method) == (10.6 * L, 17.5 * L, 35.5 * L, 89, "remote")
    assert m1.band is None and estimate_market([], percentiles=m1).method == "remote"
    thin = NEW.model_copy(deep=True)
    thin.remote_inr_lpa.sample_size = MIN_PERCENTILE_SAMPLE - 1
    m1 = m1_salary(thin, 4, "Bengaluru", remote_only=True)
    assert m1.method == "experience_bucket" and "remote band not used (29 points" in m1.detail


def test_old_shape_still_works_and_remote_preference_falls_back_to_on_site():
    assert DS.remote_inr_lpa is None
    m1 = m1_salary(DS, 4, "Bengaluru", remote_only=True)
    assert m1.method == "experience_bucket" and m1.p50 == 22.1 * L and "no remote band from module 1" in m1.detail


@pytest.mark.parametrize("modes, method, median", [(["remote"], "remote", 17.5 * L),
                                                   (["remote", "hybrid"], "experience_bucket", 21.1 * L),
                                                   ([], "experience_bucket", 21.1 * L)])
def test_estimate_endpoint_reads_the_work_mode_preference(wired, modes, method, median):
    wired.on("api.adzuna.com", json_response(ADZ)).on("127.0.0.1", m2_extract_handler)
    market = TestClient(app).post("/api/v1/salary/estimate", json={
        "target_role": "Data Scientist", "location": "Bengaluru", "skills": ["python"], "experience_years": 4,
        "work_mode": modes, "market_salary_percentiles": NEW.model_dump()}).json()["market_salary"]
    assert market["method"] == method and market["estimated_median"] == median


@pytest.mark.parametrize("hours, text", [(10, "fetched 10 h ago"), (0.4, "fetched 24 min ago"),
                                         (14.26, "fetched 14.3 h ago"), (None, None)])
def test_age_text(hours, text):
    assert age_text(hours) == text


def test_search_reports_when_its_data_was_fetched(monkeypatch, store, fake_http):
    fetched_at = NOW - timedelta(hours=10)
    jobs = [make_job(job_id="adzuna:1", last_observed_at=fetched_at),
            make_job(job_id="adzuna:2", last_observed_at=NOW - timedelta(hours=2))]
    result = job_fetcher.FetchResult(role="Data Scientist", cities=["Bengaluru"], jobs=jobs, sources=["adzuna"],
                                     from_cache=True)
    monkeypatch.setattr(search_module, "fetch_jobs", lambda *a, **k: result)
    req = JobSearchRequest(target_role="Data Scientist", location="Bengaluru", skills=["python"], experience_years=4)
    resp = search_module.run_search(req, store=store, client=fake_http.client(), m2_client=fake_http.client(),
                                    now=NOW, histogram_fn=lambda: HIST).response
    assert not resp.stale                                   # fresh within the TTL, but its age is still shown
    assert (resp.fetched_at, resp.age_hours, resp.data_age) == (fetched_at, 10.0, "fetched 10 h ago")
    assert sorted(j.age_hours for j in resp.jobs) == [2.0, 10.0]
    assert {j.fetched_at for j in resp.jobs} == {fetched_at, NOW - timedelta(hours=2)}
