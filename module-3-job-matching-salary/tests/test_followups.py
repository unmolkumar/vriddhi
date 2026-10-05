"""Module 2's is_category as the source of truth, and module 1's real salary-percentiles object."""
import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import json_response, m2_extract_handler, make_job
from test_polish import ADZ, wired  # noqa: F401  (fixture)
from src.api.main import app
from src.engines import m2_client
from src.engines.market_profile import is_broad
from src.engines.matching import BROAD_SKILL_WEIGHT, skill_component
from src.engines.salary import (
    MIN_CITY_SAMPLES, SALARY_SOURCE_TOLERANCE, estimate_market, experience_tier, m1_salary,
)
from src.models.schemas import CandidateProfile, CandidateSkill, MarketPercentiles

L = 100_000


# --- is_category: module 2's flag first, the hand list as fallback ----------------------------------

def test_module_2_flag_is_stored_and_wins_over_the_fallback_list(fake_http):
    fake_http.on("127.0.0.1", m2_extract_handler)
    job = make_job(description="Cloud work with AWS and machine learning, some ai .", skills_source="none")
    [enriched], ok = m2_client.enrich_skills([job], client=fake_http.client())
    assert ok and enriched.skill_is_category == {"machine_learning": False, "aws": False, "ai": True}
    # module 2 says a fallback-listed id is concrete (or an unlisted one is a category): its flag wins
    assert is_broad("ai", {"ai": False}) is False and is_broad("statistics", {"statistics": True}) is True


def test_fallback_list_when_module_2_sends_no_flag(fake_http):
    def old_m2(request):        # an older module 2 (or a cached job): no is_category in the response
        resp = m2_extract_handler(request)
        skills = [{k: v for k, v in s.items() if k != "is_category"} for s in resp.json()["skills"]]
        return httpx.Response(200, json={"skills": skills, "warnings": []})
    fake_http.on("127.0.0.1", old_m2)
    [job], _ = m2_client.enrich_skills([make_job(description="ai and python", skills_source="none")], client=fake_http.client())
    assert job.skill_is_category == {}
    assert is_broad("ai", job.skill_is_category) and not is_broad("python", job.skill_is_category)


def test_matching_weighs_by_the_flag_on_the_job():
    cand = CandidateProfile(skills=[CandidateSkill(name="python", level=3)], experience_years=2)
    flagged = make_job(skills=["python", "statistics"], skills_source="m2", skill_is_category={"statistics": True})
    plain = make_job(skills=["python", "statistics"], skills_source="m2")
    assert skill_component(flagged, cand)[0] == pytest.approx(1 / (1 + BROAD_SKILL_WEIGHT))
    assert skill_component(plain, cand)[0] == pytest.approx(0.5)


# --- module 1's market_salary_percentiles ---------------------------------------------------------

# Module 1's documented Data Scientist example (module-1-career-intelligence/WORKING.md §7.7, sample payload).
DS = MarketPercentiles.model_validate({
    "overall_inr_lpa": {"p25": 10.6, "p50": 17.6, "p75": 28.5, "currency": "INR LPA", "sample_size": 333},
    "overall_usd": {"p25": 57083.0, "p50": 90976.0, "p75": 140117.0, "currency": "USD", "sample_size": 2808},
    "by_experience_inr_lpa": {
        "entry": {"p25": 5.0, "p50": 8.0, "p75": 12.0, "currency": "INR LPA", "sample_size": 37},
        "mid": {"p25": 15.0, "p50": 22.1, "p75": 30.0, "currency": "INR LPA", "sample_size": 188},
        "senior": {"p25": 22.5, "p50": 36.0, "p75": 55.9, "currency": "INR LPA", "sample_size": 108}},
    "by_city_inr_lpa": {
        "Bengaluru": {"p25": 12.5, "p50": 15.0, "p75": 22.6, "currency": "INR LPA", "sample_size": 39},
        "Hyderabad": {"p25": 11.2, "p50": 16.8, "p75": 24.0, "currency": "INR LPA", "sample_size": 28},
        "Delhi NCR": {"p25": 10.5, "p50": 15.0, "p75": 22.0, "currency": "INR LPA", "sample_size": 22}},
})


@pytest.mark.parametrize("years, tier", [(0, "entry"), (2.9, "entry"), (3, "mid"), (5, "mid"), (5.1, "senior"),
                                         (7.5, "senior")])
def test_module_1_experience_tiers(years, tier):
    assert experience_tier(years) == tier


def test_documented_data_scientist_example_small_city_band_is_ignored():
    m1 = m1_salary(DS, 4, "Bengaluru")          # mid tier; Bengaluru has 39 points < MIN_CITY_SAMPLES
    assert 39 < MIN_CITY_SAMPLES
    assert (m1.p25, m1.p50, m1.p75, m1.sample_size, m1.method) == (15 * L, 22.1 * L, 30 * L, 188, "experience_bucket")
    assert "Bengaluru band not used (39 points" in m1.detail
    est = estimate_market([], percentiles=m1)
    assert (est.estimated_min, est.estimated_median, est.estimated_max) == (15 * L, 2_210_000, 30 * L)
    assert est.method == "experience_bucket" and est.sources_used == ["module_1_percentiles"]
    assert est.confidence == 0.66                          # 0.55 + 0.05 x log10(188)
    senior = m1_salary(DS, 7.5, "Bengaluru")
    assert senior.p50 == 36 * L and (senior.band.min, senior.band.max) == (5, 12)


