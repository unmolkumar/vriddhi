"""Roadmap: prerequisite order, pulled-in prerequisites, estimate ranges, weekly milestones."""
import pytest

import src.engines.skill_extractor as se
from src.engines.gap_analyzer import analyze_gap
from src.engines.roadmap_generator import KIND_HOURS_FACTOR, MAX_MILESTONES, TIER_HOURS, build_roadmap
from src.engines.skill_extractor import taxonomy
from src.models.schemas import GapAnalysisRequest, ManualProfileInput, SkillGap, SourceInfo, UserProfile


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    monkeypatch.setattr(se, "_call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("real LLM called")))


def gap_for(skills, required, hours_per_week=None):
    return analyze_gap(GapAnalysisRequest(target_role="ML Engineer", required_skills=required,
                                          manual_profile=ManualProfileInput(skills=skills, experience_years=1),
                                          hours_per_week=hours_per_week))


def assert_prerequisites_first(roadmap):
    position = {m.skill: m.order for m in roadmap.milestones}
    for m in roadmap.milestones:
        for p in m.prerequisites:
            assert position[p] < m.order, f"{p} must come before {m.skill}"


def test_prerequisites_pulled_in_and_ordered_first():
    r = gap_for(["Excel"], ["kubernetes", "deep_learning", "machine_learning"])
    order = [m.skill for m in r.roadmap.milestones]
    assert_prerequisites_first(r.roadmap)
    assert order.index("docker") < order.index("kubernetes") and order.index("linux") < order.index("kubernetes")
    assert order.index("machine_learning") < order.index("deep_learning")
    assert order.index("python") < order.index("machine_learning")
    pulled = {m.skill: m for m in r.roadmap.milestones if m.kind == "prerequisite"}
    assert {"docker", "linux", "python", "statistics", "linear_algebra", "probability"} <= set(pulled)
    assert "kubernetes" in pulled["docker"].required_for
    assert pulled["docker"].reason.startswith("Prerequisite for Kubernetes")


def test_known_prerequisites_are_not_pulled_in():
    r = gap_for(["Python", "Statistics", "Linear Algebra"], ["machine_learning"])
    assert [m.skill for m in r.roadmap.milestones] == ["machine_learning"]


def test_implied_prerequisites_count_as_known():
    # pandas maps_to python, so python isn't re-taught before Airflow.
    r = gap_for(["pandas", "SQL"], ["airflow"])
    assert "python" not in [m.skill for m in r.roadmap.milestones]


def test_hours_are_estimate_ranges_by_tier_and_kind():
    r = gap_for(["Docker"], ["kubernetes", "git"])
    by = {m.skill: m for m in r.roadmap.milestones}
    lo, hi = TIER_HOURS[3]
    factor = KIND_HOURS_FACTOR["adjacent"]
    assert by["kubernetes"].kind == "adjacent"
    assert (by["kubernetes"].estimated_hours.low, by["kubernetes"].estimated_hours.high) == (round(lo * factor), round(hi * factor))
    assert (by["git"].estimated_hours.low, by["git"].estimated_hours.high) == TIER_HOURS[1]
    assert "estimated" in r.roadmap.note and "not guarantees" in r.roadmap.note


def test_adjacent_skill_estimate_is_lower_than_missing():
    adjacent = next(m for m in gap_for(["Docker"], ["kubernetes"]).roadmap.milestones if m.skill == "kubernetes")
    missing = next(m for m in gap_for(["Excel"], ["kubernetes"]).roadmap.milestones if m.skill == "kubernetes")
    assert (adjacent.kind, missing.kind) == ("adjacent", "missing")
    assert (adjacent.hours_factor, missing.hours_factor) == (0.5, 1.0)
    assert adjacent.estimated_hours.high < missing.estimated_hours.high
    assert adjacent.estimated_hours.low < missing.estimated_hours.low
    assert (missing.estimated_hours.low, missing.estimated_hours.high) == TIER_HOURS[3]


def test_weekly_milestones_are_cumulative():
    r = gap_for(["Excel"], ["python", "sql"], hours_per_week=10)
    m1, m2 = r.roadmap.milestones
    assert (m1.estimated_weeks.low, m1.estimated_weeks.high) == (3, 6)       # python: 30-60 h at 10 h/week
    assert (m2.estimated_weeks.low, m2.estimated_weeks.high) == (6, 12)      # + sql: 30-60 h
    assert (r.roadmap.estimated_total_weeks.low, r.roadmap.estimated_total_weeks.high) == (6, 12)
    assert r.roadmap.total_estimated_hours.low == 60
    assert gap_for(["Excel"], ["python"]).roadmap.estimated_total_weeks is None


def test_whole_taxonomy_orders_without_cycles():
    """Every taxonomy skill as 'missing' still sorts topologically (the cap only trims the tail)."""
    gaps = [SkillGap(skill=s["id"], display=s["display"], in_taxonomy=True, status="missing", importance=0.5,
                     priority="Low", required_level=2, current_level=0, gap=2) for s in taxonomy()["skills"]]
    roadmap = build_roadmap(gaps, UserProfile(source=SourceInfo(format="manual")))
    assert len(roadmap.milestones) == MAX_MILESTONES and "omitted" in roadmap.note
    assert_prerequisites_first(roadmap)


def test_nothing_to_learn():
    r = gap_for(["Python"], ["python"])
    assert r.roadmap.milestones == [] or all(m.kind == "weak" for m in r.roadmap.milestones)
