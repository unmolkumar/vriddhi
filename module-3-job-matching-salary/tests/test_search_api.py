"""Search pipeline, unlocks-N-jobs, and the three step-2 endpoints, with mocked providers and module 2."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import src.engines.search as search_module
from conftest import NOW, json_response, load_mock, m2_extract_handler
from src.api.main import app
from src.engines import job_fetcher
from src.models.schemas import JobSearchRequest, export_json_schema
from src.providers import adzuna

ADZ = load_mock("adzuna_search_sample.json")
HIST = {int(k): v for k, v in load_mock("adzuna_histogram_sample.json")["histogram"].items()}
PROFILE = {"skills": [{"name": "python", "level": 4, "evidence": ["work_supported"]},
                      {"name": "sql", "level": 3, "evidence": ["work_supported"]},
                      {"name": "data_analysis", "level": 3, "evidence": ["work_supported"]}],
           "experience_years": 4, "education": ["B.Tech Computer Science"], "location": "Bengaluru"}


def skilled_adzuna(skills_per_job):
    """The recorded Adzuna jobs, with descriptions that module 2's stand-in turns into the given skills."""
    words = {"python": "Python", "sql": "SQL", "machine_learning": "machine learning", "aws": "AWS",
             "data_analysis": "data analysis", "pytorch": "PyTorch"}
    payload = json.loads(json.dumps(ADZ))
    for job, skills in zip(payload["results"], skills_per_job):
        job["description"] = "We need " + ", ".join(words[s] for s in skills) + "."
    return payload


@pytest.fixture
def wired(monkeypatch, store, fake_http):
    """Route the API's searches through the fake HTTP client and a temporary store."""
    real = job_fetcher.fetch_jobs

    def fetch(role, location, **kw):
        kw.update(store=store, client=fake_http.client(), m2_client=fake_http.client(), now=NOW)
        return real(role, location, **kw)
    monkeypatch.setattr(search_module, "fetch_jobs", fetch)
    monkeypatch.setattr(adzuna, "histogram", lambda role, city, client=None: HIST)
    fake_http.on("127.0.0.1", m2_extract_handler)
    return fake_http


def search(body):
    return TestClient(app).post("/api/v1/jobs/search", json=body)