def test_city_ratio_applies_with_enough_city_samples_and_delhi_ncr_maps():
    big = DS.model_copy(deep=True)
    big.by_city_inr_lpa["Delhi NCR"].sample_size = 60
    m1 = m1_salary(big, 4, "Delhi NCR")
    ratio = 15.0 / 17.6
    assert m1.method == "experience_x_city_ratio"
    assert m1.p50 == pytest.approx(22.1 * L * ratio) and m1.p25 == pytest.approx(15 * L * ratio)
    assert m1_salary(big, 4, "Gurugram").method == "experience_x_city_ratio"     # one of the NCR cities
    assert m1_salary(DS, 4, "Delhi NCR").method == "experience_bucket"           # 22 points: too few


def test_small_tier_sample_lowers_confidence_and_missing_tier_falls_back_to_overall():
    entry = estimate_market([], percentiles=m1_salary(DS, 1, "Pune"))           # 37 points
    mid = estimate_market([], percentiles=m1_salary(DS, 4, "Pune"))             # 188 points
    assert entry.confidence < mid.confidence and entry.confidence == 0.53       # 0.55 + 0.05 x log10(37) - 0.1
    no_tiers = MarketPercentiles(overall_inr_lpa=DS.overall_inr_lpa)
    m1 = m1_salary(no_tiers, 4, "Bengaluru")
    assert m1.method == "overall" and m1.p50 == 17.6 * L and m1.band is None


JS = {"min": 12 * L, "median": 20 * L, "max": 30 * L, "sample_size": 900, "publisher": "Glassdoor",
      "confidence": "HIGH", "bucket": "FOUR_TO_SIX"}


def test_agreeing_sources_keep_module_1_primary():
    est = estimate_market([], percentiles=m1_salary(DS, 4, "Bengaluru"), jsearch_estimate=JS)   # 20 vs 22.1: -9.5%
    assert est.sources_used == ["module_1_percentiles"] and est.source_check.agree
    assert est.source_check.gap_pct == -9.5 and est.estimated_median == 2_210_000 and est.confidence == 0.66
    assert "JSearch's estimate agrees" in est.note


def test_disagreeing_sources_are_both_reported_with_lower_confidence():
    far = {**JS, "median": 12 * L}                                              # 12 vs 22.1: -45.7%
    assert abs(12 - 22.1) / 22.1 > SALARY_SOURCE_TOLERANCE
    est = estimate_market([], percentiles=m1_salary(DS, 4, "Bengaluru"), jsearch_estimate=far)
    assert est.sources_used == ["module_1_percentiles", "jsearch_salary_estimate"]
    assert not est.source_check.agree and est.source_check.gap_pct == -45.7
    assert (est.estimated_min, est.estimated_median, est.estimated_max) == (12 * L, 1_710_000, 30 * L)
    assert est.confidence == 0.51 and "differs by 45.7%" in est.note


def test_search_accepts_module_1_object_and_reports_method(wired):
    wired.on("api.adzuna.com", json_response(ADZ)).on("127.0.0.1", m2_extract_handler)
    body = TestClient(app).post("/api/v1/salary/estimate", json={
        "target_role": "Data Scientist", "location": "Bengaluru", "skills": ["python"], "experience_years": 7.5,
        "market_salary_percentiles": DS.model_dump()}).json()
    market = body["market_salary"]
    assert market["sources_used"] == ["module_1_percentiles"] and market["method"] == "experience_bucket"
    assert market["estimated_median"] == 36 * L                                  # senior tier, Bengaluru too small
    assert body["candidate_value"]["market_position"] == pytest.approx(2.5 / 7, abs=1e-3)   # 7.5 in 5-12


# --- demo-scoped pre-warm ----------------------------------------------------------------------------

def _prewarm():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).parent.parent / "scripts" / "prewarm.py"
    spec = importlib.util.spec_from_file_location("prewarm", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prewarm_quota_plan():
    pw = _prewarm()
    demo = [(r, c) for r in ("Data Scientist", "Data Analyst", "Backend Developer") for c in ("Bengaluru", "Pune")]
    assert pw.jsearch_plan(demo, with_jsearch=True, with_salary=False) == {"search": 6, "salary": 0}
    every = [(r, c) for r in pw.ROLES for c in pw.CITIES]
    assert pw.jsearch_plan(every, with_jsearch=False, with_salary=True) == {"search": 0, "salary": 35}
    assert pw.jsearch_plan([("X", "Delhi-NCR")], with_jsearch=True, with_salary=True) == {"search": 3, "salary": 1}
    assert pw._list(" Pune, ,Bengaluru ", []) == ["Pune", "Bengaluru"] and pw._list(None, ["a"]) == ["a"]
