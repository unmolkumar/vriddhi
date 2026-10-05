"""Demo-quality behaviour: inferred-skill penalty, typed skills via module 2, broad categories, salary sources."""
import json
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

import src.engines.search as search_module
from conftest import NOW, json_response, load_mock, m2_extract_handler, make_job
from src.api.main import app
from src.engines import job_fetcher
from src.engines.job_store import JobStore
from src.engines.market_profile import BROAD_SKILL_IDS, build_profile, concrete_children
from src.engines.matching import INFERRED_PENALTY, match_job, skill_component, skills_confidence
from src.engines.salary import candidate_value, estimate_market
from src.models.schemas import CandidateProfile, CandidateSkill, ExperienceBand, JobSearchRequest, MarketPercentiles
from src.providers import adzuna, jsearch
from src.providers.common import ProviderError

L = 100_000
ADZ = load_mock("adzuna_search_sample.json")
HIST = {int(k): v for k, v in load_mock("adzuna_histogram_sample.json")["histogram"].items()}
JS_SALARY = load_mock("jsearch_salary_sample.json")


def candidate(*names, years=4.0):
    return CandidateProfile(skills=[CandidateSkill(name=n, level=3) for n in names], experience_years=years,
                            location="Bengaluru")


# --- inferred-skill penalty --------------------------------------------------------------------

def test_fully_inferred_job_cannot_outrank_a_comparable_job_with_real_skills():
    real = make_job(job_id="adzuna:real", skills=["python", "sql", "spark"])
    inferred = make_job(job_id="adzuna:inferred", skills=["python", "sql", "spark"],
                        inferred_skills=["python", "sql", "spark"], skills_inferred=True)
    c = candidate("python", "sql")
    real_score, inferred_score = skill_component(real, c)[0], skill_component(inferred, c)[0]
    assert real_score == pytest.approx(2 / 3)
    assert inferred_score == pytest.approx(2 / 3 * (1 - INFERRED_PENALTY))          # same skills, all inferred
    assert match_job(real, c).overall > match_job(inferred, c).overall


def test_skills_confidence_labels():
    assert skills_confidence(make_job(skills=["python"])) == "extracted"
    assert skills_confidence(make_job(skills=["python", "sql"], inferred_skills=["sql"])) == "partly_inferred"
    assert skills_confidence(make_job(skills=["sql"], inferred_skills=["sql"])) == "inferred"


# --- broad categories ---------------------------------------------------------------------------

def live_like_jobs():
    """Mirrors the live Data Scientist / Bengaluru sample: broad ids (ai, data_science) dominate the profile."""
    parents = {"machine_learning": "ai", "generative_ai": "ai", "pytorch": "deep_learning", "llm": "generative_ai",
               "data_science": "machine_learning"}
    rows = [["ai", "data_science", "machine_learning", "pytorch"], ["ai", "data_science", "llm"],
            ["ai", "machine_learning", "statistics"], ["ai", "generative_ai", "llm"], ["data_science", "python"],
            ["ai", "machine_learning", "pytorch", "llm"]]
    return [make_job(job_id=f"adzuna:{i}", title="Data Scientist", skills=s, skill_parents=parents,
                     experience_min=3, experience_max=6) for i, s in enumerate(rows)]


def test_broad_ids_are_never_inferred_or_listed_first():
    jobs = live_like_jobs()
    profile = build_profile("Data Scientist", ["Bengaluru"], jobs)
    assert profile.top_skills[0].skill == "ai"                          # still visible as a demand signal
    missing = skill_component(jobs[0], candidate("python"))[2]
    assert missing[0] not in BROAD_SKILL_IDS and set(missing[-2:]) == {"ai", "data_science"}
    assert set(concrete_children("ai", jobs, profile)[:2]) == {"machine_learning", "llm"}   # most asked (3 jobs each)


