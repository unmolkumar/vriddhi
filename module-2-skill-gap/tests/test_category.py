"""is_category: broad field-level ids are shown and explained, never taught as one skill."""
import pytest
from fastapi.testclient import TestClient

import src.engines.skill_extractor as se
from src.api.main import app
from src.engines.gap_analyzer import analyze_gap
from src.engines.skill_extractor import category_children, extract_skills, taxonomy
from src.models.schemas import GapAnalysisRequest, ManualProfileInput

CATEGORIES = {"ai", "generative_ai", "data_science", "data_engineering", "big_data", "backend", "frontend_development",
              "full_stack_development", "web_development", "mobile_development", "api_development", "devops",
              "automation", "cloud", "software_testing", "cybersecurity"}


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    monkeypatch.setattr(se, "_call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("real LLM called")))
    monkeypatch.setenv("GROQ_API_KEY", "")


def gap(skills, required, role="X"):
    return analyze_gap(GapAnalysisRequest(target_role=role, required_skills=required,
                                          manual_profile=ManualProfileInput(skills=skills, experience_years=2)))


def test_every_skill_has_the_flag_and_the_set_is_deliberate():
    skills = taxonomy()["skills"]
    assert all(isinstance(s["is_category"], bool) for s in skills)
    assert {s["id"] for s in skills if s["is_category"]} == CATEGORIES
    assert "data" in taxonomy()["non_skill_ids"]               # 'data' stays a non-skill, as gap analysis treats it
    for concrete in ("machine_learning", "etl", "ci_cd", "statistics", "sql", "python"):
        assert not next(s for s in skills if s["id"] == concrete)["is_category"]


def test_category_children_nearest_and_most_central_first():
    assert category_children("cloud")[:3] == ["aws", "azure", "gcp"]
    assert category_children("ai")[0] == "machine_learning"
    assert category_children("devops") == ["ci_cd", "monitoring", "infrastructure_as_code", "containerization"]
    assert category_children("backend") == []                  # the taxonomy has nothing under it
    assert not set(category_children("ai")) & CATEGORIES       # never another category


def test_extractor_and_endpoints_expose_the_flag():
    hits = {h.id: h for h in extract_skills("Cloud and AWS, plus DevOps", use_llm=False)}
    assert hits["aws"].is_category is False and hits["devops"].is_category is True
    client = TestClient(app)
    body = client.post("/api/v1/skills/extract", json={"text": "Experience with AWS and Cloud Computing"}).json()
    assert {s["id"]: s["is_category"] for s in body["skills"]} == {"aws": False, "cloud": True}
    resume = client.post("/api/v1/skills/analyze_resume", files={"file": ("cv.txt", b"SKILLS\nPython, DevOps, Docker")},
                         data={"use_llm": "false"}).json()
    flags = {s["name"]: s["is_category"] for s in resume["profile"]["skills"]}
    assert flags == {"python": False, "devops": True, "docker": False}


def test_required_category_is_explained_not_scheduled():
    r = gap(["linux", "docker"], ["devops", "linux", "kubernetes", "cloud", "docker"], role="DevOps Engineer")
    rows = {g.skill: g for g in r.gap_matrix}
    cloud = rows["cloud"]
    assert cloud.is_category and cloud.status == "missing"
    assert cloud.category_children[:3] == ["aws", "azure", "gcp"]
    assert cloud.advice == ("Cloud Computing is a broad field, not a single skill. Concrete skills that build it: "
                            "AWS, Microsoft Azure, Google Cloud Platform, Cloudflare.")
    milestones = [m.skill for m in r.roadmap.milestones]
    assert not set(milestones) & CATEGORIES and not set(r.learning_priorities) & {"Cloud Computing", "DevOps"}
    assert r.skills.critical_missing[-1] == "cloud"             # concrete gaps lead, categories trail
    assert "Cloud Computing" not in r.verdict_message


def test_category_without_known_children_says_so():
    g = gap([], ["automation"]).gap_matrix[0]
    assert g.is_category and g.category_children == []
    assert g.advice == "Automation is a broad field, not a single skill, so it isn't added to the roadmap."


def test_met_category_gets_no_note_but_a_weak_one_does():
    from src.models.schemas import ExtractedSkill, SourceInfo, UserProfile
    profile = UserProfile(skills=[ExtractedSkill(name="aws", display="AWS", maps_to="cloud", level=3, confidence=0.9,
                                                 evidence=["work_supported"])], source=SourceInfo(format="manual"))
    met = analyze_gap(GapAnalysisRequest(target_role="X", required_skills=["cloud"], profile=profile)).gap_matrix[0]
    assert met.status == "matched" and met.is_category and met.gap <= 0 and met.advice is None
    weak = next(x for x in gap(["AWS"], ["cloud"]).gap_matrix if x.skill == "cloud")   # typed: level 2 < 3
    assert weak.gap > 0 and weak.advice.startswith("Cloud Computing is a broad field")


def test_category_prerequisites_are_not_pulled_in():
    # terraform's prerequisite is 'cloud': the roadmap must not schedule the category itself
    r = gap([], ["terraform"])
    assert [m.skill for m in r.roadmap.milestones] == ["terraform"]
