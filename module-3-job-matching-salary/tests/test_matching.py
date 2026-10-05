"""Match components, weights, classification, inferred skills, experience sources, market profile."""
import pytest

from conftest import make_job
from src.engines.market_profile import MIN_JOB_SKILLS, build_profile, infer_skills
from src.engines.matching import (
    INFERRED_PENALTY, INFERRED_SKILL_WEIGHT, RELATED_SKILL_CREDIT, classify, skills_confidence, education_component, experience_band, experience_component,
    location_component, match_job, preference_component, seniority_component, skill_component,
)
from src.models.schemas import CandidateProfile, CandidateSkill, ExperienceBand, JobSearchRequest, MatchWeights


def cand(skills, years=2.0, **kw):
    return CandidateProfile(skills=[CandidateSkill(**s) if isinstance(s, dict) else CandidateSkill(name=s, level=3)
                                    for s in skills], experience_years=years, **kw)


# --- worked example (WORKING.md §6.3; spec's Backend Developer example) ------------------------

def test_worked_example_backend_developer():
    candidate = cand(["python", "fastapi", "sql", {"name": "docker", "level": 2}, "redis"], years=2,
                     location="Bengaluru", education=["B.Tech Computer Science"])
    job = make_job(title="Backend Developer", skills=["python", "fastapi", "sql", "docker"],
                   experience_min=3, experience_max=6, description="Python, FastAPI, SQL, Docker. 3-6 years.")
    m = match_job(job, candidate)
    b = m.breakdown
    assert (b.skills, b.experience, b.education, b.location, b.seniority, b.preference) == (0.95, 0.6667, 1.0, 1.0, 1.0, 1.0)
    assert m.overall == 0.9133 and classify(m.overall) == "Strong"      # 0.4*0.95 + 0.2*0.6667 + 0.1*4
    assert m.matched == ["python", "fastapi", "sql", "docker"] and m.missing == [] and m.experience_source == "posting"


# --- skills ------------------------------------------------------------------------------------

def test_skill_credit_by_level_evidence_and_parents():
    job = make_job(skills=["python", "sql", "aws", "postgresql"], skill_parents={"postgresql": "sql"})
    c = cand([{"name": "python", "level": 1}, {"name": "mysql", "level": 3, "maps_to": "sql"},
              {"name": "aws", "level": 4, "needs_verification": True}])
    score, matched, missing, method = skill_component(job, c)
    # python 0.6 (level 1); sql 1.0 via mysql's maps_to; aws 1.0 x 0.5 (needs verification); postgresql 0
    assert method == "skills" and score == pytest.approx((0.6 + 1.0 + 0.5 + 0) / 4)
    assert matched == ["python", "sql", "aws"] and missing == ["postgresql"]
    related = skill_component(make_job(skills=["postgresql"], skill_parents={"postgresql": "sql"}), cand(["sql"]))
    assert related[0] == RELATED_SKILL_CREDIT                         # knows SQL, job asks PostgreSQL


def test_inferred_skills_count_less_and_come_last():
    job = make_job(skills=["python", "spark"], inferred_skills=["spark"], skills_inferred=True)
    score, _, missing, _ = skill_component(job, cand(["python"]))
    # weighted credit 1 / (1 + 0.5), then x (1 - 0.3 x 1/2 inferred)
    assert score == pytest.approx(1 / (1 + INFERRED_SKILL_WEIGHT) * (1 - INFERRED_PENALTY * 0.5))
    job = make_job(skills=["spark", "python", "sql"], inferred_skills=["spark"])
    assert skill_component(job, cand([]))[2] == ["python", "sql", "spark"]


def test_keyword_fallback_when_job_has_no_skills():
    job = make_job(skills=[], skills_source="unavailable", title="Data Scientist",
                   description="Strong Python and machine learning; SQL a plus.")
    score, matched, missing, method = skill_component(job, cand(["python", "machine_learning", "sql", "tableau"]))
    assert method == "keywords" and matched == ["python", "machine_learning", "sql"] and score == 0.75


# --- experience and seniority -------------------------------------------------------------------

def test_experience_sources():
    band = ExperienceBand(min=2, max=6)
    assert experience_band(make_job(experience_min=3, experience_max=5), band)[1] == "posting"
    open_ended = experience_band(make_job(experience_min=4), band)[0]
    assert (open_ended.min, open_ended.max) == (4, 7)                    # "4+" read as 4-7
    assert experience_band(make_job(), band) == (band, "request")
    lead, source = experience_band(make_job(title="Lead Data Engineer"), None)
    assert source == "title_heuristic" and (lead.min, lead.max) == (6, 12)
    assert experience_band(make_job(title="Data Engineer"), None) == (None, "unknown")