def test_unlocks_contain_no_category_ids(monkeypatch, store, fake_http):
    jobs = live_like_jobs()
    fetched = job_fetcher.FetchResult(role="Data Scientist", cities=["Bengaluru"], jobs=jobs, sources=["adzuna"])
    monkeypatch.setattr(search_module, "fetch_jobs", lambda *a, **k: fetched)
    req = JobSearchRequest(target_role="Data Scientist", location="Bengaluru",
                           profile={"skills": [{"name": "python", "level": 4}, {"name": "statistics", "level": 3}],
                                    "experience_years": 4, "location": "Bengaluru"},
                           gap_analysis={"critical_missing": ["ai", "data_science"]})
    resp = search_module.run_search(req, store=store, client=fake_http.client(), m2_client=fake_http.client(),
                                    now=NOW, histogram_fn=lambda: HIST).response
    skills = [u.skill for u in resp.skill_unlocks]
    assert skills and not set(skills) & BROAD_SKILL_IDS                 # ai -> machine_learning, never "ai"
    assert all(u.jobs_unlocked > 0 for u in resp.skill_unlocks)          # 0-job unlocks are skipped
    # a broad id is never listed ahead of a concrete missing skill (it may be the only one missing)
    assert all(j.missing_skills[0] not in BROAD_SKILL_IDS for j in resp.jobs
               if any(s not in BROAD_SKILL_IDS for s in j.missing_skills))


# --- typed skills through module 2 ---------------------------------------------------------------

@pytest.fixture
def wired(monkeypatch, store, fake_http):
    real = job_fetcher.fetch_jobs

    def fetch(role, location, **kw):
        kw.update(store=store, client=fake_http.client(), m2_client=fake_http.client(), now=NOW)
        return real(role, location, **kw)
    monkeypatch.setattr(search_module, "fetch_jobs", fetch)
    real_resolve = search_module.resolve_typed_skills
    monkeypatch.setattr(search_module, "resolve_typed_skills",
                        lambda typed, client=None: real_resolve(typed, client=fake_http.client()))
    monkeypatch.setattr(adzuna, "histogram", lambda role, city, client=None: HIST)
    real_salary = jsearch.estimated_salary
    monkeypatch.setattr(jsearch, "estimated_salary",
                        lambda role, city, bucket, client=None: real_salary(role, city, bucket, client=fake_http.client()))
    return fake_http


def test_typed_postgres_matches_postgresql_via_module_2(wired):
    payload = json.loads(json.dumps(ADZ))
    payload["results"][0]["description"] = "We use PostgreSQL daily."
    wired.on("api.adzuna.com", json_response(payload))

    def m2(request):  # the job says PostgreSQL; the user typed "Postgres"
        text = json.loads(request.content)["text"].lower()
        hits = [{"id": "postgresql", "display": "PostgreSQL", "maps_to": "sql", "matches": [w]}
                for w in ("postgresql", "postgres") if w in text][:1]
        return httpx.Response(200, json={"skills": hits, "warnings": []})
    wired.on("127.0.0.1", m2)
    body = TestClient(app).post("/api/v1/jobs/search", json={
        "target_role": "Data Scientist", "location": "Bengaluru", "skills": ["Postgres", "Quantum Juggling"],
        "experience_years": 2}).json()
    job = next(j for j in body["jobs"] if j["job_id"] == "adzuna:5905882064")
    assert job["matched_skills"] == ["postgresql"]
    assert any("Quantum Juggling" in w for w in body["warnings"])


def test_typed_skills_fall_back_to_keywords_when_module_2_is_down(wired):
    wired.on("api.adzuna.com", json_response(ADZ))           # no module 2 route
    body = TestClient(app).post("/api/v1/jobs/search", json={
        "target_role": "Data Scientist", "location": "Bengaluru", "skills": ["Postgres"]}).json()
    assert any("typed skills are matched as plain keywords" in w for w in body["warnings"])


# --- salary sources ----------------------------------------------------------------------------------

PERCENTILES = MarketPercentiles(p25=14 * L, p50=17 * L, p75=21 * L, sample_size=1200,
                                experience_band=ExperienceBand(min=3, max=7))