def test_search_end_to_end(wired):
    wired.on("api.adzuna.com", json_response(skilled_adzuna([
        ["python", "sql", "data_analysis"], ["python", "machine_learning", "aws", "sql"], ["pytorch"]])))
    resp = search({"target_role": "Data Scientist", "profile": PROFILE, "typical_experience": {"min": 2, "max": 6}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cities"] == ["Bengaluru"] and body["total_found"] == 3 and body["sources"] == ["adzuna"]
    assert [j["rank"] for j in body["jobs"]] == [1, 2, 3]
    top = body["jobs"][0]
    assert top["title"] == "Data Scientist" and top["matched_skills"] == ["python", "sql", "data_analysis"]
    assert top["classification"] in ("Strong", "Good") and 0 <= top["match_score"] <= 100
    assert set(top["match"]) >= {"skills", "experience", "weights", "skill_method"}
    sparse = next(j for j in body["jobs"] if j["inferred_skills"])        # the PyTorch-only job got topped up
    assert "python" in sparse["inferred_skills"] and sparse["skills_source"] == "m2"
    assert body["role_market_profile"]["top_skills"][0] == {"skill": "python", "share": 0.667, "jobs": 2}
    assert body["market_salary"]["sources_used"] == ["adzuna_histogram"]  # 1 usable posted salary < 5
    assert body["negotiation"]["job_id"] == top["job_id"] and body["negotiation"]["recommended_target"].endswith("LPA")
    assert body["provider_trace"] == [{"provider": "adzuna", "city": "Bengaluru", "status": "ok", "detail": None, "jobs": 3}]
    assert all(j["experience_source"] in ("posting", "request") for j in body["jobs"])


def test_unlocks_n_jobs(wired):
    wired.on("api.adzuna.com", json_response(skilled_adzuna([
        ["python", "sql", "machine_learning"], ["python", "machine_learning", "sql"], ["python", "sql", "aws"]])))
    # A fresher (0 years against a 3-6 band): Partial today; machine learning lifts the two ML jobs to Good.
    body = search({"target_role": "Data Scientist", "location": "Bengaluru", "experience_years": 0,
                   "typical_experience": {"min": 3, "max": 6}, "skills": ["python", "sql"],
                   "gap_analysis": {"critical_missing": ["machine_learning"]}}).json()
    unlocks = {u["skill"]: u for u in body["skill_unlocks"]}
    ml = unlocks["machine_learning"]
    before = {j["job_id"]: j["classification"] for j in body["jobs"]}
    assert ml["jobs_unlocked"] == 2 and all(before[j] not in ("Good", "Strong") for j in ml["example_job_ids"])
    assert ml["message"].startswith("Learning machine learning would move") and "in Bengaluru" in ml["message"]
    assert body["skill_unlocks"][0]["jobs_unlocked"] >= body["skill_unlocks"][-1]["jobs_unlocked"]


def test_unlock_candidates_come_from_market_profile_without_gap(wired):
    wired.on("api.adzuna.com", json_response(skilled_adzuna([["python", "aws"], ["python", "aws", "sql"], ["aws"]])))
    body = search({"target_role": "Data Scientist", "location": "Bengaluru", "skills": ["python"]}).json()
    assert [u["skill"] for u in body["skill_unlocks"]] == ["aws", "sql"] or         sorted(u["skill"] for u in body["skill_unlocks"]) == ["aws", "sql"]   # profile skills the candidate lacks


def test_employment_filter_and_module_2_down(wired):
    wired.handlers.pop("127.0.0.1")
    wired.on("api.adzuna.com", json_response(ADZ))
    body = search({"target_role": "Data Scientist", "location": "Bangalore", "skills": ["python"],
                   "employment_type": ["contract"]}).json()
    assert body["total_found"] == 0 and any("employment type" in w for w in body["warnings"])
    body = search({"target_role": "Data Scientist", "location": "Bangalore", "skills": ["python"]}).json()
    assert all(j["match"]["skill_method"] == "keywords" for j in body["jobs"])
    assert any("Module 2" in w for w in body["warnings"])


def test_salary_estimate_endpoint(wired):
    wired.on("api.adzuna.com", json_response(ADZ))
    body = TestClient(app).post("/api/v1/salary/estimate", json={"target_role": "Data Scientist", "profile": PROFILE,
                                                                 "market_baseline": {"median_salary_inr_lpa": 18.5}}).json()
    assert body["market_salary"]["display"] == "Estimated market range: 5-25 LPA"
    assert body["candidate_value"]["estimated_min"] and body["candidate_value"]["reasons"]


def test_negotiate_endpoint(wired):
    wired.on("api.adzuna.com", json_response(ADZ))
    client = TestClient(app)
    base = {"target_role": "Data Scientist", "profile": PROFILE}
    by_job = client.post("/api/v1/salary/negotiate", json={**base, "job_id": "adzuna:5905444031"}).json()
    offered = client.post("/api/v1/salary/negotiate", json={**base, "posted_salary_min": 900000, "posted_salary_max": 1100000}).json()
    no_salary = client.post("/api/v1/salary/negotiate", json={**base, "job_id": "adzuna:5905882064"}).json()
    assert by_job["negotiation"]["job_id"] == "adzuna:5905444031" and by_job["match_score"] is not None
    assert by_job["negotiation"]["posted_salary"] == "10-15 LPA"
    assert offered["negotiation"]["posted_salary"] == "9-11 LPA"
    assert no_salary["negotiation"]["posted_salary"] is None and no_salary["negotiation"]["reasons"][0].startswith("No posted salary")
    missing = client.post("/api/v1/salary/negotiate", json={**base, "job_id": "adzuna:nope"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "JOB_NOT_FOUND"


@pytest.mark.parametrize("body", [
    {"target_role": "X", "skills": ["python"]},                                   # no location anywhere
    {"target_role": "", "location": "Pune"},
    {"target_role": "X", "location": "Pune", "typical_experience": {"min": 8, "max": 2}},
    {"target_role": "X", "location": "Pune", "weights": {"skills": 0, "experience": 0, "education": 0, "location": 0,
                                                         "seniority": 0, "preference": 0}},
], ids=["no_location", "empty_role", "bad_band", "zero_weights"])
def test_invalid_requests(body):
    resp = search(body)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "INVALID_REQUEST"


def test_readable_skill_names():
    from src.engines.search import readable
    assert [readable(s) for s in ("ai", "aws", "sql", "machine_learning", "python")] == \
        ["AI", "AWS", "SQL", "machine learning", "python"]


def test_schema_export_is_current():
    from pathlib import Path
    committed = json.loads((Path(__file__).parent.parent / "src" / "models" / "schema_m3.json").read_text(encoding="utf-8"))
    assert committed == export_json_schema(), "run: python -m src.models.schemas"
    assert {"Job", "JobSearchRequest", "JobSearchResponse", "NegotiateResponse", "ErrorResponse"} <= set(committed["$defs"])


def test_openapi_lists_endpoints():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert {"/api/v1/jobs/search", "/api/v1/salary/estimate", "/api/v1/salary/negotiate", "/api/v1/health"} <= set(paths)


def test_request_model_examples():
    JobSearchRequest(target_role="Backend Developer", location="Bengaluru", skills=["python", "sql", "docker"],
                     experience_years=2)                                          # the spec's input example