@pytest.mark.parametrize("years, expected", [(2, 2 / 3), (3, 1.0), (6, 1.0), (11, 0.5), (8, 0.8), (0, 0.0)])
def test_experience_component(years, expected):
    assert experience_component(years, ExperienceBand(min=3, max=6)) == pytest.approx(expected)
    assert experience_component(years, None) == 0.7


def test_seniority():
    assert seniority_component(make_job(title="Senior Data Scientist"), 5, None) == 1.0
    assert seniority_component(make_job(title="Senior Data Scientist"), 1, None) == pytest.approx(0.4)   # 2 levels apart
    assert seniority_component(make_job(title="Data Scientist"), 3, ExperienceBand(min=3, max=6)) == 1.0
    assert seniority_component(make_job(title="Data Scientist"), 3, None) == 0.7


# --- education, location, preferences -----------------------------------------------------------

def test_education():
    assert education_component([], make_job(description="Great team")) == 1.0                         # no requirement
    assert education_component(["B.Tech CS"], make_job(description="Master's degree required")) == 0.6
    assert education_component(["M.Tech CS"], make_job(description="Master's degree required")) == 1.0
    assert education_component([], make_job(description="Bachelor's degree in CS")) == 0.4


def test_location():
    cities = {"Bengaluru"}
    assert location_component(make_job(location="Bengaluru"), cities) == 1.0
    assert location_component(make_job(location="Pune"), cities) == 0.3
    assert location_component(make_job(location="Pune", work_mode="remote"), cities) == 1.0
    assert location_component(make_job(location=None), cities) == 0.7
    assert location_component(make_job(location="Noida"), {"Gurugram"}) == 0.8          # same region (Delhi NCR)


def test_preferences():
    job = make_job(work_mode="hybrid", employment_type="full_time", salary_min=1500000, salary_max=2000000)
    assert preference_component(job, [], [], None) == 1.0
    assert preference_component(job, ["hybrid", "remote"], ["full_time"], 18) == 1.0
    assert preference_component(job, ["remote"], [], None) == 0.0
    assert preference_component(job, [], [], 25) == 0.3
    assert preference_component(make_job(salary_min=3000000, salary_is_predicted=True), [], [], 10) == 0.7


# --- weights and classification ------------------------------------------------------------------

def test_weights_are_configurable_and_normalised():
    w = MatchWeights(skills=1, experience=1, education=0, location=0, seniority=0, preference=0).normalised()
    job = make_job(skills=["python", "sql"], experience_min=4)
    m = match_job(job, cand(["python"], years=2), weights=w)
    assert m.overall == pytest.approx(0.5 * 0.5 + 0.5 * 0.5)
    with pytest.raises(ValueError):
        MatchWeights(skills=0, experience=0, education=0, location=0, seniority=0, preference=0)


@pytest.mark.parametrize("overall, label", [(0.85, "Strong"), (0.80, "Strong"), (0.7, "Good"), (0.65, "Good"),
                                            (0.5, "Partial"), (0.45, "Partial"), (0.3, "Weak")])
def test_classification(overall, label):
    assert classify(overall) == label


def test_manual_skills_become_level_2_ids():
    req = JobSearchRequest(target_role="X", skills=["Power BI", "SQL"], location="Pune")
    assert [(s.name, s.level) for s in req.candidate().skills] == [("power_bi", 2), ("sql", 2)]


# --- role market profile and inference -----------------------------------------------------------

def test_market_profile_and_inference():
    jobs = [make_job(job_id=f"adzuna:{i}", skills=s) for i, s in enumerate([
        ["python", "sql", "machine_learning", "aws"], ["python", "sql", "spark"], ["python", "machine_learning", "sql"],
        ["python"], [], ])]
    jobs.append(make_job(job_id="adzuna:x", skills=[], skills_source="unavailable"))
    profile = build_profile("Data Scientist", ["Bengaluru"], jobs)
    assert profile.jobs_analysed == 4                                   # only jobs with extracted skills
    assert [(p.skill, p.share) for p in profile.top_skills[:3]] == [("python", 1.0), ("sql", 0.75), ("machine_learning", 0.5)]
    filled = {j.job_id: j for j in infer_skills(jobs, profile)}
    sparse = filled["adzuna:3"]                                         # had 1 skill (< MIN_JOB_SKILLS)
    assert sparse.skills_inferred and sparse.skills[0] == "python" and "sql" in sparse.inferred_skills
    assert len(sparse.skills) == 5 and "python" not in sparse.inferred_skills
    assert not filled["adzuna:0"].skills_inferred                      # 4 own skills: left alone
    assert filled["adzuna:x"].skills_inferred                          # module 2 down: filled from the profile
    assert MIN_JOB_SKILLS == 3