def test_module_1_percentiles_are_primary_and_tighter_than_histogram():
    few_posted = [make_job(salary_min=10 * L, salary_max=14 * L)]
    hist_only = estimate_market(few_posted, histogram_fn=lambda: HIST)
    with_pct = estimate_market(few_posted, histogram_fn=lambda: HIST, percentiles=PERCENTILES)
    assert with_pct.sources_used == ["module_1_percentiles"] and with_pct.sample_size == 1200
    assert (with_pct.estimated_min, with_pct.estimated_median, with_pct.estimated_max) == (14 * L, 17 * L, 21 * L)
    assert with_pct.confidence > hist_only.confidence
    width = lambda e: e.estimated_max - e.estimated_min  # noqa: E731
    assert width(with_pct) < width(hist_only)
    band = ExperienceBand(min=3, max=7)
    cand_pct = candidate_value(with_pct, match_score=0.8, years=5, band=band, city="Bengaluru")
    cand_hist = candidate_value(hist_only, match_score=0.8, years=5, band=band, city="Bengaluru")
    assert cand_pct.estimated_max - cand_pct.estimated_min < cand_hist.estimated_max - cand_hist.estimated_min


def test_small_percentile_sample_falls_through():
    thin = MarketPercentiles(p25=14 * L, p50=17 * L, p75=21 * L, sample_size=10)
    est = estimate_market([], histogram_fn=lambda: HIST, percentiles=thin)
    assert est.sources_used == ["adzuna_histogram"]


def test_source_order_posted_then_percentiles_then_jsearch_then_histogram():
    posted = [make_job(job_id=f"a:{i}", salary_min=(10 + i) * L, salary_max=(13 + i) * L) for i in range(6)]
    estimate = {"min": 12 * L, "median": 17.7 * L, "max": 24 * L, "sample_size": 2070, "publisher": "Glassdoor",
                "confidence": "VERY_HIGH"}
    assert estimate_market(posted, percentiles=PERCENTILES, jsearch_estimate=estimate).sources_used == ["posted_salaries"]
    assert estimate_market([], percentiles=PERCENTILES, jsearch_estimate=estimate).sources_used == ["module_1_percentiles"]
    js_est = estimate_market([], jsearch_estimate=estimate, histogram_fn=lambda: HIST)
    assert js_est.sources_used == ["jsearch_salary_estimate"] and js_est.confidence == 0.7 and "Glassdoor" in js_est.note
    assert estimate_market([], histogram_fn=lambda: HIST).sources_used == ["adzuna_histogram"]


def test_candidate_range_positions_by_experience_and_narrows_with_confidence():
    market = estimate_market([], percentiles=PERCENTILES)          # 14-21 L, confidence ~0.70
    band = ExperienceBand(min=3, max=7)
    junior = candidate_value(market, match_score=0.7, years=3, band=band, city="Bengaluru")
    senior = candidate_value(market, match_score=0.7, years=7, band=band, city="Bengaluru")
    assert (junior.market_position, senior.market_position) == (0.0, 1.0)
    assert junior.estimated_max < senior.estimated_max
    # worked example (WORKING.md §8.2): 5 years in 3-7 -> position 0.5 -> centre 17.5 L at match 0.7;
    # half-width = 7 L x (0.15 + 0.35 x (1 - conf))
    mid = candidate_value(market, match_score=0.7, years=5, band=band, city="Bengaluru")
    half = 7 * L * (0.15 + 0.35 * (1 - market.confidence))
    assert mid.market_position == 0.5 and mid.adjustment == 1.0
    assert (mid.estimated_min, mid.estimated_max) == (round((17.5 * L - half) / 10_000) * 10_000,
                                                      round((17.5 * L + half) / 10_000) * 10_000)


# --- JSearch salary estimate: provider, cache, opt-in -------------------------------------------------

