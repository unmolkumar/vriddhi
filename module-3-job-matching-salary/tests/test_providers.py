"""Provider normalisation (recorded / documented samples), request parameters and error statuses."""
import copy

import httpx
import pytest

from conftest import NOW, json_response, load_mock, raises
from src.providers import adzuna, jsearch
from src.providers.common import (
    ProviderError, dedupe_key, description_quality, parse_experience, plausible_salary, work_mode,
)

ADZ = load_mock("adzuna_search_sample.json")
JS = load_mock("jsearch_search_sample.json")


# --- Adzuna (recorded response) ----------------------------------------------------------

def test_adzuna_normalises_recorded_response():
    jobs = [adzuna.normalise(r, NOW) for r in ADZ["results"]]
    first, with_salary = jobs[0], jobs[1]
    assert first.job_id == "adzuna:5905882064" and first.title == "Data Scientist" and first.company == "EXL"
    assert (first.location, first.location_raw) == ("Bengaluru", "Bangalore, Karnataka")
    assert first.employment_type == "full_time" and first.source == "adzuna" and first.currency == "INR"
    assert first.posted_at.isoformat() == "2026-10-01T21:08:38+00:00" and first.last_observed_at == NOW
    assert (first.salary_min, first.salary_max) == (None, None)        # missing salary stays missing
    assert (with_salary.salary_min, with_salary.salary_max) == (1000000, 1500000)
    assert not with_salary.salary_is_predicted
    assert all(j.source_url.startswith("https://www.adzuna.in/") for j in jobs)


def test_adzuna_predicted_salary_is_flagged():
    raw = copy.deepcopy(ADZ["results"][1])
    raw["salary_is_predicted"] = "1"
    assert adzuna.normalise(raw, NOW).salary_is_predicted is True


def test_adzuna_missing_fields():
    raw = copy.deepcopy(ADZ["results"][0])
    for key in ("company", "location", "description", "contract_time", "created"):
        raw.pop(key)
    job = adzuna.normalise(raw, NOW)
    assert (job.company, job.location, job.posted_at, job.employment_type) == (None, None, None, "unknown")
    assert job.description_quality == "missing"
    assert adzuna.normalise({**raw, "title": "  "}, NOW) is None and adzuna.normalise({"title": "x"}, NOW) is None


def test_adzuna_request_and_count(fake_http):
    fake_http.on("api.adzuna.com", json_response(ADZ))
    jobs, total = adzuna.search("data scientist", "Bengaluru", client=fake_http.client(), now=NOW)
    assert len(jobs) == 3 and total == ADZ["count"]
    req = fake_http.requests[0]
    assert req.url.path == "/v1/api/jobs/in/search/1"
    params = dict(req.url.params)
    assert params["what"] == "data scientist" and params["where"] == "Bangalore"   # Adzuna's name for Bengaluru
    assert params["app_id"] == "test-id" and params["max_days_old"] == str(adzuna.MAX_DAYS_OLD)


@pytest.mark.parametrize("handler, status", [
    (json_response({"error": "x"}, 500), "error"),
    (json_response({"error": "auth"}, 401), "error"),
    (raises(httpx.ReadTimeout), "timeout"),
    (raises(httpx.ConnectError), "error"),
    (lambda r: httpx.Response(200, text="<html>not json</html>"), "error"),
])
def test_adzuna_errors(fake_http, handler, status):
    fake_http.on("api.adzuna.com", handler)
    with pytest.raises(ProviderError) as e:
        adzuna.search("x", "Pune", client=fake_http.client(), now=NOW)
    assert e.value.status == status


def test_adzuna_not_configured(monkeypatch):
    monkeypatch.delenv("ADZUNA_APP_KEY")
    assert not adzuna.configured()
    with pytest.raises(ProviderError) as e:
        adzuna.search("x", "Pune", now=NOW)
    assert e.value.status == "not_configured"


def test_adzuna_histogram(fake_http):
    fake_http.on("api.adzuna.com", json_response(load_mock("adzuna_histogram_sample.json")))
    hist = adzuna.histogram("data scientist", "Bengaluru", client=fake_http.client())
    assert hist[1000000] == 191 and hist[0] == 117 and all(isinstance(k, int) for k in hist)


# --- JSearch (constructed from documented fields) --------------------------------------------

def test_jsearch_normalisation():
    jobs = {r["job_id"]: jsearch.normalise(r, NOW) for r in JS["data"]}
    senior = jobs["js-001"]
    assert senior.job_id == "jsearch:js-001" and senior.publisher == "LinkedIn" and senior.location == "Bengaluru"
    assert (senior.salary_min, senior.salary_max) == (2400000, 3600000)
    assert (senior.experience_min, senior.experience_max) == (4, 7)
    remote = jobs["js-002"]
    assert remote.work_mode == "remote" and remote.location == "Bengaluru"            # "Bangalore" normalised
    assert (remote.salary_min, remote.salary_max) == (720000, 1080000)                # monthly x 12
    assert remote.description_quality == "short"
    contract = jobs["js-003"]
    assert contract.employment_type == "contract" and (contract.salary_min, contract.salary_max) == (None, None)  # USD dropped
    assert contract.experience_min == 3
    assert jobs["js-004"] is None                                                     # expired listing


def test_jsearch_request_headers(fake_http):
    fake_http.on("jsearch.p.rapidapi.com", json_response(JS))
    jobs, total = jsearch.search("data scientist", "Bengaluru", client=fake_http.client(), now=NOW)
    assert len(jobs) == 4 and total is None
    req = fake_http.requests[0]
    assert req.headers["X-RapidAPI-Key"] == "test-rapid" and req.headers["X-RapidAPI-Host"] == "jsearch.p.rapidapi.com"
    assert dict(req.url.params) == {"query": "data scientist in Bangalore", "page": "1", "num_pages": "1",
                                    "country": "in", "date_posted": "month"}


def test_jsearch_not_subscribed(fake_http):
    fake_http.on("jsearch.p.rapidapi.com", json_response({"message": "You are not subscribed to this API."}, 403))
    with pytest.raises(ProviderError) as e:
        jsearch.search("x", "Pune", client=fake_http.client(), now=NOW)
    assert e.value.status == "not_subscribed"


# --- shared helpers ---------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("3-5 years of experience", (3, 5)), ("2 to 4 yrs", (2, 4)), ("4+ years in Python", (4, None)),
    ("at least 6 years", (6, None)), ("Minimum of 2 years", (2, None)), ("5 years of relevant experience", (5, None)),
    ("Founded 50 years ago", (None, None)), ("", (None, None)), ("Fresher role", (None, None)),
])
def test_parse_experience(text, expected):
    assert parse_experience(text) == expected


def test_salary_sanity_and_text_helpers():
    assert plausible_salary(1_200_000) == 1_200_000 and plausible_salary("850000.6") == 850001
    assert plausible_salary(12) is None and plausible_salary(10**9) is None and plausible_salary(None) is None
    assert description_quality("") == "missing" and description_quality("x" * 50) == "short"
    assert work_mode("Hybrid, 3 days in office") == "hybrid" and work_mode("Work from home") == "remote"
    assert work_mode("On-site in Pune") == "onsite" and work_mode("", is_remote=True) == "remote"


def test_dedupe_key_ignores_company_suffixes_and_case():
    assert dedupe_key("Data Scientist", "VY SYSTEMS PRIVATE LIMITED", "Bengaluru") == \
        dedupe_key("data scientist", "VY Systems Pvt Ltd", "bengaluru")
    assert dedupe_key("Data Scientist", "A", "Pune") != dedupe_key("Data Scientist", "A", "Mumbai")