def test_jsearch_salary_normalises_recorded_response(fake_http):
    fake_http.on("jsearch.p.rapidapi.com", json_response(JS_SALARY))
    est = jsearch.estimated_salary("Data Scientist", "Bengaluru", "FOUR_TO_SIX", client=fake_http.client())
    assert est == {"min": 1200000, "median": 1770000, "max": 2407000, "sample_size": 2070, "publisher": "Glassdoor",
                   "confidence": "VERY_HIGH", "updated_at": "2026-08-15T07:32:17.000Z", "bucket": "FOUR_TO_SIX"}
    params = dict(fake_http.requests[0].url.params)
    assert params == {"job_title": "Data Scientist", "location": "Bangalore, India", "location_type": "CITY",
                      "years_of_experience": "FOUR_TO_SIX"}
    usd = json.loads(json.dumps(JS_SALARY))
    usd["data"][0]["salary_currency"] = "USD"
    fake_http.on("jsearch.p.rapidapi.com", json_response(usd))
    assert jsearch.estimated_salary("Data Scientist", "Bengaluru", "ALL", client=fake_http.client()) is None
    fake_http.on("jsearch.p.rapidapi.com", json_response({"message": "You are not subscribed to this API."}, 403))
    with pytest.raises(ProviderError):
        jsearch.estimated_salary("Data Scientist", "Bengaluru", "ALL", client=fake_http.client())


@pytest.mark.parametrize("years, bucket", [(None, "ALL"), (0.5, "LESS_THAN_ONE"), (2, "ONE_TO_THREE"),
                                           (4.5, "FOUR_TO_SIX"), (7.5, "SEVEN_TO_NINE"), (12, "TEN_TO_FOURTEEN"),
                                           (20, "ABOVE_FIFTEEN")])
def test_experience_buckets(years, bucket):
    assert jsearch.experience_bucket(years) == bucket


def test_jsearch_salary_is_opt_in_and_cached_for_7_days(wired):
    wired.on("api.adzuna.com", json_response(ADZ)).on("127.0.0.1", m2_extract_handler)
    wired.on("jsearch.p.rapidapi.com", json_response(JS_SALARY))
    client = TestClient(app)
    base = {"target_role": "Data Scientist", "location": "Bengaluru", "skills": ["python"], "experience_years": 4.5}
    assert client.post("/api/v1/jobs/search", json=base).json()["market_salary"]["sources_used"] == ["adzuna_histogram"]
    assert wired.calls("jsearch.p.rapidapi.com") == 0                    # not opted in, nothing cached
    first = client.post("/api/v1/jobs/search", json={**base, "jsearch_salary": True}).json()
    assert first["market_salary"]["sources_used"] == ["jsearch_salary_estimate"]
    second = client.post("/api/v1/jobs/search", json=base).json()        # cached: used even without opting in
    assert second["market_salary"]["sources_used"] == ["jsearch_salary_estimate"]
    assert wired.calls("jsearch.p.rapidapi.com") == 1


def test_salary_cache_expires_after_7_days(store):
    store.save_salary("data scientist|Bengaluru|ALL", {"min": 1, "median": 2, "max": 3}, NOW - timedelta(days=8))
    req = JobSearchRequest(target_role="Data Scientist", location="Bengaluru")
    assert search_module._jsearch_salary(req, "Bengaluru", 4.5, store, None, NOW, []) is None
    store.save_salary("data scientist|Bengaluru|ALL", {"min": 1, "median": 2, "max": 3}, NOW - timedelta(days=6))
    assert search_module._jsearch_salary(req, "Bengaluru", 4.5, store, None, NOW, []) == {"min": 1, "median": 2, "max": 3}


def test_percentiles_accepted_by_search_and_estimate(wired):
    wired.on("api.adzuna.com", json_response(ADZ)).on("127.0.0.1", m2_extract_handler)
    body = TestClient(app).post("/api/v1/salary/estimate", json={
        "target_role": "Data Scientist", "location": "Bengaluru", "skills": ["python"], "experience_years": 5,
        "market_salary_percentiles": PERCENTILES.model_dump()}).json()
    assert body["market_salary"]["sources_used"] == ["module_1_percentiles"]
    assert body["candidate_value"]["market_position"] == 0.5             # 5 years in the percentiles' 3-7 band
    bad = TestClient(app).post("/api/v1/jobs/search", json={
        "target_role": "X", "location": "Pune", "market_salary_percentiles": {"p25": 5, "p50": 3, "p75": 9, "sample_size": 50}})
    assert bad.status_code == 422


def test_store_salary_round_trip(tmp_path):
    s = JobStore(tmp_path / "x.sqlite")
    s.save_salary("k", {"median": 5}, NOW)
    assert s.load_salary("k") == ({"median": 5}, NOW) and s.stats()["salary_estimates"] == 1
